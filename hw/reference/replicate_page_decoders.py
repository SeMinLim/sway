#!/usr/bin/env python3
"""Local copies of headHidden high-address page decoders only.

The mapped input is preserved only after both independent transformation
audits pass. Final replacement is atomic and adds no coefficient stage.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import shutil

import check_address_replicas as address_auditor
from check_page_decoder_copies import FROZEN_ADDRESS_AUDITOR, audit as audit_decoder_copies
from merge_reset_controls import atomic_json

ENGINE = 'main_core_headHidden_engine'
ADDRESS_PREFIX = ENGINE + '_addressReplica_'
DECODER_PREFIX = ENGINE + '_pageDecodeReplica_'
TYPES = {'LUT4', 'PFUMX', 'L6MUX21'}

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def plan(module):
    cells, nets = module['cells'], module['netnames']
    drivers, users = defaultdict(list), defaultdict(list)
    for name, cell in cells.items():
        for port, direction in cell['port_directions'].items():
            for index, wire in enumerate(cell['connections'][port]):
                if type(wire) is int:
                    (drivers if direction == 'output' else users)[wire].append((name, port, index))
    address = nets[ENGINE + '_addressR']['bits']
    if len(address) != 13 or len(set(address)) != 13:
        raise RuntimeError('Expected thirteen canonical address bits')
    roots = dict(zip(address, range(13)))
    originals = []
    for wire in address:
        if len(drivers[wire]) != 1 or drivers[wire][0][1:] != ('Q', 0):
            raise RuntimeError('Canonical address must be unique FF Q')
        name = drivers[wire][0][0]
        if cells[name]['type'] != 'TRELLIS_FF':
            raise RuntimeError('Canonical address must be a register')
        originals.append(name)
    for name, cell in cells.items():
        if name.startswith(DECODER_PREFIX):
            raise RuntimeError('Decoder copying cannot be applied twice')
        if name.startswith(ADDRESS_PREFIX):
            match = re.fullmatch(re.escape(ADDRESS_PREFIX) + r'([1-7])_([0-7])', name)
            if not match:
                raise RuntimeError('Input must contain only the reviewed low-address copies')
            bit = int(match.group(2))
            original = cells[originals[bit]]
            if cell['parameters'] != original['parameters'] or cell['type'] != 'TRELLIS_FF':
                raise RuntimeError('Existing address copy differs from canonical FF')
            if {p:v for p,v in cell['connections'].items() if p != 'Q'} != {p:v for p,v in original['connections'].items() if p != 'Q'}:
                raise RuntimeError('Existing address copy has different next-state inputs')
            roots[cell['connections']['Q'][0]] = bit
    if len(roots) != 69:
        raise RuntimeError('Expected canonical13 plus56 reviewed low-address copies')
    owners, pending = {}, set()
    def own(wire, group):
        if wire in roots or wire in {'0','1'}:
            return
        if type(wire) is not int or len(drivers[wire]) != 1:
            raise RuntimeError('Undefined coefficient wire')
        name, _, _ = drivers[wire][0]
        if name in pending:
            raise RuntimeError('Coefficient cycle')
        if name in owners:
            return
        cell = cells[name]
        if cell['type'] not in TYPES:
            raise RuntimeError('Non-combinational coefficient intermediate')
        owners[name] = group
        pending.add(name)
        for port, direction in cell['port_directions'].items():
            if direction == 'input':
                for source in cell['connections'][port]:
                    own(source, group)
        pending.remove(name)
    operand = nets[ENGINE + '_operandQ_D_IN']['bits']
    if len(operand) != 17:
        raise RuntimeError('Expected unchanged single-lane operand tuple')
    for group, wire in enumerate(operand[1:9]):
        own(wire, group)
    @lru_cache(None)
    def support(wire):
        if wire in {'0','1'}:
            return frozenset()
        if wire in roots:
            return frozenset([roots[wire]])
        name, _, _ = drivers[wire][0]
        if name not in owners:
            raise RuntimeError('Foreign coefficient support')
        return frozenset().union(*(support(source)
            for port, direction in cells[name]['port_directions'].items() if direction == 'input'
            for source in cells[name]['connections'][port]))
    pure = set()
    for name in owners:
        dependencies = frozenset().union(*(support(wire)
            for port, direction in cells[name]['port_directions'].items() if direction == 'output'
            for wire in cells[name]['connections'][port]))
        if dependencies and dependencies.issubset(range(8,13)):
            pure.add(name)
    if len(pure) != 50 or any(owners[name] != 0 for name in pure):
        raise RuntimeError('Pure high-address decoder boundary differs from reviewed input')
    constants = {drivers[source][0][0] for name in pure
                 for port, direction in cells[name]['port_directions'].items() if direction == 'input'
                 for source in cells[name]['connections'][port]
                 if type(source) is int and source not in roots and not support(source)}
    for name in constants:
        cell = cells[name]
        if (cell['type'] != 'LUT4' or cell['parameters'] != {'INIT': '0' * 16}
                or set(cell['connections']) != set('ABCDZ')
                or any(cell['connections'][pin] != ['0'] for pin in 'ABCD')):
            raise RuntimeError('Unreviewed constant decoder branch')
    if len(constants) != 25:
        raise RuntimeError('Expected twenty-five zero-LUT decoder leaves')
    required = {}
    for group in range(1,8):
        needed = set()
        def include(name):
            if name in needed:
                return
            needed.add(name)
            for port, direction in cells[name]['port_directions'].items():
                if direction == 'input':
                    for source in cells[name]['connections'][port]:
                        if source in drivers and drivers[source][0][0] in pure | constants:
                            include(drivers[source][0][0])
        for name, owner in owners.items():
            if owner != group or name in pure:
                continue
            for port, direction in cells[name]['port_directions'].items():
                if direction == 'input':
                    for source in cells[name]['connections'][port]:
                        if source in drivers and drivers[source][0][0] in pure:
                            include(drivers[source][0][0])
        required[group] = needed
    return drivers, address, originals, owners, pure, constants, required

def transform(before):
    result = deepcopy(before)
    module = result['modules']['mkTop']
    cells, nets = module['cells'], module['netnames']
    drivers, address, originals, owners, pure, constants, required = plan(module)
    wire_ids = {wire for cell in cells.values() for values in cell['connections'].values() for wire in values if type(wire) is int}
    wire_ids.update(wire for net in nets.values() for wire in net['bits'] if type(wire) is int)
    wire_ids.update(wire for port in module['ports'].values() for wire in port['bits'] if type(wire) is int)
    next_wire = max(wire_ids) + 1
    additions, rewires = [], []
    aliases, decoder_wires, clone_names = {}, {}, {}
    def allocate_net(name):
        nonlocal next_wire
        if name in nets:
            raise RuntimeError('New net name collision: ' + name)
        wire = next_wire
        next_wire += 1
        nets[name] = {'hide_name':0, 'bits':[wire], 'attributes':{}}
        return wire
    for group, needed in sorted(required.items()):
        high_bits = sorted({address.index(source)
            for name in needed for port, direction in cells[name]['port_directions'].items() if direction == 'input'
            for source in cells[name]['connections'][port] if source in address})
        if high_bits != list(range(8,13)):
            raise RuntimeError('Expected all five high-address inputs per decoder group')
        for bit in high_bits:
            name = ADDRESS_PREFIX + str(group) + '_' + str(bit)
            if name in cells:
                raise RuntimeError('New FF name collision')
            cell = deepcopy(cells[originals[bit]])
            wire = allocate_net(name)
            cell['connections']['Q'] = [wire]
            cells[name] = cell
            aliases[group,address[bit]] = wire
            additions.append({'name':name,'source':originals[bit],'group':group,'kind':'address_ff','address_bit':bit,'new_output_wires':{'Q':wire}})
    indices = {name:str(index) for index,name in enumerate(sorted(pure))}
    indices.update({name:'zero_' + str(index) for index,name in enumerate(sorted(constants))})
    for group, needed in sorted(required.items()):
        for original in sorted(needed):
            name = DECODER_PREFIX + str(group) + '_' + str(indices[original])
            if name in cells:
                raise RuntimeError('New decoder name collision')
            cell = deepcopy(cells[original])
            outputs = {}
            for port, direction in cell['port_directions'].items():
                if direction == 'output':
                    if len(cell['connections'][port]) != 1:
                        raise RuntimeError('Only scalar primitive outputs are allowed')
                    old = cell['connections'][port][0]
                    wire = allocate_net(name + '_' + port)
                    cell['connections'][port] = [wire]
                    decoder_wires[group,old] = wire
                    outputs[port] = wire
            cells[name] = cell
            clone_names[group,original] = name
            additions.append({'name':name,'source':original,'group':group,'kind':'constant_zero_leaf' if original in constants else 'pure_high_decoder','new_output_wires':outputs})
    for (group, original), name in clone_names.items():
        cell = cells[name]
        for port, direction in cell['port_directions'].items():
            if direction == 'input':
                cell['connections'][port] = [aliases.get((group,source), decoder_wires.get((group,source), source))
                                             for source in cell['connections'][port]]
    for name, group in owners.items():
        if not group or name in pure:
            continue
        cell = cells[name]
        for port, direction in cell['port_directions'].items():
            if direction != 'input':
                continue
            for index, source in enumerate(cell['connections'][port]):
                new = decoder_wires.get((group,source))
                if new is not None:
                    cell['connections'][port][index] = new
                    rewires.append({'cell':name,'port':port,'index':index,'group':group,'before':source,'after':new})
    counts = Counter(cells[item['name']]['type'] for item in additions)
    if counts != {'TRELLIS_FF':35,'LUT4':342,'PFUMX':171}:
        raise RuntimeError('Draft additions differ from reviewed bound: ' + repr(counts))
    return result, {'status':'transformed_independent_audit_required','added_cell_types':dict(sorted(counts.items())),
        'added_cells':len(additions),'added_nets':next_wire - min(value for item in additions for value in item['new_output_wires'].values()),
        'rewired_input_pins':len(rewires),'decoder_groups':{str(g):len(ns - constants) for g,ns in sorted(required.items())},
        'constant_leaf_groups':{str(g):len(ns & constants) for g,ns in sorted(required.items())},
        'additions':additions,'rewires':rewires,'extra_coefficient_pipeline_cycles':0,
        'boundary':'Only pure high-address decode cells, their proven-zero LUT leaves and exact same-cycle high-address FF copies; existing truth-plane cells untouched'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('netlist', type=Path)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--rtl', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if len({args.netlist.resolve(), args.before.resolve(), args.rtl.resolve(), args.report.resolve()}) != 4:
        raise RuntimeError('Netlist, preserved input, RTL and report must be distinct')
    original_sha, rtl_sha = sha256(args.netlist), sha256(args.rtl)
    address_checker = Path(__file__).with_name('check_address_replicas.py')
    decoder_checker = Path(__file__).with_name('check_page_decoder_copies.py')
    if sha256(address_checker) != FROZEN_ADDRESS_AUDITOR:
        raise RuntimeError('Address auditor differs from the reviewed frozen source')
    before = json.loads(args.netlist.read_text())
    # plan() rejects any pre-existing decoder copy before any files are written.
    # An ordinary rebuild supplies a freshly mapped and audited 56-copy input.
    after, report = transform(before)
    proof = audit_decoder_copies(before, after, args.rtl.read_text(), address_auditor)
    if proof.get('status') != 'pass':
        raise RuntimeError('Independent decoder-copy prewrite audit failed')
    if sha256(args.netlist) != original_sha or sha256(args.rtl) != rtl_sha:
        raise RuntimeError('Decoder-copy inputs changed during the audit')
    args.before.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # Safe for repeated fresh builds: overwrite only with the input just audited.
    shutil.copyfile(args.netlist, args.before)
    if sha256(args.before) != original_sha:
        raise RuntimeError('Preserved pre-decoder netlist differs')
    atomic_json(args.netlist, after)
    report.update({'input': str(args.before), 'input_sha256': original_sha,
                   'preserved_input': args.before.name, 'output': str(args.netlist),
                   'output_sha256': sha256(args.netlist), 'rtl_sha256': rtl_sha,
                   'transform_sha256': sha256(Path(__file__)),
                   'auditor_sha256': sha256(decoder_checker),
                   'address_auditor_sha256': sha256(address_checker),
                   'prewrite_audit': proof})
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('SWAY_PAGE_DECODERS_TRANSFORMED', report['added_cell_types'],
          'rewired_pins', report['rewired_input_pins'])


if __name__ == '__main__':
    main()
