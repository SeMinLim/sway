#!/usr/bin/env python3
"""Replace private combinational HeadHidden ROM logic with independently audited trees.

Every original FF and outside-observed control/carry cone is retained. The
independent checker proves all coefficient values, FIFO next states and address
startup before the netlist is atomically replaced. Timing is checked separately.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import os
import tempfile
import sys
sys.dont_write_bytecode = True
import check_private_rom as checker
from pathlib import Path

ENGINE = 'main_core_headHidden_engine'
PREFIX = ENGINE + '_privateRom_'
WEIGHTS_SHA256 = '75440fcbdaf2741e978d3e6ab9824c56156665c01aad6c1dd1953af065d226a6'
COMBINATIONAL = {'LUT4', 'PFUMX', 'L6MUX21', 'CCU2C'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vector_cell(cell, inputs, mask):
    def lut(init, values):
        rows = [mask if (init >> i) & 1 else 0 for i in range(1 << len(values))]
        for select in values:
            rows = [(a & (mask ^ select)) | (b & select) for a, b in zip(rows[::2], rows[1::2])]
        return rows[0]
    kind, params = cell['type'], cell['parameters']
    if kind == 'LUT4':
        return {'Z': lut(int(params.get('INIT', '0'), 2), [inputs[p] for p in 'ABCD'])}
    if kind in {'PFUMX', 'L6MUX21'}:
        sel, low, high = ('C0', 'BLUT', 'ALUT') if kind == 'PFUMX' else ('SD', 'D0', 'D1')
        return {'Z': (inputs[sel] & inputs[high]) | ((mask ^ inputs[sel]) & inputs[low])}
    require(kind == 'CCU2C', 'Unsupported combinational primitive: ' + kind)
    carry, result = inputs['CIN'], {}
    for half in range(2):
        init = int(params.get('INIT' + str(half), '0'), 2)
        inject = params.get('INJECT1_' + str(half), 'YES')
        require(inject in {'YES', 'NO'}, 'Unsupported carry injection')
        value = lut(init, [inputs[p + str(half)] for p in 'ABCD'])
        value2 = lut(init & 15, [inputs[p + str(half)] for p in 'AB'])
        result['S' + str(half)] = value ^ (carry if inject == 'NO' else 0)
        carry = ((mask ^ value) & (value2 if inject == 'NO' else 0)) | (value & carry)
    result['COUT'] = carry
    return result


def fifo_boundary(module, drivers, address_roots):
    """Identify controls by complete Boolean truth, never numeric wire IDs."""
    cells, nets = module['cells'], module['netnames']
    q0 = nets[ENGINE + '_operandQ.data0_reg']['bits'][1:9]
    q1 = nets[ENGINE + '_operandQ.data1_reg']['bits'][1:9]
    require(len(q0) == len(q1) == 8 and len(set(q0 + q1)) == 16, 'Expected sixteen FIFO weight FFs')
    q_sources = {}
    for name, cell in cells.items():
        if cell['type'] == 'TRELLIS_FF':
            for wire in cell['connections']['Q']:
                q_sources[wire] = name
    for wire in q0 + q1:
        require(wire in q_sources and drivers[wire] == [(q_sources[wire], 'Q', 0)], 'Non-FF FIFO data root')
    di = [cells[q_sources[q]]['connections']['DI'][0] for q in q0]
    for index, q in enumerate(q1):
        require(cells[q_sources[q]]['connections']['DI'] == [nets[ENGINE + '_operandQ_D_IN']['bits'][index+1]],
                'Second FIFO storage does not directly sample its ROM output')
    dependencies, active = {}, set()
    seen_cells = set()

    def support(wire):
        if wire in ('0', '1'):
            return frozenset()
        if wire in q_sources:
            return frozenset({wire})
        if wire in dependencies:
            return dependencies[wire]
        require(wire not in active and type(wire) is int and len(drivers[wire]) == 1, 'Invalid FIFO dependency')
        active.add(wire)
        name, _, _ = drivers[wire][0]
        cell = cells[name]
        require(cell['type'] in COMBINATIONAL, 'Unreviewed FIFO combinational dependency: ' + name)
        seen_cells.add(name)
        result = frozenset().union(*(support(b) for port, direction in cell['port_directions'].items()
                                    if direction == 'input' for b in cell['connections'][port]))
        active.remove(wire)
        dependencies[wire] = result
        return result

    for wire in di:
        support(wire)
    controls = {label: nets[ENGINE + suffix]['bits'][0] for label, suffix in (
        ('active', '_activeOn'), ('bank', '_bankQ_EMPTY_N'), ('full', '_operandQ_FULL_N'),
        ('empty', '_operandQ_EMPTY_N'), ('deq', '_operandQ_DEQ'))}
    control_roots = sorted(frozenset().union(*(support(wire) for wire in controls.values())))
    require(len(control_roots) <= 16 and not set(control_roots) & (set(q0 + q1) | address_roots),
            'Unexpected FIFO control dependence or too many control state bits')
    count = 1 << len(control_roots)
    mask = (1 << count) - 1
    values = {'0': 0, '1': mask}
    values.update({wire: sum(1 << state for state in range(count) if (state >> index) & 1)
                   for index, wire in enumerate(control_roots)})
    evaluated = {}

    def evaluate(wire):
        if wire in values:
            return values[wire]
        name, port, _ = drivers[wire][0]
        if name not in evaluated:
            cell = cells[name]
            ins = {p: evaluate(bs[0]) for p, bs in cell['connections'].items()
                   if cell['port_directions'][p] == 'input'}
            evaluated[name] = vector_cell(cell, ins, mask)
        return evaluated[name][port]

    active_value, bank, full, empty, deq = [evaluate(controls[key]) for key in ('active', 'bank', 'full', 'empty', 'deq')]
    enq = active_value & bank & full
    expected = [enq & ((mask ^ empty) | (deq & full)), deq & (mask ^ full),
                ((mask ^ deq) & (mask ^ enq)) | ((mask ^ deq) & empty) | ((mask ^ enq) & full)]
    aliases = [[] for _ in range(3)]
    for wire, dep in dependencies.items():
        if dep and dep <= set(control_roots):
            value = evaluate(wire)
            for index, wanted in enumerate(expected):
                if value == wanted:
                    aliases[index].append(wire)
    require(all(aliases), 'Mapped FIFO does not expose all three exact reference controls')
    aliases = [sorted(wires, key=lambda wire: drivers[wire][0][0]) for wires in aliases]
    control_cut = {wire for wires in aliases for wire in wires}
    for name in list(seen_cells):
        cell = cells[name]
        if any(support(wire) - set(control_roots)
               for port, direction in cell['port_directions'].items() if direction == 'output'
               for wire in cell['connections'][port]):
            for port, direction in cell['port_directions'].items():
                if direction == 'input':
                    for wire in cell['connections'][port]:
                        dependency = support(wire)
                        if dependency and dependency <= set(control_roots):
                            control_cut.add(wire)
    control_cut = sorted(control_cut)
    cut_patterns = [evaluate(wire) for wire in control_cut]
    vectors = Counter(tuple((pattern >> state) & 1 for pattern in cut_patterns) for state in range(count))
    control_cells = set()

    def preserve_control(wire):
        if wire in ('0', '1') or wire in q_sources:
            return
        name, _, _ = drivers[wire][0]
        if name in control_cells:
            return
        control_cells.add(name)
        for port, direction in cells[name]['port_directions'].items():
            if direction == 'input':
                for bit in cells[name]['connections'][port]:
                    preserve_control(bit)

    for wire in control_cut:
        preserve_control(wire)
    return {'q0': q0, 'q1': q1, 'di': di, 'controls': [wires[0] for wires in aliases],
            'control_aliases': aliases, 'control_ff_roots': control_roots,
            'control_cut_wires': control_cut,
            'control_vectors': [{'values': list(vector), 'state_count': vectors[vector]} for vector in sorted(vectors)],
            'protected_control_cells': sorted(control_cells),
            'control_assignments': count,
            'equations': ['d0di=E&(~empty|(D&full))', 'd0d1=D&~full',
                          'd0h=(~D&~E)|(~D&empty)|(~E&full)', 'E=activeOn&bankEMPTY&operandFULL']}


def identify(module):
    cells, nets = module['cells'], module['netnames']
    drivers, users = defaultdict(list), defaultdict(list)
    for name, cell in cells.items():
        require(set(cell['port_directions']) == set(cell['connections']), 'Port mismatch: ' + name)
        for port, direction in cell['port_directions'].items():
            require(direction in {'input', 'output'}, 'Unsupported direction: ' + name)
            for index, wire in enumerate(cell['connections'][port]):
                if type(wire) is int:
                    (drivers if direction == 'output' else users)[wire].append((name, port, index))
    canonical = nets[ENGINE + '_addressR']['bits']
    require(len(canonical) == 13 and len(set(canonical)) == 13, 'Expected canonical address13')
    groups = [list(canonical)]
    for group in range(1, 8):
        bits = []
        for bit in range(13):
            name = ENGINE + '_addressReplica_' + str(group) + '_' + str(bit)
            clone = cells[name]
            require(len(drivers[canonical[bit]]) == 1, 'Address driver must be unique')
            original = cells[drivers[canonical[bit]][0][0]]
            require(clone['type'] == original['type'] == 'TRELLIS_FF', 'Expected address FFs')
            require(clone['parameters'] == original['parameters'], 'Replica parameters differ')
            require({p: v for p, v in clone['connections'].items() if p != 'Q'} ==
                    {p: v for p, v in original['connections'].items() if p != 'Q'},
                    'Replica next-state inputs differ')
            require(len(clone['connections']['Q']) == 1, 'Replica Q must be scalar')
            bits.append(clone['connections']['Q'][0])
        groups.append(bits)
    roots = {wire for group in groups for wire in group}
    require(len(roots) == 104, 'Expected eight distinct address groups')
    weight_outputs = nets[ENGINE + '_operandQ_D_IN']['bits'][1:9]
    require(len(weight_outputs) == 8 and len(set(weight_outputs)) == 8, 'Expected eight distinct weight outputs')
    fifo = fifo_boundary(module, drivers, roots)
    roots.update(fifo['q0'] + fifo['q1'])
    roots.update(fifo['control_cut_wires'])
    outputs = weight_outputs + fifo['di']
    require(len(set(outputs)) == 16, 'Weight and FIFO DI boundaries must be distinct')
    output_set = set(outputs)
    cone, pending = set(), set()

    def visit(wire):
        if wire in roots or wire in ('0', '1'):
            return
        require(type(wire) is int and len(drivers[wire]) == 1, 'Unresolved ROM input')
        name, _, _ = drivers[wire][0]
        require(name not in pending, 'ROM cycle')
        if name in cone:
            return
        require(cells[name]['type'] in COMBINATIONAL, 'Foreign ROM dependency: ' + name)
        cone.add(name)
        pending.add(name)
        for port, direction in cells[name]['port_directions'].items():
            if direction == 'input':
                for source in cells[name]['connections'][port]:
                    visit(source)
        pending.remove(name)

    for wire in outputs:
        visit(wire)
    original_cone = set(cone)
    # Page copying intentionally leaves obsolete original decoder nodes. Include
    # only downstream combinational branches with no state or port observation.
    port_wires = {wire for port in module['ports'].values() for wire in port['bits'] if type(wire) is int}
    dead_cache, dead_pending = {}, set()

    def dead(name):
        if name in dead_cache:
            return dead_cache[name]
        if cells[name]['type'] not in {'LUT4', 'PFUMX', 'L6MUX21'}:
            return False
        require(name not in dead_pending, 'Combinational cycle in dead-branch inspection')
        dead_pending.add(name)
        result = all(wire not in port_wires and wire not in output_set and wire not in roots
                     and all(dead(user) for user, _, _ in users[wire])
                     for port, direction in cells[name]['port_directions'].items() if direction == 'output'
                     for wire in cells[name]['connections'][port])
        dead_pending.remove(name)
        dead_cache[name] = result
        return result

    queue = list(cone)
    while queue:
        name = queue.pop()
        for port, direction in cells[name]['port_directions'].items():
            if direction == 'output':
                for wire in cells[name]['connections'][port]:
                    for user, _, _ in users[wire]:
                        if user not in cone and dead(user):
                            cone.add(user)
                            queue.append(user)
    protected = set()

    def protect(name):
        if name in protected:
            return
        require(name in cone, 'Protected upstream cell escapes ROM cone')
        protected.add(name)
        for port, direction in cells[name]['port_directions'].items():
            if direction == 'input':
                for wire in cells[name]['connections'][port]:
                    if wire not in roots and wire not in ('0', '1'):
                        protect(drivers[wire][0][0])

    outside_pins = []
    for name in fifo['protected_control_cells']:
        if name in cone:
            protect(name)
    for name in cone:
        cell = cells[name]
        if cell['type'] == 'CCU2C':
            protect(name)
        for port, direction in cell['port_directions'].items():
            if direction == 'output':
                for wire in cell['connections'][port]:
                    if wire not in output_set:
                        foreign = [(n, p, i) for n, p, i in users[wire] if n not in cone]
                        if foreign or wire in port_wires:
                            protect(name)
                            outside_pins.extend((wire, n, p, i) for n, p, i in foreign)
    removed = cone - protected
    require(all(drivers[wire][0][0] in removed for wire in outputs),
            'A boundary driver has an independently protected observation')
    dead = {wire for name in removed for port, direction in cells[name]['port_directions'].items()
            if direction == 'output' for wire in cells[name]['connections'][port]} - output_set
    require(not dead & port_wires, 'Private ROM signal reaches a top-level port')
    require(all(n in removed for wire in dead for n, _, _ in users[wire]),
            'Private ROM signal still has retained cell consumers')
    removed_nets = {name for name, net in nets.items() if dead.intersection(net['bits'])}
    fifo['dead_downstream_cells'] = sorted(cone - original_cone)
    return groups, weight_outputs, cone, protected, removed, removed_nets, outside_pins, fifo


def transform(before, weights):
    require(len(weights) == 6400 and hashlib.sha256(weights).hexdigest() == WEIGHTS_SHA256,
            'Frozen HeadHidden coefficient bytes differ')
    source = before['modules']['mkTop']
    require(not any(name.startswith(PREFIX) for name in source['cells']), 'Already transformed')
    groups, outputs, cone, protected, removed, removed_nets, outside, fifo = identify(source)
    result = deepcopy(before)
    module = result['modules']['mkTop']
    cells, nets = module['cells'], module['netnames']
    used = {wire for cell in source['cells'].values() for bits in cell['connections'].values()
            for wire in bits if type(wire) is int}
    used.update(wire for net in source['netnames'].values() for wire in net['bits'] if type(wire) is int)
    used.update(wire for port in source['ports'].values() for wire in port['bits'] if type(wire) is int)
    next_wire = max(used) + 1
    for name in removed:
        del cells[name]
    for name in removed_nets:
        del nets[name]
    additions = []

    def primitive(suffix, kind, inputs, init=None, output=None):
        nonlocal next_wire
        name = PREFIX + suffix
        require(name not in cells and name not in nets, 'New name collides: ' + name)
        if output is None:
            output = next_wire
            next_wire += 1
        parameters = {'INIT': format(init, '016b')} if kind == 'LUT4' else {}
        cell = {'hide_name': 0, 'type': kind, 'parameters': parameters,
                'attributes': {'module_not_derived': '00000000000000000000000000000001'},
                'port_directions': {**{p: 'input' for p in inputs}, 'Z': 'output'},
                'connections': {**{p: [w] for p, w in inputs.items()}, 'Z': [output]}}
        cells[name] = cell
        nets[name] = {'hide_name': 0, 'bits': [output], 'attributes': {}}
        additions.append(name)
        return output

    def lut(suffix, ins, init, output=None):
        return primitive(suffix, 'LUT4', dict(zip('ABCD', ins)), init, output)

    def mux_tree(suffix, leaves, selects):
        require(len(leaves) == 8 and len(selects) == 3, 'Expected private LUT7 cluster')
        level = [primitive(suffix + '_mux5_' + str(i), 'PFUMX',
                           {'BLUT': leaves[2*i], 'ALUT': leaves[2*i+1], 'C0': selects[0]})
                 for i in range(4)]
        level = [primitive(suffix + '_mux6_' + str(i), 'L6MUX21',
                           {'D0': level[2*i], 'D1': level[2*i+1], 'SD': selects[1]})
                 for i in range(2)]
        return primitive(suffix + '_mux7', 'L6MUX21',
                         {'D0': level[0], 'D1': level[1], 'SD': selects[2]})

    mux_init = sum(1 << i for i in range(16) if ((i >> 1) & 1 if (i >> 2) & 1 else i & 1))
    tail_init = sum(1 << i for i in range(16)
                    if not (i & 8) and ((i >> 1) & 1 if (i >> 2) & 1 else i & 1))
    gate_init = sum(1 << i for i in range(16) if (i & 1) and not (i & 2) and not (i & 4))
    for bit in range(8):
        address = groups[bit]
        prefix = 'b' + str(bit)
        blocks = []
        for block in range(50):
            key = prefix + '_block' + str(block)
            leaves = []
            for leaf in range(8):
                offset = 128 * block + 16 * leaf
                init = sum(((weights[offset + i] >> bit) & 1) << i for i in range(16))
                leaves.append(lut(key + '_lut' + str(leaf), address[:4], init))
            blocks.append(mux_tree(key, leaves, address[4:7]))
        banks = []
        for bank in range(3):
            key = prefix + '_select' + str(bank)
            leaves = [lut(key + '_lut' + str(i),
                          [blocks[bank*16+2*i], blocks[bank*16+2*i+1], address[7], '0'], mux_init)
                      for i in range(8)]
            banks.append(mux_tree(key, leaves, address[8:11]))
        tail = lut(prefix + '_tail0', [blocks[48], blocks[49], address[7], address[8]], tail_init)
        banks.append(lut(prefix + '_tail1', [tail, address[9], address[10], '0'], gate_init))
        final = [lut(prefix + '_final' + str(i), [banks[2*i], banks[2*i+1], address[11], '0'], mux_init)
                 for i in range(2)]
        primitive(prefix + '_output', 'PFUMX',
                  {'BLUT': final[0], 'ALUT': final[1], 'C0': address[12]}, output=outputs[bit])
        first_init = sum(1 << i for i in range(16) if ((i & 1) and (i & 2)) or ((i & 4) and (i & 8)))
        final_init = sum(1 << i for i in range(16) if ((i & 1) and (i & 2)) or (i & 4))
        mux = lut(prefix + '_fifo0', [fifo['q1'][bit], fifo['controls'][1], fifo['q0'][bit], fifo['controls'][2]], first_init)
        lut(prefix + '_fifo1', [outputs[bit], fifo['controls'][0], mux, '0'], final_init,
            output=fifo['di'][bit])
    counts = Counter(cells[name]['type'] for name in additions)
    require(counts == {'LUT4': 3440, 'PFUMX': 1704, 'L6MUX21': 1272}, 'Constructive count differs')
    oldcells = source['cells']
    removed_counts = Counter(oldcells[name]['type'] for name in removed)
    return result, {
        'status': 'independent_audit_required', 'scope': 'Private combinational ROM replacement; no pack/timing result',
        'frozen_weight_sha256': WEIGHTS_SHA256, 'address_count': 8192, 'nonpadding_addresses': 6400,
        'result_wires': outputs, 'address_groups': groups,
        'fifo_boundary': fifo,
        'original_cone_cell_types': dict(sorted(Counter(oldcells[n]['type'] for n in cone).items())),
        'protected_cell_types': dict(sorted(Counter(oldcells[n]['type'] for n in protected).items())),
        'removed_cell_types': dict(sorted(removed_counts.items())), 'added_cell_types': dict(sorted(counts.items())),
        'net_cell_delta': {kind: counts[kind] - removed_counts[kind] for kind in sorted(counts)},
        'retained_outside_observation_pins': len(outside),
        'removed_cells': sorted(removed), 'removed_net_aliases': sorted(removed_nets),
        'protected_cells': sorted(protected), 'added_cells': additions,
        'private_lut7_clusters': 424, 'dedicated_final_mux5_clusters': 8,
        'max_constructive_primitive_depth': 10,
        'max_fifo_di_primitive_depth': 11,
        'all_original_ffs_preserved': True, 'extra_coefficient_cycles': 0,
        'area_improvement_claimed': False,
    }


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'weights', 'output', 'rtl', 'cells-sim', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    inputs = {name: getattr(args, name) for name in ('before', 'weights', 'rtl', 'cells_sim')}
    require(len({p.resolve() for p in [*inputs.values(), args.output, args.report]}) == len(inputs) + 2,
            'Input/output paths must differ')
    hashes = {name: digest(path) for name, path in inputs.items()}
    output_before = digest(args.output) if args.output.exists() else None
    require(output_before is None or output_before == hashes['before'],
            'Existing output is not the preserved pre-ROM input')
    before = json.loads(args.before.read_text())
    after, report = transform(before, args.weights.read_bytes())
    proof = checker.audit(before, after, args.weights.read_bytes(), rtl=args.rtl, cells_sim=args.cells_sim)
    require(proof['status'] == 'pass', 'Independent prewrite audit failed')
    require(all(digest(path) == hashes[name] for name, path in inputs.items()), 'Inputs changed')
    require((digest(args.output) if args.output.exists() else None) == output_before, 'Output changed during audit')
    # Compact encoding and insertion order are part of the frozen candidate identity.
    encoded = json.dumps(after, separators=(',', ':')) + '\n'
    report.update(status='pass', input_json=str(args.before.resolve()), input_sha256=hashes['before'],
                  preserved_input=args.before.name, output_json=str(args.output.resolve()),
                  output_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
                  rtl_sha256=hashes['rtl'], weights_sha256=hashes['weights'], cells_sim_sha256=hashes['cells_sim'],
                  transform_sha256=digest(Path(__file__)), auditor_sha256=digest(Path(checker.__file__)),
                  address_auditor_sha256=checker.ADDRESS_AUDITOR_HASH, prewrite_audit=proof)
    atomic_write(args.output, encoded)
    atomic_write(args.report, json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'added_cell_types', 'removed_cell_types', 'net_cell_delta', 'output_sha256')}))


if __name__ == '__main__':
    main()
