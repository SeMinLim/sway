#!/usr/bin/env python3
"""Independent audit of a private combinational HeadHidden ROM.

No transformer/witness import. Independently derive deletion scope, recognize
legal dedicated mux trees, and exhaustively compare 8192 addresses with frozen
INT8 bytes. This is not a pack, placement, routing, or timing success claim.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
sys.dont_write_bytecode = True

ENGINE = 'main_core_headHidden_engine'
PREFIX = ENGINE + '_privateRom_'
REPLICA = ENGINE + '_addressReplica_'
WEIGHT_HASH = '75440fcbdaf2741e978d3e6ab9824c56156665c01aad6c1dd1953af065d226a6'
EXPECTED_COUNTS = Counter({'LUT4': 3440, 'PFUMX': 1704, 'L6MUX21': 1272})
GROUP_COUNTS = Counter({'LUT4': 430, 'PFUMX': 213, 'L6MUX21': 159})
N = 8192
MASK = (1 << N) - 1
# Both primitive definitions have been reviewed against the independent evaluator.
CELL_LIBRARIES = {
    '23c1d1d35b5bd9ba5a07e17cf7d5c07fe7b6840e1deb6c2f746c3f430b4cec54': (
        'Yosys 0.33', {}),
    'c1ddb9de055c6c2a2225215ffd6ded48c884d12d5c7321eaf1a2f417d4f23ce0': (
        'Yosys 0.67+24 / OSS CAD Suite 2026-07-11', {
            'common_sim.vh': '3d55fcb1b659f9a99e081d0e8cd3e0a74fb8d659644103db9ad10b04a64ce75d',
            'ccu2c_sim.vh': '178b1310b2e70768114e1c795118529055799df1ad650a09d0613b3856a81ccc',
        }),
}
REVIEWED_PACKING = {
    'nextpnr_commit': '2b560ad0ccc6e7e93ad8bd6cb0f88f925bbb314b',
    'source': 'ecp5/pack.cc',
    'sha256': 'b6a97ff9e768fe1e93a56310d53f09e706cf0578b0b08a03e46f5e69631e32bc',
}
ADDRESS_AUDITOR = Path(__file__).with_name('check_address_replicas.py')
ADDRESS_AUDITOR_HASH = '8e4e9c140c524553287b5cf43b10de1fc70d11cb69baeed94d53112605c29832'
PORTS = {
 'LUT4': (set('ABCD'), {'Z'}),
 'PFUMX': ({'ALUT', 'BLUT', 'C0'}, {'Z'}),
 'L6MUX21': ({'D0', 'D1', 'SD'}, {'Z'}),
 'CCU2C': ({'CIN', 'A0', 'B0', 'C0', 'D0', 'A1', 'B1', 'C1', 'D1'}, {'S0', 'S1', 'COUT'}),
}
FF_PARAMETERS = {'CEMUX': 'CE', 'CLKMUX': 'CLK', 'GSR': 'DISABLED', 'LSRMUX': 'LSR',
                 'REGSET': 'RESET', 'SRMODE': 'LSR_OVER_CE'}


def require(test, message):
    if not test:
        raise RuntimeError(message)


def same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    return left == right


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def source_proof(cells_sim):
    """Check the actual selected Yosys models, including every evaluated include."""
    cells_sim = Path(cells_sim)
    digest = sha(cells_sim)
    library = CELL_LIBRARIES.get(digest)
    require(library is not None, 'Cell library differs from the reviewed Yosys primitive equations')
    version, includes = library
    hashes = {'cells_sim.v': digest}
    for name, expected in includes.items():
        actual = sha(cells_sim.parent / name)
        require(actual == expected, 'Cell library include differs from reviewed equations: ' + name)
        hashes[name] = actual
    return {'reviewed_library': version, 'library_sha256': hashes,
            'reviewed_packing_source': REVIEWED_PACKING,
            'equations': 'LUT4 INIT[{D,C,B,A}]; PFUMX C0?ALUT:BLUT; L6MUX21 SD?D1:D0; CCU2C official LUT4/LUT2 carry equations',
            'topology': 'Private LUT4 pair -> PFUMX; balanced PFUMX pair -> L6MUX21; balanced previous L6 pair -> final L6MUX21. New stages begin at ordinary LUT4 inputs.'}


def scalar(cell, port):
    values = cell['connections'].get(port, [])
    require(len(values) == 1 and (type(values[0]) is int or values[0] in ('0', '1')),
            'Expected defined scalar primitive port: ' + port)
    return values[0]


def primitive(cell, new=False):
    kind = cell['type']
    require(kind in PORTS and (not new or kind != 'CCU2C'), 'Unreviewed combinational primitive: ' + kind)
    inputs, outputs = PORTS[kind]
    require(set(cell['connections']) == inputs | outputs
            and cell['port_directions'] == {**dict.fromkeys(inputs, 'input'), **dict.fromkeys(outputs, 'output')},
            'Unexpected primitive ports/directions: ' + kind)
    for pin in inputs | outputs:
        value = scalar(cell, pin)
        if pin in outputs:
            require(type(value) is int, 'Primitive output is not an integer wire')
    params = cell['parameters']
    if kind == 'LUT4':
        require(set(params) <= {'INIT'} and (not new or set(params) == {'INIT'}), 'Unreviewed LUT4 parameters')
        init(params, 'INIT')
    elif kind == 'CCU2C':
        require(set(params) <= {'INIT0', 'INIT1', 'INJECT1_0', 'INJECT1_1'}, 'Unreviewed carry parameters')
        for half in range(2):
            init(params, 'INIT' + str(half))
            require(params.get('INJECT1_' + str(half), 'YES') in {'YES', 'NO'}, 'Unknown carry injection')
    else:
        require(params == {}, 'Unreviewed mux parameters')


def init(parameters, key):
    text = parameters.get(key, '0' * 16)
    require(isinstance(text, str) and re.fullmatch(r'[01]{1,16}', text), 'Undefined LUT truth table')
    return int(text, 2)


def connectivity(module):
    drivers, users = defaultdict(list), defaultdict(list)
    for name, cell in module['cells'].items():
        require(set(cell['connections']) == set(cell['port_directions']), 'Cell port/direction mismatch: ' + name)
        for pin, direction in cell['port_directions'].items():
            require(direction in {'input', 'output', 'inout'}, 'Unknown port direction')
            for index, wire in enumerate(cell['connections'][pin]):
                if type(wire) is int:
                    if direction in {'output', 'inout'}:
                        drivers[wire].append((name, pin, index))
                    if direction in {'input', 'inout'}:
                        users[wire].append((name, pin, index))
    return drivers, users


def all_wires(module):
    result = {w for c in module['cells'].values() for values in c['connections'].values() for w in values if type(w) is int}
    result.update(w for n in module['netnames'].values() for w in n['bits'] if type(w) is int)
    result.update(w for p in module['ports'].values() for w in p['bits'] if type(w) is int)
    return result


def root_groups(module, drivers):
    cells, nets = module['cells'], module['netnames']
    address = nets[ENGINE + '_addressR']['bits']
    require(len(address) == 13 and all(type(w) is int for w in address) and len(set(address)) == 13,
            'Expected thirteen distinct canonical address wires')
    groups = {0: address}
    expected_names = {REPLICA + str(g) + '_' + str(b) for g in range(1, 8) for b in range(13)}
    require({n for n in cells if n.startswith(REPLICA)} == expected_names, 'Expected exactly 91 existing address FF copies')
    sources = []
    for wire in address:
        require(len(drivers[wire]) == 1 and drivers[wire][0][1:] == ('Q', 0), 'Canonical address lacks unique FF Q')
        name = drivers[wire][0][0]
        source = cells[name]
        require(source['type'] == 'TRELLIS_FF' and source['parameters'] == FF_PARAMETERS,
                'Canonical address FF parameters differ')
        sources.append(source)
    for group in range(1, 8):
        groups[group] = []
        for bit in range(13):
            name = REPLICA + str(group) + '_' + str(bit)
            clone = cells[name]
            wire = scalar(clone, 'Q')
            require(type(wire) is int and drivers[wire] == [(name, 'Q', 0)], 'Address replica has no unique FF Q')
            expected = deepcopy(sources[bit]); expected['connections']['Q'] = [wire]
            require(same(clone, expected), 'Address replica next-state/metadata differs from canonical FF')
            require(same(nets[name], {'hide_name': 0, 'bits': [wire], 'attributes': {}}), 'Address replica net metadata differs')
            groups[group].append(wire)
    roots = {wire: (group, bit) for group, wires in groups.items() for bit, wire in enumerate(wires)}
    require(len(roots) == 104, 'Address groups do not have 104 unique FF Q wires')
    outputs = nets[ENGINE + '_operandQ_D_IN']['bits']
    require(len(outputs) == 17, 'Expected unchanged single-lane operand tuple')
    outputs = outputs[1:9]
    require(len(set(outputs)) == 8 and all(type(w) is int for w in outputs), 'Expected eight unique coefficient output wires')
    return groups, roots, outputs


def fifo_interface(module, address_roots, weights, drivers):
    """Find control cuts by exact truth, independent of mapped names/IDs."""
    cells, nets = module['cells'], module['netnames']
    q0 = nets[ENGINE + '_operandQ.data0_reg']['bits'][1:9]
    q1 = nets[ENGINE + '_operandQ.data1_reg']['bits'][1:9]
    require(len(q0) == len(q1) == 8 and len(set(q0 + q1)) == 16, 'Expected sixteen weight storage FF Qs')
    dis, ff_names = [], []
    for bit in range(8):
        for wire in (q0[bit], q1[bit]):
            require(type(wire) is int and len(drivers[wire]) == 1 and drivers[wire][0][1:] == ('Q', 0), 'Weight state is not unique FF Q')
            require(cells[drivers[wire][0][0]]['type'] == 'TRELLIS_FF', 'Weight state driver is not FF')
        first = drivers[q0[bit]][0][0]; second = drivers[q1[bit]][0][0]
        dis.append(scalar(cells[first], 'DI')); ff_names.extend([first, second])
        require(scalar(cells[second], 'DI') == weights[bit], 'Data1 DI is not the preserved weight output')
    dependencies, all_cone, pending = {}, set(), set()
    def deps(wire):
        if wire in ('0', '1'):
            return frozenset()
        require(type(wire) is int and len(drivers[wire]) == 1, 'Undefined FIFO dependency')
        name, port, index = drivers[wire][0]
        if cells[name]['type'] == 'TRELLIS_FF':
            require(port == 'Q' and index == 0, 'Unexpected FF root port')
            return frozenset({wire})
        require(name not in pending, 'FIFO dependency cycle')
        if name not in dependencies:
            primitive(cells[name]); pending.add(name)
            dependencies[name] = frozenset().union(*(deps(scalar(cells[name], p)) for p in PORTS[cells[name]['type']][0]))
            pending.remove(name); all_cone.add(name)
        return dependencies[name]
    for wire in dis:
        deps(wire)
    labels = {'active': '_activeOn', 'bank_empty': '_bankQ_EMPTY_N', 'full': '_operandQ_FULL_N',
              'empty': '_operandQ_EMPTY_N', 'deq': '_operandQ_DEQ'}
    controls = {}
    for label, suffix in labels.items():
        bits = nets[ENGINE + suffix]['bits']
        require(len(bits) == 1 and type(bits[0]) is int, 'Missing scalar FIFO semantic control: ' + label)
        controls[label] = bits[0]
    control_ffs = set().union(*(deps(wire) for wire in controls.values()))
    require(not control_ffs & (set(address_roots) | set(q0 + q1)) and 0 < len(control_ffs) <= 16,
            'FIFO control depends on data/address or has excessive state roots')
    control_ffs = sorted(control_ffs); width = 1 << len(control_ffs); mask = (1 << width) - 1
    patterns = {wire: pattern(bit, width) for bit, wire in enumerate(control_ffs)}
    ports, _ = evaluate_planes(module, patterns, list(controls.values()), drivers, width)
    values = dict(zip(controls, ports)); enq = values['active'] & values['bank_empty'] & values['full']
    deq, full, empty = values['deq'], values['full'], values['empty']
    expected = {'di': enq & ((mask ^ empty) | (deq & full)), 'd1': deq & (mask ^ full),
                'h': ((mask ^ deq) & (mask ^ enq)) | ((mask ^ deq) & empty) | ((mask ^ enq) & full)}
    require(len(set(expected.values())) == 3, 'FIFO semantic control functions are not distinct')
    candidates = []
    for name in all_cone:
        if dependencies[name] and dependencies[name] <= set(control_ffs):
            candidates.extend(scalar(cells[name], pin) for pin in PORTS[cells[name]['type']][1])
    candidates = sorted(set(candidates)); truths, _ = evaluate_planes(module, patterns, candidates, drivers, width)
    equivalents = {label: {wire for wire, value in zip(candidates, truths) if value == target}
                   for label, target in expected.items()}
    require(all(equivalents.values()), 'Could not independently identify all FIFO control cuts by truth')
    cut_roles = {wire: label for label, wires in equivalents.items() for wire in wires}
    data_roots = set(address_roots) | set(q0 + q1)
    natural = {scalar(cells[name], pin) for name in all_cone if dependencies[name] & data_roots
               for pin in PORTS[cells[name]['type']][0]
               if (found := deps(scalar(cells[name], pin))) and found <= set(control_ffs)}
    cut_wires = set(cut_roles) | natural
    cut_order = sorted(cut_wires)
    cut_values, _ = evaluate_planes(module, patterns, cut_order, drivers, width)
    vectors = sorted({tuple((value >> state) & 1 for value in cut_values) for state in range(width)})
    protected, todo = set(), list(cut_wires)
    while todo:
        wire = todo.pop()
        if wire in ('0', '1') or wire in control_ffs:
            continue
        name = drivers[wire][0][0]
        if name in protected:
            continue
        protected.add(name)
        todo.extend(scalar(cells[name], p) for p in PORTS[cells[name]['type']][0])
    return {'q0': q0, 'q1': q1, 'dis': dis, 'ff_names': ff_names, 'cut_roles': cut_roles,
            'equivalents': equivalents, 'cut_wires': cut_wires, 'cut_order': cut_order,
            'control_vectors': vectors, 'protected_control_cells': protected,
            'control_ff_roots': control_ffs, 'control_truth_cases': width, 'semantic_controls': controls}


def deletion_plan(module, roots, outputs, drivers, users, control_protected=()):
    cells = module['cells']; cone, active = set(), set()
    def visit(wire):
        if wire in roots or wire in ('0', '1'):
            return
        require(type(wire) is int and len(drivers[wire]) == 1, 'Undefined/multiply driven original coefficient wire')
        name = drivers[wire][0][0]
        require(name not in active, 'Original coefficient cycle')
        if name in cone:
            return
        primitive(cells[name]); active.add(name)
        for pin in PORTS[cells[name]['type']][0]:
            visit(scalar(cells[name], pin))
        active.remove(name); cone.add(name)
    for wire in outputs:
        visit(wire)
    initial = set(cone); boundary = set(outputs)
    exposed = {wire for port in module['ports'].values() for wire in port['bits'] if type(wire) is int}
    # Forward closure supplies only completely unobserved combinational branches.
    forward, todo = set(), list(initial)
    while todo:
        name = todo.pop()
        for pin in PORTS[cells[name]['type']][1]:
            wire = scalar(cells[name], pin)
            if wire in boundary or wire in roots:
                continue
            for user, _, _ in users[wire]:
                if user in initial or user in forward or user in control_protected:
                    continue
                if cells[user]['type'] not in {'LUT4', 'PFUMX', 'L6MUX21'}:
                    continue
                primitive(cells[user]); forward.add(user); todo.append(user)
    dead = set(forward)
    while True:
        rejected = {name for name in dead for pin in PORTS[cells[name]['type']][1]
                    if (wire := scalar(cells[name], pin)) in boundary | exposed | set(roots)
                    or any(user not in dead for user, _, _ in users[wire])}
        if not rejected:
            break
        dead.difference_update(rejected)
    cone.update(dead)
    protected = set(); outside_pins = []
    def protect(name):
        if name in protected or name not in cone:
            return
        protected.add(name)
        for pin in PORTS[cells[name]['type']][0]:
            wire = scalar(cells[name], pin)
            if wire in roots or wire in ('0', '1'):
                continue
            require(type(wire) is int and len(drivers[wire]) == 1, 'Undefined protected ancestor')
            protect(drivers[wire][0][0])
    for name in cone:
        cell = cells[name]
        if cell['type'] == 'CCU2C' or name in control_protected:
            protect(name)
        for pin in PORTS[cell['type']][1]:
            wire = scalar(cell, pin)
            if wire in boundary:
                continue
            external = [entry for entry in users[wire] if entry[0] not in cone]
            if external or wire in exposed or wire in roots:
                outside_pins.extend(external); protect(name)
    removed = cone - protected
    require(removed and not removed & set(control_protected), 'No private cells or control producer would be deleted')
    require(all(drivers[wire][0][0] in removed for wire in outputs), 'Retained boundary driver cannot be replaced')
    dead_wires = {scalar(cells[name], pin) for name in removed for pin in PORTS[cells[name]['type']][1]} - boundary
    require(not dead_wires & exposed, 'Removed internal wire reaches a top-level port')
    require(all(user in removed for wire in dead_wires for user, _, _ in users[wire]), 'Removed private wire reaches retained cell')
    aliases = {name for name, net in module['netnames'].items() if any(wire in dead_wires for wire in net['bits'])}
    return {'cone': cone, 'initial_cone': initial, 'dead_extra': dead, 'protected': protected,
            'removed': removed, 'dead_wires': dead_wires, 'removed_netnames': aliases, 'outside_pins': outside_pins}


def topology(cells, additions, drivers, users):
    """Recognize physical dedicated trees, without using generated cell names."""
    rank, dedicated_parent = {}, {}
    def child(parent, pin, kinds):
        wire = scalar(cells[parent], pin)
        require(type(wire) is int and len(drivers[wire]) == 1, 'Dedicated mux data is not uniquely driven')
        name, port, index = drivers[wire][0]
        require(name in additions and cells[name]['type'] in kinds and port == 'Z' and index == 0,
                'Illegal dedicated mux input topology')
        require(users[wire] == [(parent, pin, 0)], 'Dedicated internal subtree is not private')
        require(name not in dedicated_parent, 'Dedicated subtree has multiple physical parents')
        dedicated_parent[name] = parent
        return name
    for name in sorted(additions):
        if cells[name]['type'] == 'PFUMX':
            lo = child(name, 'BLUT', {'LUT4'}); hi = child(name, 'ALUT', {'LUT4'})
            require(lo != hi, 'PFUMX inputs share one physical LUT4')
            rank[name] = 5
    pending = {name for name in additions if cells[name]['type'] == 'L6MUX21'}
    while pending:
        ready = []
        for name in sorted(pending):
            data = [scalar(cells[name], pin) for pin in ('D0', 'D1')]
            require(all(type(w) is int and len(drivers[w]) == 1 for w in data), 'Undefined L6 data input')
            names = [drivers[w][0][0] for w in data]
            if all(n in rank for n in names):
                require(rank[names[0]] == rank[names[1]] and rank[names[0]] in {5, 6},
                        'Unbalanced dedicated tree or unsupported stage above LUT7')
                lo = child(name, 'D0', {'PFUMX', 'L6MUX21'}); hi = child(name, 'D1', {'PFUMX', 'L6MUX21'})
                require(lo != hi, 'L6 inputs share one physical subtree')
                rank[name] = rank[lo] + 1; ready.append(name)
        require(ready, 'Dedicated mux cycle or illegal/non-dedicated data driver')
        pending.difference_update(ready)
    stages = Counter(rank.values())
    roots = {name for name in rank if name not in dedicated_parent}
    require(stages == {5: 1704, 6: 848, 7: 424}, 'Dedicated tree stage counts differ from constructive bound')
    require(Counter(rank[name] for name in roots) == {5: 8, 7: 424}, 'Unexpected incomplete dedicated trees')
    ordinary = {name for name in additions if cells[name]['type'] == 'LUT4' and name not in dedicated_parent}
    require(len(ordinary) == 32, 'Expected thirty-two ordinary tail/FIFO LUT4s')
    return {'rank': rank, 'roots': roots, 'ordinary': ordinary,
            'full_lut7_macros': 424, 'finishing_lut5_macros': 8, 'ordinary_lut4s': 32}


def minterms(table, values, mask=MASK):
    """Independent bit-parallel minterm expansion, one integer bit per address."""
    answer = 0
    for row in range(1 << len(values)):
        if not (table >> row) & 1:
            continue
        term = mask
        for index, value in enumerate(values):
            term &= value if (row >> index) & 1 else mask ^ value
        answer |= term
    return answer


def evaluate_planes(module, root_values, outputs, drivers, width=N):
    mask = (1 << width) - 1
    values = dict(root_values)
    cache, active, counts = {}, set(), Counter()
    def wire_value(wire):
        if wire in ('0', '1'):
            return mask if wire == '1' else 0
        if wire in values:
            return values[wire]
        require(type(wire) is int and len(drivers[wire]) == 1, 'Evaluation found undefined/multiple driver')
        name, output, index = drivers[wire][0]
        require(index == 0 and name not in active, 'Evaluation found invalid port or combinational cycle')
        if name not in cache:
            cell = module['cells'][name]; primitive(cell)
            active.add(name)
            inputs = {pin: wire_value(scalar(cell, pin)) for pin in PORTS[cell['type']][0]}
            params = cell['parameters']; kind = cell['type']
            if kind == 'LUT4':
                result = {'Z': minterms(init(params, 'INIT'), [inputs[p] for p in 'ABCD'], mask)}
            elif kind == 'PFUMX':
                select = inputs['C0']; result = {'Z': (select & inputs['ALUT']) | ((mask ^ select) & inputs['BLUT'])}
            elif kind == 'L6MUX21':
                select = inputs['SD']; result = {'Z': (select & inputs['D1']) | ((mask ^ select) & inputs['D0'])}
            else:
                carry = inputs['CIN']; result = {}
                for half in range(2):
                    table = init(params, 'INIT' + str(half))
                    lut = minterms(table, [inputs[p + str(half)] for p in 'ABCD'], mask)
                    lut2 = minterms(table & 15, [inputs[p + str(half)] for p in 'AB'], mask)
                    inject = params.get('INJECT1_' + str(half), 'YES')
                    result['S' + str(half)] = lut ^ (carry if inject == 'NO' else 0)
                    carry = ((mask ^ lut) & (lut2 if inject == 'NO' else 0)) | (lut & carry)
                result['COUT'] = carry
            active.remove(name); cache[name] = result; counts[kind] += 1
        require(output in cache[name], 'Unreviewed primitive output')
        return cache[name][output]
    planes = [wire_value(wire) for wire in outputs]
    return planes, dict(sorted(counts.items()))


def pattern(bit, width):
    if width < 8:
        return sum(1 << value for value in range(width) if (value >> bit) & 1)
    # Byte construction is bounded linear work, avoiding quadratic giant-int sums.
    if bit < 3:
        return int.from_bytes(bytes([sum(((lane >> bit) & 1) << lane for lane in range(8))]) * (width // 8), 'little')
    run = 1 << (bit - 3)
    return int.from_bytes((bytes(run) + bytes([255]) * run) * (width // (16 * run)), 'little')


def evaluate(module, roots, outputs, drivers):
    patterns = {wire: pattern(bit, N) for wire, (_, bit) in roots.items()}
    planes, counts = evaluate_planes(module, patterns, outputs, drivers)
    decoded = bytes(sum(((plane >> address) & 1) << bit for bit, plane in enumerate(planes)) for address in range(N))
    return decoded, counts


def fifo_truth(old, new, address_roots, weights, fifo, old_drivers, new_drivers):
    """Exhaustive arbitrary reference cuts, or exhaustive actual control-state composition."""
    arbitrary = fifo['cut_wires'] == set(fifo['cut_roles'])
    if arbitrary:
        combinations = 32; width = N * combinations
        controls = {label: pattern(13 + index, width) for index, label in enumerate(('di', 'd1', 'h'))}
        cut_patterns = {wire: controls[label] for wire, label in fifo['cut_roles'].items()}
        q0, q1 = pattern(16, width), pattern(17, width)
    else:
        combinations = len(fifo['control_vectors']) * 4; width = N * combinations
        assignments = [(vector, state) for vector in fifo['control_vectors'] for state in range(4)]
        def blocks(bits):
            return int.from_bytes(b''.join(bytes([255 if bit else 0]) * (N // 8) for bit in bits), 'little')
        cut_patterns = {wire: blocks(vector[index] for vector, _ in assignments)
                        for index, wire in enumerate(fifo['cut_order'])}
        q0 = blocks(state & 1 for _, state in assignments)
        q1 = blocks((state >> 1) & 1 for _, state in assignments)
        controls = {label: cut_patterns[min(wires)] for label, wires in fifo['equivalents'].items()}
    root_values = {wire: pattern(bit, width) for wire, (_, bit) in address_roots.items()}
    root_values.update(cut_patterns)
    root_values.update({wire: q0 for wire in fifo['q0']})
    root_values.update({wire: q1 for wire in fifo['q1']})
    # Equal Q0/Q1 patterns across groups are valid only after structural proof
    # that each endpoint has no dependency on another weight state bit.
    for group, output in enumerate(fifo['dis']):
        seen = set()
        def check(wire):
            if wire in address_roots or wire in fifo['cut_wires'] or wire in ('0', '1'):
                return
            if wire in fifo['q0'] + fifo['q1']:
                require(wire in {fifo['q0'][group], fifo['q1'][group]}, 'Original FIFO endpoint depends on another weight state bit')
                return
            require(len(old_drivers[wire]) == 1, 'Undefined old FIFO endpoint dependency')
            name = old_drivers[wire][0][0]
            if name in seen:
                return
            primitive(old['cells'][name]); seen.add(name)
            for pin in PORTS[old['cells'][name]['type']][0]:
                check(scalar(old['cells'][name], pin))
        check(output)
    old_values, _ = evaluate_planes(old, root_values, fifo['dis'], old_drivers, width)
    new_values, _ = evaluate_planes(new, root_values, fifo['dis'], new_drivers, width)
    expected_bytes = weights + bytes(N - len(weights))
    for bit, (before_value, after_value) in enumerate(zip(old_values, new_values)):
        plane = bytes(sum(((expected_bytes[start + lane] >> bit) & 1) << lane for lane in range(8))
                      for start in range(0, N, 8))
        w = int.from_bytes(plane * combinations, 'little')
        expected = (w & controls['di']) | (q1 & controls['d1']) | (q0 & controls['h'])
        require(before_value == expected, 'Original data0 DI differs from exact FIFO formula at weight bit ' + str(bit))
        require(after_value == expected, 'New data0 DI differs from exact FIFO formula at weight bit ' + str(bit))
    return {'status': 'pass', 'addresses': N, 'control_and_stored_data_combinations_per_address': combinations,
            'data0_bit_comparisons': N * combinations * 8, 'data0_FFs': 8, 'data1_FFs': 8,
            'formula': '(W & d0di) | (Q1 & d0d1) | (Q0 & d0h)',
            'data1_proof': 'Each immutable data1 FF DI is its preserved W wire; exhaustive W equality plus immutable CE/CLK/LSR and parameters proves equal next state',
            'assumed_FIFO_reachability_or_control_one_hot': False,
            'cut_proof_mode': 'all 32 arbitrary di,d1,h,Q0,Q1 assignments' if arbitrary else 'all actual control FF assignments exhaustively composed into unique cut vectors, crossed with all four Q0/Q1 assignments',
            'control_cut_wires': fifo['cut_order'], 'distinct_exhaustive_control_vectors': len(fifo['control_vectors']),
            'control_vectors': fifo['control_vectors'],
            'controls_verified_against_FIFO2_equations': True,
            'control_truth_assignments': fifo['control_truth_cases'],
            'control_state_FF_roots': fifo['control_ff_roots'],
            'equivalent_control_cuts': {key: sorted(value) for key, value in fifo['equivalents'].items()},
            'control_producer_fanin_cells_preserved': len(fifo['protected_control_cells'])}


def transition_proof(before, after, rtl):
    require(isinstance(rtl, str) and rtl.strip(), 'Generated RTL is required for address startup proof')
    require(sha(ADDRESS_AUDITOR) == ADDRESS_AUDITOR_HASH, 'Reviewed address startup auditor changed')
    spec = importlib.util.spec_from_file_location('independent_existing_address_auditor', ADDRESS_AUDITOR)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    proofs = []
    for data in (before, after):
        aliases, proof = module.inspect_replicas(data['modules']['mkTop'], rtl)
        require(len(aliases) == 91 and proof['status'] == 'pass', 'Address startup/observation proof failed')
        proofs.append(proof)
    require(proofs[0]['initialization_and_induction'] == proofs[1]['initialization_and_induction'],
            'Address initialization/induction contract changed')
    return {'status': 'pass', 'address_auditor_sha256': ADDRESS_AUDITOR_HASH,
            'replica_ffs': 91, 'initialization_and_induction': proofs[1]['initialization_and_induction'],
            'observation_boundary': proofs[1]['observation_boundary']}


def audit(before, after, weights, *, rtl, cells_sim):
    require(rtl is not None and cells_sim is not None, 'RTL and reviewed cell library are required')
    rtl = Path(rtl)
    rtl_sha = sha(rtl)
    rtl_text = rtl.read_text()
    require(rtl_text.strip(), 'Generated RTL is required for address startup proof')
    libraries = source_proof(cells_sim)
    require(len(weights) == 6400 and hashlib.sha256(weights).hexdigest() == WEIGHT_HASH,
            'Frozen HeadHidden weight bytes differ')
    require('mkTop' in before['modules'] and before['modules'].keys() == after['modules'].keys(), 'Original module set changed')
    require(all(same(module, after['modules'][name]) for name, module in before['modules'].items() if name != 'mkTop'), 'Non-mkTop library module changed')
    old, new = before['modules']['mkTop'], after['modules']['mkTop']
    old_cells, new_cells = old['cells'], new['cells']
    require(not any(name.startswith(PREFIX) for name in old_cells), 'Private ROM prototype already present')
    old_drivers, old_users = connectivity(old)
    groups, roots, outputs = root_groups(old, old_drivers)
    fifo = fifo_interface(old, roots, outputs, old_drivers)
    extended_roots = set(roots) | set(fifo['q0'] + fifo['q1']) | set(fifo['cut_wires'])
    boundary_outputs = outputs + fifo['dis']
    plan = deletion_plan(old, extended_roots, boundary_outputs, old_drivers, old_users, fifo['protected_control_cells'])
    removed = set(old_cells) - set(new_cells); additions = set(new_cells) - set(old_cells)
    require(removed == plan['removed'], 'Deleted cells differ from independently derived private cone')
    require(additions and all(name.startswith(PREFIX) for name in additions), 'Unexpected added cell name')
    require(Counter(new_cells[name]['type'] for name in additions) == EXPECTED_COUNTS,
            'New primitive counts differ from constructive bound')
    require(all(same(new_cells[name], cell) for name, cell in old_cells.items() if name not in removed),
            'Original retained cell/FF/control/placement changed')
    deleted_nets = set(old['netnames']) - set(new['netnames'])
    added_nets = set(new['netnames']) - set(old['netnames'])
    require(deleted_nets == plan['removed_netnames'], 'Deleted aliases differ from exact dead-private-wire alias set')
    require(added_nets == additions and not additions & set(old['netnames']), 'New scalar net names must equal new cell names')
    require(all(same(new['netnames'][name], net) for name, net in old['netnames'].items() if name not in deleted_nets),
            'Original retained net metadata changed')
    require(same({k: v for k, v in old.items() if k not in {'cells', 'netnames'}},
                 {k: v for k, v in new.items() if k not in {'cells', 'netnames'}}),
            'Original module ports/settings/attributes changed')
    require(same({k: v for k, v in before.items() if k != 'modules'},
                 {k: v for k, v in after.items() if k != 'modules'}), 'Original JSON envelope changed')
    # The previous exact key-set and value comparisons are a complete reversal:
    # delete additions, restore original independently derived cells/aliases.
    old_max = max(all_wires(old)); boundary = set(boundary_outputs); allocated = set()
    for name in additions:
        cell = new_cells[name]; primitive(cell, new=True)
        require(set(cell) == {'hide_name', 'type', 'parameters', 'attributes', 'port_directions', 'connections'}
                and type(cell['hide_name']) is int and cell['hide_name'] == 0,
                'Unexpected new primitive object schema')
        require(cell['attributes'] == {'module_not_derived': '0' * 31 + '1'}, 'New primitive attributes differ from scratch schema')
        wire = scalar(cell, 'Z')
        require(wire not in allocated and (wire in boundary or wire > old_max), 'New output is not unique/fresh or an approved boundary')
        allocated.add(wire)
        require(same(new['netnames'][name], {'hide_name': 0, 'bits': [wire], 'attributes': {}}), 'New primitive net metadata differs')
    require(boundary <= allocated, 'New ROM does not drive all preserved boundaries')
    drivers, users = connectivity(new)
    for name in additions:
        wire = scalar(new_cells[name], 'Z')
        require(drivers[wire] == [(name, 'Z', 0)], 'New output has another driver')
        if wire not in boundary:
            require(users[wire] and all(user in additions for user, _, _ in users[wire]), 'New internal output escapes private ROM or is unused')
        for pin in PORTS[new_cells[name]['type']][0]:
            value = scalar(new_cells[name], pin)
            require(value in extended_roots or value in allocated or value in ('0', '1'), 'New ROM input has foreign ownership/source')
    topo = topology(new_cells, additions, drivers, users)
    visited, active, owners, depths, source_bits = set(), set(), {}, {}, {}
    def walk(wire, group):
        if wire in roots:
            require(roots[wire][0] == group, 'New ROM uses another output group address FF')
            source_bits.setdefault(group, set()).add(roots[wire][1]); return 0
        if wire in fifo['cut_wires']:
            return 0
        if wire in fifo['q0'] + fifo['q1']:
            require(wire in {fifo['q0'][group], fifo['q1'][group]}, 'New FIFO endpoint uses another bit state FF')
            return 0
        if wire in ('0', '1'):
            return 0
        require(len(drivers[wire]) == 1, 'New group input not uniquely driven')
        name = drivers[wire][0][0]
        require(name in additions and name not in active, 'New ROM reaches old logic or has a cycle')
        if name in owners:
            require(owners[name] == group, 'New ROM logic is shared across output groups')
            return depths[name]
        active.add(name)
        depth = 1 + max(walk(scalar(new_cells[name], pin), group) for pin in PORTS[new_cells[name]['type']][0])
        active.remove(name); owners[name] = group; depths[name] = depth; visited.add(name)
        return depth
    output_depths = [walk(wire, group) for group, wire in enumerate(outputs)]
    di_depths = [walk(wire, group) for group, wire in enumerate(fifo['dis'])]
    require(visited == additions, 'New ROM contains unreachable added logic')
    group_details = {}
    for group in range(8):
        selected = {name for name in additions if owners[name] == group}
        require(Counter(new_cells[name]['type'] for name in selected) == GROUP_COUNTS, 'Per-output primitive count differs')
        require(source_bits[group] == set(range(13)), 'New group does not use its complete thirteen-bit address')
        require(Counter(topo['rank'][name] for name in topo['roots'] & selected) == {7: 53, 5: 1},
                'Per-output dedicated macro count differs')
        require(len(topo['ordinary'] & selected) == 4, 'Per-output tail/FIFO logic count differs')
        group_details[str(group)] = {'primitive_counts': dict(GROUP_COUNTS), 'weight_depth': output_depths[group], 'data0_DI_depth': di_depths[group],
                                     'full_lut7_macros': 53, 'finishing_lut5_macros': 1}
    require(max(output_depths) <= 10, 'New ROM exceeds ten primitive levels')
    require(max(di_depths) <= 11, 'New data0 DI exceeds eleven primitive levels')
    expected = weights + bytes(N - len(weights))
    old_bytes, old_counts = evaluate(old, roots, outputs, old_drivers)
    new_bytes, new_counts = evaluate(new, roots, outputs, drivers)
    for label, actual in (('before', old_bytes), ('after', new_bytes)):
        mismatch = [address for address, (a, b) in enumerate(zip(actual, expected)) if a != b]
        require(not mismatch, label + ' ROM differs from frozen bytes at ' + repr(mismatch[:8]))
    fifo_proof = fifo_truth(old, new, roots, weights, fifo, old_drivers, drivers)
    startup = transition_proof(before, after, rtl_text)
    require(sha(rtl) == rtl_sha, 'Generated RTL changed during audit')
    require(sha(ADDRESS_AUDITOR) == ADDRESS_AUDITOR_HASH, 'Reviewed address startup auditor changed during audit')
    require(source_proof(cells_sim) == libraries, 'Primitive sources changed during audit')
    deleted_counts = Counter(old_cells[name]['type'] for name in removed)
    protected_counts = Counter(old_cells[name]['type'] for name in plan['protected'])
    return {'status': 'pass', 'scope': 'Exact private combinational ROM replacement and immutable retained JSON; exhaustive all-address truth comparison',
            'frozen_weights_sha256': WEIGHT_HASH, 'valid_bytes': 6400, 'zero_padding_bytes': 1792,
            'exhaustive_addresses': N, 'exhaustive_output_bit_comparisons': 8 * N, 'mismatches': 0,
            'new_primitive_counts': dict(EXPECTED_COUNTS), 'removed_primitive_counts': dict(sorted(deleted_counts.items())),
            'protected_primitive_counts': dict(sorted(protected_counts.items())),
            'net_primitive_delta': dict(sorted((EXPECTED_COUNTS - Counter()).items())) if not removed else
                {kind: EXPECTED_COUNTS[kind] - deleted_counts[kind] for kind in sorted(set(EXPECTED_COUNTS) | set(deleted_counts))},
            'original_cone_cells': len(plan['cone']), 'protected_cone_cells': len(plan['protected']),
            'initial_extended_cone_cells': len(plan['initial_cone']), 'forward_dead_extra_cells': len(plan['dead_extra']),
            'removed_private_cells': len(removed), 'removed_dead_wire_aliases': len(deleted_nets),
            'protected_external_input_pins': len(plan['outside_pins']), 'retained_boundary_wires': boundary_outputs,
            'retained_original_cells_and_attributes': 'typed JSON value identical',
            'all_original_FFs_clocks_controls_unchanged': True, 'whole_JSON_reverse_comparison': 'pass',
            'private_output_groups': group_details, 'maximum_primitive_depth': max(di_depths), 'maximum_weight_primitive_depth': max(output_depths),
            'FIFO_next_state_proof': fifo_proof,
            'dedicated_topology': {k: v for k, v in topo.items() if k not in {'rank', 'roots', 'ordinary'}},
            'before_evaluated_primitives': old_counts, 'after_evaluated_primitives': new_counts,
            'address_startup_and_observation': startup, 'primitive_source_proof': libraries,
            'physical_result': 'No packing, placement, routing, congestion or timing outcome established'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'weights', 'rtl', 'cells-sim', 'report'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    inputs = {name: getattr(args, name) for name in ('before', 'after', 'weights', 'rtl', 'cells_sim')}
    require(len({path.resolve() for path in [*inputs.values(), args.report]}) == len(inputs) + 1, 'Input and report paths must differ')
    hashes = {name: sha(path) for name, path in inputs.items()}
    report = {'status': 'fail', 'input_sha256': hashes, 'checker_sha256': sha(Path(__file__))}
    try:
        result = audit(json.loads(args.before.read_text()), json.loads(args.after.read_text()), args.weights.read_bytes(),
                       rtl=args.rtl, cells_sim=args.cells_sim)
        require(all(sha(path) == hashes[name] for name, path in inputs.items()), 'Audit inputs changed')
        report.update(result)
        report['inputs_unchanged'] = True
    except (OSError, RuntimeError, KeyError, TypeError, ValueError, AssertionError) as error:
        report['error'] = str(error)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'error', 'new_primitive_counts', 'removed_primitive_counts',
                                                  'net_primitive_delta', 'exhaustive_addresses', 'maximum_primitive_depth') if key in report}))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
