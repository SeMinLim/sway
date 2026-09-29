#!/usr/bin/env python3
"""Replicate only the two gateDelayQ combinational RAM write-enable drivers.

This post-synthesis placement aid changes no state, clocks, data or addresses.
Requires the reviewed Yosys 0.67+24 ECP5 models. The independent audit runs
before any candidate is written; check_wre.py also supplies a standalone audit.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
sys.dont_write_bytecode = True
import check_wre as checker


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def transform(document, group_size=8):
    if type(group_size) is not int or not 1 <= group_size <= 8:
        raise RuntimeError('group_size must be an integer in 1..8')
    module = document['modules']['mkTop']
    cells = module['cells']
    expected_rams = {f'main_core_block{block}_gateDelayQ.arr.0.{index}'
                     for block in range(2) for index in range(80)}
    prefixes = tuple(f'main_core_block{block}_{scan}gateDelayQ.arr.'
                     for block in range(2) for scan in ('', 'scan_'))
    all_gate_rams = {name for name, cell in cells.items()
                    if name.startswith(prefixes) and cell['type'] == 'TRELLIS_DPR16X4'}
    if all_gate_rams != expected_rams:
        raise RuntimeError('Unexpected complete gateDelayQ RAM inventory')
    drivers = {}
    bits = set()
    for cell_name, cell in cells.items():
        for port, connection in cell['connections'].items():
            for bit in connection:
                if type(bit) is int:
                    bits.add(bit)
                    if cell['port_directions'][port] == 'output':
                        drivers.setdefault(bit, []).append((cell_name, port))
    for section in ('ports', 'netnames'):
        for item in module[section].values():
            bits.update(bit for bit in item['bits'] if type(bit) is int)
    next_bit = max(bits) + 1
    plans = []
    for block in range(2):
        stem = f'main_core_block{block}_gateDelayQ.arr.0.'
        names = [stem + str(index) for index in range(80)]
        actual = {name for name, cell in cells.items()
                  if name.startswith(stem) and cell['type'] == 'TRELLIS_DPR16X4'}
        if actual != set(names):
            raise RuntimeError(f'Unexpected RAM instance set for block {block}')
        if any(cells[name]['type'] != 'TRELLIS_DPR16X4' for name in names):
            raise RuntimeError('Expected complete TRELLIS_DPR16X4 cells')
        enables = {tuple(cells[name]['connections']['WRE']) for name in names}
        if len(enables) != 1:
            raise RuntimeError('RAM group must share one scalar WRE')
        old_enable, = enables.pop()
        if type(old_enable) is not int or len(drivers.get(old_enable, [])) != 1:
            raise RuntimeError('RAM WRE must have exactly one concrete driver')
        driver_name, driver_port = drivers[old_enable][0]
        source = cells[driver_name]
        if (source['type'] != 'LUT4' or driver_port != 'Z'
                or set(source['connections']) != set('ABCDZ')
                or any(len(v) != 1 for v in source['connections'].values())
                or source['port_directions'] != dict.fromkeys('ABCD', 'input') | {'Z': 'output'}):
            raise RuntimeError('WRE driver must be one ordinary scalar LUT4')
        if set(source['parameters']) != {'INIT'} or len(source['parameters']['INIT']) != 16:
            raise RuntimeError('WRE LUT must have a 16-bit INIT')
        if set(source['parameters']['INIT']) - set('01'):
            raise RuntimeError('WRE LUT INIT must be fully defined')
        for offset in range(0, 80, group_size):
            name = f'sway_wre_copy_block{block}_{offset // group_size}'
            alias = name + '$WRE'
            if name in cells or alias in module['netnames']:
                raise RuntimeError('WRE copy name already exists')
            plans.append((name, alias, next_bit, source, names[offset:offset + group_size]))
            next_bit += 1
    result = dict(document)
    result['modules'] = dict(document['modules'])
    output = dict(module)
    result['modules']['mkTop'] = output
    output['cells'] = dict(cells)
    output['netnames'] = dict(module['netnames'])
    for name, alias, bit, source, targets in plans:
        copy = deepcopy(source)
        copy['connections']['Z'] = [bit]
        output['cells'][name] = copy
        output['netnames'][alias] = {'hide_name': 0, 'bits': [bit], 'attributes': {}}
        for target in targets:
            ram = deepcopy(cells[target])
            ram['connections']['WRE'] = [bit]
            output['cells'][target] = ram
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'output', 'cells-sim', 'report'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--group-size', type=int, default=8)
    args = parser.parse_args()
    inputs = {'before': args.before, 'cells_sim': args.cells_sim,
              'common_sim': args.cells_sim.with_name('common_sim.vh')}
    destinations = {args.output.resolve(), args.report.resolve()}
    checker.require(len(destinations) == 2 and not destinations.intersection(p.resolve() for p in inputs.values()),
                    'Output/report must differ from each other and all inputs')
    hashes = {name: sha(path) for name, path in inputs.items()}
    checker.source_model(args.cells_sim)
    previous_output_sha = sha(args.output) if args.output.exists() else None
    checker.require(previous_output_sha in (None, hashes['before']),
                    'Existing output does not match preserved input')
    document = checker.load(args.before)
    result = transform(document, args.group_size)
    # Read the immutable reference again: the transform cannot hide an edit by
    # modifying an object shared between its source and destination documents.
    del document
    prewrite = checker.audit(checker.load(args.before), result, args.group_size, cells_sim=args.cells_sim)
    checker.require(all(sha(path) == hashes[name] for name, path in inputs.items()),
                    'Input changed before writing candidate')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=args.output.parent,
                                         prefix=args.output.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            expected_hash = hashlib.sha256()
            for chunk in json.JSONEncoder(separators=(',', ':')).iterencode(result):
                data = chunk.encode('utf-8')
                checker.require(stream.write(data) == len(data), 'Short netlist write')
                expected_hash.update(data)
            checker.require(stream.write(b'\n') == 1, 'Short netlist newline write')
            expected_hash.update(b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        checker.require(sha(temporary) == expected_hash.hexdigest(), 'Written netlist hash mismatch')
        checker.require(all(sha(path) == hashes[name] for name, path in inputs.items()),
                        'Input changed while writing candidate')
        checker.require((sha(args.output) if args.output.exists() else None) == previous_output_sha,
                        'Output changed before atomic replacement')
        os.replace(temporary, args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    report = {'status': 'pass', 'input_sha256': hashes['before'], 'output_sha256': sha(args.output),
              'preserved_input': args.before.name, 'transform_sha256': sha(Path(__file__)),
              'auditor_sha256': sha(Path(checker.__file__)), 'cells_sim_sha256': hashes['cells_sim'],
              'common_sim_sha256': hashes['common_sim'], 'prewrite_audit': prewrite,
              'group_size': args.group_size, 'inputs_unchanged': True}
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'pass', 'input_sha256': report['input_sha256'],
                      'output_sha256': report['output_sha256'], 'copies': prewrite['copied_LUT4_cells'],
                      'rewired_ram_WRE_pins': 160, 'timing': 'not evaluated'}))


if __name__ == '__main__':
    main()
