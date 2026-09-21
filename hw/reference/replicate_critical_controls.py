#!/usr/bin/env python3
"""Replicate only the three late LUT4 controls on B1's routed critical path."""

import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path

from replicate_controls import natural


DRIVERS = (
    'core_block1_convAddressQ.empty_reg_LUT4_C_Z_LUT4_Z',
    'core_block1_convSelectedQ.D_OUT_TRELLIS_FF_Q_3_LSR_LUT4_Z_1',
    'core_block1_convSelectedQ.D_OUT_TRELLIS_FF_Q_7_LSR_LUT4_Z',
)
CAP = 8


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    paths = [args.input.resolve(), args.output.resolve(), args.manifest.resolve()]
    require(len(set(paths)) == len(paths), 'input/output/manifest must be distinct')
    args.output.unlink(missing_ok=True)
    args.manifest.unlink(missing_ok=True)
    doc = json.loads(args.input.read_text())
    module = doc['modules'][args.top]
    cells = module['cells']
    top_bits = {bit for port in module['ports'].values() for bit in port['bits']}
    all_bits = {bit for cell in cells.values() for bits in cell['connections'].values()
                for bit in bits if type(bit) is int}
    all_bits.update(bit for net in module['netnames'].values() for bit in net['bits']
                    if type(bit) is int)
    all_bits.update(bit for bit in top_bits if type(bit) is int)
    next_bit = max(all_bits) + 1
    bit_names = collections.defaultdict(list)
    for name, net in module['netnames'].items():
        for index, bit in enumerate(net['bits']):
            bit_names[bit].append((bool(net.get('hide_name')), name, index))

    def users(bit):
        return [(name, port, index) for name, cell in cells.items()
                for port, bits in cell['connections'].items()
                if cell['port_directions'][port] == 'input'
                for index, value in enumerate(bits) if value == bit]

    def destination_order(endpoint):
        name, port, index = endpoint
        cell = cells[name]
        if cell['type'] == 'TRELLIS_FF' and 'Q' in cell['connections']:
            names = bit_names.get(cell['connections']['Q'][0], [])
            if names:
                _, logical_name, bit_index = min(names, key=lambda value:
                                                (value[0], len(value[1]), value[1]))
                return natural((logical_name, bit_index, port, name))
        return natural(endpoint)

    selected = {}
    for name in DRIVERS:
        require(name in cells, f'missing selected driver: {name}')
        cell = cells[name]
        require(cell['type'] == 'LUT4', f'selected driver must be LUT4: {name}')
        require(set(cell['connections']) == {'A', 'B', 'C', 'D', 'Z'} and
                all(len(bits) == 1 for bits in cell['connections'].values()),
                f'unsupported LUT4 ports: {name}')
        require(cell['port_directions'] == {'A': 'input', 'B': 'input', 'C': 'input',
                                           'D': 'input', 'Z': 'output'},
                f'unsupported LUT4 directions: {name}')
        init = cell['parameters'].get('INIT', '')
        require(len(init) == 16 and set(init) <= {'0', '1'}, f'invalid LUT4 INIT: {name}')
        bit = cell['connections']['Z'][0]
        require(type(bit) is int and bit not in top_bits, f'exposed control net: {name}')
        endpoints = users(bit)
        require(not any(port.startswith('CLK') or port == 'WCK'
                        for _, port, _ in endpoints), f'control net drives clock: {name}')
        selected[name] = {'net_bit': bit, 'before_fanout': len(endpoints)}

    origins = {}
    aliases = {}
    splits = []
    # Clone downstream controls first. Their additional inputs are then included
    # when the selected upstream drivers are partitioned; no other cone is cloned.
    for name in reversed(DRIVERS):
        bit = selected[name]['net_bit']
        endpoints = sorted(users(bit), key=destination_order)
        groups = [endpoints[start:start + CAP] for start in range(0, len(endpoints), CAP)]
        copies = []
        for group in groups[1:]:
            clone_name = f'{name}$critical_fanout${len(origins)}'
            require(clone_name not in cells and clone_name + '.Z' not in module['netnames'],
                    'clone name collision')
            clone = copy.deepcopy(cells[name])
            clone['attributes']['keep'] = '00000000000000000000000000000001'
            clone['connections']['Z'] = [next_bit]
            cells[clone_name] = clone
            module['netnames'][clone_name + '.Z'] = {
                'hide_name': 1, 'bits': [next_bit], 'attributes': {}}
            origins[clone_name] = name
            aliases[next_bit] = bit
            copies.append(next_bit)
            for sink, port, index in group:
                require(cells[sink]['connections'][port][index] == bit, 'stale endpoint')
                cells[sink]['connections'][port][index] = next_bit
            next_bit += 1
        splits.append({'driver': name, 'before_partition_fanout': len(endpoints),
                       'copies': copies})

    for split in splits:
        name = split['driver']
        outputs = [selected[name]['net_bit'], *split['copies']]
        final_fanout = {str(bit): len(users(bit)) for bit in outputs}
        require(all(count <= CAP for count in final_fanout.values()),
                f'selected control exceeded cap: {name}')
        selected[name]['final_output_fanout'] = final_fanout

    result = {'schema': 1, 'scope': 'B1 three selected late critical-path LUT4 controls',
              'input_sha256': sha(args.input), 'top': args.top, 'cap': CAP,
              'selected_drivers': selected, 'processing_order': list(reversed(DRIVERS)),
              'splits': splits, 'clone_origins': origins,
              'net_aliases': {str(bit): source for bit, source in aliases.items()},
              'cloned_cell_types': {'LUT4': len(origins)},
              'partition_heuristic': 'logical destination FF Q net name and bit index, then cell name',
              'no_recursive_cloning': True, 'no_sequential_cells_cloned': True,
              'no_timing_constraints_changed': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, separators=(',', ':')) + '\n')
    result['output_sha256'] = sha(args.output)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(result, indent=2) + '\n')
    print(f'SWAY_CRITICAL_CONTROL_REPLICATION generated={len(origins)} cap={CAP} selected=3')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--top', default='mkTop')
    run(parser.parse_args())
