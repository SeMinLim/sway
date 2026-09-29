#!/usr/bin/env python3
"""Reproducible focused negative tests; touches only in-memory copies and report."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import check_private_rom as checker


def run(before, after, weights, *, rtl, cells_sim):
    positive = checker.audit(before, after, weights, rtl=rtl, cells_sim=cells_sim)
    assert positive['status'] == 'pass'
    print('PASS Unmodified full proof', flush=True)
    old, new = before['modules']['mkTop'], after['modules']['mkTop']
    drivers, users = checker.connectivity(old)
    groups, roots, outputs = checker.root_groups(old, drivers)
    fifo = checker.fifo_interface(old, roots, outputs, drivers)
    additions = sorted(set(new['cells']) - set(old['cells']))
    results = []
    oracle_cases = 0
    for width in (1, 2, 4, 8, 16):
        for bit in range(width.bit_length()):
            expected = sum(1 << state for state in range(width) if (state >> bit) & 1)
            assert checker.pattern(bit, width) == expected, (width, bit)
            oracle_cases += 1
    results.append({'test': 'Pattern oracle widths 1,2,4,8,16', 'status': 'pass', 'cases': oracle_cases})
    print('PASS Pattern oracle ' + str(oracle_cases) + ' cases', flush=True)
    def rejected(label, expected):
        try:
            checker.audit(before, after, weights, rtl=rtl, cells_sim=cells_sim)
        except RuntimeError as error:
            if expected not in str(error):
                raise AssertionError((label, str(error))) from error
            results.append({'test': label, 'status': 'pass', 'rejection': str(error)})
            print('PASS ' + label, flush=True)
        else:
            raise AssertionError('Mutation accepted: ' + label)
    def mutate(label, name, change, expected):
        original = new['cells'][name]
        new['cells'][name] = deepcopy(original)
        try:
            change(new['cells'][name]); rejected(label, expected)
        finally:
            new['cells'][name] = original
    ff = drivers[fifo['q1'][0]][0][0]
    mutate('FF clock immutable', ff, lambda c: c['connections'].__setitem__('CLK', [999]), 'Original retained cell')
    control = drivers[min(fifo['equivalents']['di'])][0][0]
    mutate('Control producer immutable', control, lambda c: c['parameters'].__setitem__('INIT', '0' * 16), 'Original retained cell')
    carry = next(n for n, c in old['cells'].items() if c['type'] == 'CCU2C' and n in new['cells'])
    mutate('Carry immutable', carry, lambda c: c['parameters'].__setitem__('INIT0', format(int(c['parameters'].get('INIT0', '0'), 2) ^ 1, '016b')), 'Original retained cell')
    lut = next(n for n in additions if new['cells'][n]['type'] == 'LUT4'
               and any(w in roots for values in new['cells'][n]['connections'].values() for w in values))
    pin = next(p for p, values in new['cells'][lut]['connections'].items() if values[0] in roots)
    group, bit = roots[new['cells'][lut]['connections'][pin][0]]
    mutate('Address group ownership', lut, lambda c: c['connections'].__setitem__(pin, [groups[(group + 1) % 8][bit]]), 'another output group')
    mutate('Exact new attributes', lut, lambda c: c['attributes'].__setitem__('keep', 1), 'attributes differ')
    pf = next(n for n in additions if new['cells'][n]['type'] == 'PFUMX')
    mutate('Dedicated leaf sharing rejected', pf, lambda c: c['connections'].__setitem__('ALUT', c['connections']['BLUT'][:]), 'private ROM or is unused')
    state, pin = next((n, p) for n in additions for p, v in new['cells'][n]['connections'].items() if p != 'Z' and v[0] in fifo['q0'])
    bit = fifo['q0'].index(new['cells'][state]['connections'][pin][0])
    mutate('Stored state bit ownership', state, lambda c: c['connections'].__setitem__(pin, [fifo['q0'][(bit + 1) % 8]]), 'another bit state')
    mutate('ROM truth table', lut, lambda c: c['parameters'].__setitem__('INIT', format(int(c['parameters']['INIT'], 2) ^ 1, '016b')), 'ROM differs from frozen')
    hold, data = min(fifo['equivalents']['h']), min(fifo['equivalents']['di'])
    gate, pin = next((n, p) for n in additions for p, values in new['cells'][n]['connections'].items() if p != 'Z' and values == [hold])
    mutate('FIFO cut truth', gate, lambda c: c['connections'].__setitem__(pin, [data]), 'New data0 DI differs')
    alias = next(n for n in old['netnames'] if n not in new['netnames'])
    new['netnames'][alias] = old['netnames'][alias]
    try:
        rejected('Exact deleted alias set', 'Deleted aliases differ')
    finally:
        del new['netnames'][alias]
    library = next(n for n in after['modules'] if n != 'mkTop')
    original = after['modules'][library]
    after['modules'][library] = deepcopy(original); after['modules'][library]['unexpected'] = True
    try:
        rejected('Primitive library unchanged', 'Non-mkTop library module changed')
    finally:
        after['modules'][library] = original
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'weights', 'rtl', 'cells-sim', 'report'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    inputs = {name: getattr(args, name) for name in ('before', 'after', 'weights', 'rtl', 'cells_sim')}
    hashes = {name: checker.sha(path) for name, path in inputs.items()}
    results = run(json.loads(args.before.read_text()), json.loads(args.after.read_text()), args.weights.read_bytes(), rtl=args.rtl, cells_sim=args.cells_sim)
    checker.require(all(checker.sha(path) == hashes[name] for name, path in inputs.items()), 'Test inputs changed')
    report = {'status': 'pass', 'negative_tests': sum('rejection' in item for item in results), 'tests_total': len(results), 'tests': results,
              'input_sha256': hashes, 'checker_sha256': checker.sha(Path(checker.__file__)),
              'runner_sha256': checker.sha(Path(__file__)), 'netlists_written': False}
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'pass', 'negative_tests': sum('rejection' in item for item in results), 'tests_total': len(results)}))


if __name__ == '__main__':
    main()
