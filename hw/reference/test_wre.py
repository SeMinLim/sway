#!/usr/bin/env python3
"""Reject realistic state, function, ownership and provenance corruptions."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import sys
sys.dont_write_bytecode = True
import check_wre as checker


def run(before, after, cells_sim):
    original = before['modules']['mkTop']
    module = after['modules']['mkTop']
    cells = module['cells']
    copies = sorted(set(cells) - set(original['cells']))
    first, second = copies[:2]
    wre = cells[first]['connections']['Z'][0]
    ram = 'main_core_block0_gateDelayQ.arr.0.0'
    ff = next(name for name, c in cells.items() if c['type'] == 'TRELLIS_FF')
    dsp = next(name for name, c in cells.items() if c['type'] == 'MULT18X18D')
    source = next(name for name, c in original['cells'].items()
                  if c['type'] == 'LUT4' and c['connections']['Z'] == original['cells'][ram]['connections']['WRE'])
    results = []

    def rejected(label, expected, operation=None):
        try:
            if operation is None:
                checker.audit(before, after, cells_sim=cells_sim)
            else:
                operation()
        except RuntimeError as error:
            checker.require(expected in str(error), f'{label}: unexpected rejection: {error}')
            results.append({'test': label, 'status': 'pass', 'rejection': str(error)})
            print('PASS ' + label, flush=True)
        else:
            raise AssertionError('Mutation accepted: ' + label)

    def change_cell(label, name, mutation, expected):
        previous = cells[name]
        cells[name] = deepcopy(previous)
        try:
            mutation(cells[name])
            rejected(label, expected)
        finally:
            cells[name] = previous

    change_cell('Copied LUT truth table', first,
                lambda c: c['parameters'].__setitem__('INIT', format(int(c['parameters']['INIT'], 2) ^ 1, '016b')),
                'Exact clone')
    change_cell('Copied LUT input', first, lambda c: c['connections'].__setitem__('A', ['0']), 'Exact clone')
    change_cell('Copied LUT attributes', first, lambda c: c['attributes'].__setitem__('keep', '1'), 'Exact clone')
    change_cell('Output aliases old signal', first,
                lambda c: c['connections'].__setitem__('Z', original['cells'][ram]['connections']['WRE']), 'Exact clone')
    change_cell('RAM assigned to wrong copy', ram,
                lambda c: c['connections'].__setitem__('WRE', cells[second]['connections']['Z']), 'Assigned WRE')
    for port in ('WCK', 'DI', 'DO', 'RAD', 'WAD'):
        def mutate(c, port=port):
            c['connections'][port][0] = '1' if c['connections'][port][0] == '0' else '0'
        change_cell('RAM ' + port + ' immutable', ram, mutate, 'RAM state/data/clock identity')
    change_cell('RAM initialization immutable', ram,
                lambda c: c['parameters'].__setitem__('INITVAL', '0' * 64), 'RAM state/data/clock identity')
    change_cell('RAM WREMUX immutable', ram,
                lambda c: c['parameters'].__setitem__('WREMUX', 'INV'), 'RAM state/data/clock identity')
    change_cell('RAM WCKMUX immutable', ram,
                lambda c: c['parameters'].__setitem__('WCKMUX', 'INV'), 'RAM state/data/clock identity')
    change_cell('FF clock immutable', ff,
                lambda c: c['connections'].__setitem__('CLK', ['0']), 'Whole JSON reversal')
    change_cell('No extra cloned-WRE FF consumer', ff,
                lambda c: c['connections'].__setitem__('DI', [wre]), 'Whole JSON reversal')
    change_cell('Original LUT immutable', source,
                lambda c: c['parameters'].__setitem__('INIT', '0' * 16), 'Whole JSON reversal')
    change_cell('DSP immutable', dsp,
                lambda c: c['attributes'].__setitem__('unauthorized', True), 'Whole JSON reversal')
    change_cell('Typed JSON identity', ff,
                lambda c: c.__setitem__('hide_name', bool(c['hide_name'])), 'Typed JSON mismatch')
    alias = first + '$WRE'
    previous = module['netnames'][alias]
    module['netnames'][alias] = deepcopy(previous)
    module['netnames'][alias]['bits'] = [wre + 1000]
    try:
        rejected('Copy alias exact', 'Exact new alias')
    finally:
        module['netnames'][alias] = previous
    old_alias = next(iter(original['netnames']))
    previous = module['netnames'].pop(old_alias)
    try:
        rejected('Original alias cannot be deleted', 'Alias addition/deletion set differs')
    finally:
        module['netnames'][old_alias] = previous
    module['netnames']['unauthorized_alias'] = {'hide_name': 0, 'bits': [wre], 'attributes': {}}
    try:
        rejected('No additional aliases', 'Alias addition/deletion set differs')
    finally:
        del module['netnames']['unauthorized_alias']
    cells['unauthorized_cell'] = deepcopy(cells[first])
    try:
        rejected('No additional cells', 'Cell addition/deletion set differs')
    finally:
        del cells['unauthorized_cell']
    library_name = next(name for name in after['modules'] if name != 'mkTop')
    library = after['modules'][library_name]
    after['modules'][library_name] = dict(library, unauthorized=True)
    try:
        rejected('Primitive library unchanged', 'Whole JSON reversal')
    finally:
        after['modules'][library_name] = library
    with tempfile.TemporaryDirectory() as folder:
        copied_model = Path(folder) / 'cells_sim.v'
        copied_model.write_bytes(cells_sim.read_bytes())
        copied_model.with_name('common_sim.vh').write_bytes(cells_sim.with_name('common_sim.vh').read_bytes() + b'\n')
        rejected('Reviewed model hash enforced', 'Unreviewed common_sim.vh', lambda: checker.source_model(copied_model))
        rejected('Programmatic audit pins model', 'Unreviewed common_sim.vh',
                 lambda: checker.audit(before, after, cells_sim=copied_model))
        copied_common = copied_model.with_name('common_sim.vh')
        copied_common.write_bytes(cells_sim.with_name('common_sim.vh').read_bytes())
        original_same = checker.same
        mutated = False
        def change_model_during_audit(left, right, path='document'):
            nonlocal mutated
            if path == 'Whole JSON reversal' and not mutated:
                copied_common.write_bytes(copied_common.read_bytes() + b'\n')
                mutated = True
            return original_same(left, right, path)
        checker.same = change_model_during_audit
        try:
            rejected('Programmatic audit pins model at exit', 'Unreviewed common_sim.vh',
                     lambda: checker.audit(before, after, cells_sim=copied_model))
            checker.require(mutated, 'Mid-audit model mutation was not exercised')
        finally:
            checker.same = original_same
    checker.audit(before, after, cells_sim=cells_sim)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'cells-sim', 'report'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    inputs = {'before': args.before, 'after': args.after, 'cells_sim': args.cells_sim,
              'common_sim': args.cells_sim.with_name('common_sim.vh')}
    hashes = {name: checker.sha(path) for name, path in inputs.items()}
    checker.source_model(args.cells_sim)
    results = run(checker.load(args.before), checker.load(args.after), args.cells_sim)
    checker.require(all(checker.sha(path) == hashes[name] for name, path in inputs.items()), 'Test input changed')
    report = {'status': 'pass', 'negative_tests': len(results), 'tests': results,
              'input_sha256': hashes, 'checker_sha256': checker.sha(Path(checker.__file__)),
              'runner_sha256': checker.sha(Path(__file__)), 'netlists_written': False}
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'pass', 'negative_tests': len(results)}))


if __name__ == '__main__':
    main()
