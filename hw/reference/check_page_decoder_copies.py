#!/usr/bin/env python3
"""Independently audit same-cycle copies of head-hidden high-address decoders.

The transformer is neither imported nor used as an oracle. This checker derives
the exact permitted additions and rewires from the preserved mapped input.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path


ENGINE = 'main_core_headHidden_engine'
ADDRESS_PREFIX = ENGINE + '_addressReplica_'
DECODER_PREFIX = ENGINE + '_pageDecodeReplica_'
FROZEN_ADDRESS_AUDITOR = 'a321b96002a291ca939494004e2ca06fd9d4d8d465caae67614e531b21c279d2'
LUT_TYPES = {'LUT4', 'PFUMX', 'L6MUX21'}
HIGH_BITS = frozenset(range(8, 13))
def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connectivity(module):
    drivers, users = defaultdict(list), defaultdict(list)
    for name, cell in module['cells'].items():
        require(set(cell['connections']) == set(cell['port_directions']), 'Port/direction mismatch: ' + name)
        for port, direction in cell['port_directions'].items():
            require(direction in {'input', 'output'}, 'Unreviewed port direction: ' + name)
            for index, bit in enumerate(cell['connections'][port]):
                if type(bit) is int:
                    (drivers if direction == 'output' else users)[bit].append((name, port, index))
    return drivers, users


def output_bits(cell):
    return [bit for port, direction in cell['port_directions'].items() if direction == 'output'
            for bit in cell['connections'][port]]


def derive_plan(module, aliases):
    """Derive ownership and high-only closure directly from coefficient roots."""
    cells, nets = module['cells'], module['netnames']
    drivers, _ = connectivity(module)
    address = nets[ENGINE + '_addressR']['bits']
    require(len(address) == 13 and len(set(address)) == 13, 'Expected thirteen distinct address bits')
    roots = {wire: bit for bit, wire in enumerate(address)}
    roots.update({wire: roots[canonical] for wire, canonical in aliases.items()})
    operand = nets[ENGINE + '_operandQ_D_IN']['bits']
    require(len(operand) == 17, 'Expected one-lane input/weight/metadata tuple')
    owners, pending = {}, set()

    def visit(wire, group):
        if wire in roots or wire in ('0', '1'):
            return
        require(type(wire) is int and len(drivers[wire]) == 1, 'Undefined/multiple coefficient driver')
        name, _, _ = drivers[wire][0]
        require(name not in pending, 'Combinational coefficient cycle')
        if name in owners:
            return
        require(cells[name]['type'] in LUT_TYPES, 'Foreign primitive in coefficient cone: ' + name)
        owners[name] = group
        pending.add(name)
        for port, direction in cells[name]['port_directions'].items():
            if direction == 'input':
                for source in cells[name]['connections'][port]:
                    visit(source, group)
        pending.remove(name)

    for group, wire in enumerate(operand[1:9]):
        visit(wire, group)
    dependencies, active = {}, set()

    def deps(wire):
        if wire in roots:
            return frozenset({roots[wire]})
        if wire in ('0', '1'):
            return frozenset()
        require(type(wire) is int and len(drivers[wire]) == 1, 'Undefined decoder dependency')
        name, _, _ = drivers[wire][0]
        require(name in owners and name not in active, 'Foreign/cyclic decoder dependency')
        if name not in dependencies:
            active.add(name)
            found = set()
            for port, direction in cells[name]['port_directions'].items():
                if direction == 'input':
                    for source in cells[name]['connections'][port]:
                        found.update(deps(source))
            active.remove(name)
            dependencies[name] = frozenset(found)
        return dependencies[name]

    for wire in operand[1:9]:
        deps(wire)
    pure = {name for name, dependency in dependencies.items() if dependency and dependency <= HIGH_BITS}
    constant_cells = {drivers[wire][0][0] for name in pure
                      for port, direction in cells[name]['port_directions'].items() if direction == 'input'
                      for wire in cells[name]['connections'][port]
                      if type(wire) is int and wire not in roots
                      and not dependencies[drivers[wire][0][0]]}
    constant_outputs = {}
    for name in constant_cells:
        cell = cells[name]
        require(cell['type'] == 'LUT4' and cell['parameters'] == {'INIT': '0' * 16}
                and set(cell['connections']) == set('ABCDZ')
                and all(cell['connections'][pin] == ['0'] for pin in 'ABCD')
                and cell['port_directions'] == {**dict.fromkeys('ABCD', 'input'), 'Z': 'output'}
                and len(cell['connections']['Z']) == 1,
                'Unreviewed retained constant decoder input')
        wire = cell['connections']['Z'][0]
        require(type(wire) is int and drivers[wire] == [(name, 'Z', 0)],
                'Constant decoder leaf must have one uniquely driven scalar output')
        constant_outputs[wire] = name
    require(len(constant_cells) == 25, 'The reviewed decoder has exactly twenty-five zero LUT4 leaves')
    require(len(pure) == 50, 'The reviewed mapped design has exactly fifty pure-high decoder cells')
    require(Counter(cells[name]['type'] for name in pure) == Counter({'LUT4': 25, 'PFUMX': 25}),
            'Unexpected original high-page decoder primitive classes')
    require(all(drivers[wire][0][0] not in pure for wire in operand[1:9]),
            'A coefficient output itself is a high-only truth function')
    pure_outputs = {}
    for name in pure:
        require(len(output_bits(cells[name])) == 1, 'Expected single-output high decoder primitive')
        for wire in output_bits(cells[name]):
            require(type(wire) is int and drivers[wire][0][0] == name, 'Unreviewed decoder output')
            pure_outputs[wire] = name

    groups = {group: set() for group in range(1, 8)}

    def include(group, name):
        if name in groups[group]:
            return
        groups[group].add(name)
        for port, direction in cells[name]['port_directions'].items():
            if direction == 'input':
                for wire in cells[name]['connections'][port]:
                    if wire in pure_outputs:
                        include(group, pure_outputs[wire])
                    elif wire in roots:
                        require(roots[wire] in HIGH_BITS and wire == address[roots[wire]],
                                'Original decoder leaf is not its canonical high-address Q')
                    elif wire in constant_outputs:
                        include(group, constant_outputs[wire])
                    else:
                        require(wire in ('0', '1'), 'High-page closure contains a foreign input: '
                                + repr((group, name, port, wire, drivers.get(wire))))

    borders = []
    for name, group in owners.items():
        if not group or name in pure:
            continue
        for port, direction in cells[name]['port_directions'].items():
            if direction == 'input':
                for index, wire in enumerate(cells[name]['connections'][port]):
                    if wire in pure_outputs:
                        include(group, pure_outputs[wire])
                        borders.append((name, port, index, wire, group))
    require({group: len(names) for group, names in groups.items()} == {**{i: 75 for i in range(1, 7)}, 7: 63},
            'Unexpected independently derived per-consumer decoder closure')
    require({group: len(names & constant_cells) for group, names in groups.items()}
            == {**{i: 25 for i in range(1, 7)}, 7: 21},
            'Unexpected independently derived constant-leaf closure')
    return {'address': address, 'owners': owners, 'pure': pure, 'pure_outputs': pure_outputs,
            'groups': groups, 'borders': borders, 'drivers': drivers, 'dependencies': dependencies,
            'constant_outputs': constant_outputs, 'constant_cells': constant_cells}


def evaluate_high_only(module, roots, output, page, cache, pending):
    """Scalar official LUT4/PFUMX equations, all thirty-two page addresses."""
    cells, drivers = module['cells'], module['_drivers']
    if output in roots:
        return (page >> (roots[output] - 8)) & 1
    if output in ('0', '1'):
        return int(output)
    require(type(output) is int and len(drivers[output]) == 1, 'Invalid high-page evaluator driver')
    name, port, index = drivers[output][0]
    require(name not in pending, 'High-page evaluator cycle')
    if name not in cache:
        pending.add(name)
        cell = cells[name]
        kind = cell['type']
        expected_inputs = {'A', 'B', 'C', 'D'} if kind == 'LUT4' else {'ALUT', 'BLUT', 'C0'}
        require(kind in {'LUT4', 'PFUMX'} and set(cell['connections']) == expected_inputs | {'Z'},
                'Unexpected decoder primitive ports')
        for pin, bits in cell['connections'].items():
            require(len(bits) == 1 and cell['port_directions'][pin] == ('output' if pin == 'Z' else 'input'),
                    'Unexpected decoder port shape')
        values = {pin: evaluate_high_only(module, roots, cell['connections'][pin][0], page, cache, pending)
                  for pin in expected_inputs}
        if kind == 'LUT4':
            require(set(cell['parameters']) == {'INIT'}, 'Unexpected decoder LUT parameters')
            init = cell['parameters']['INIT']
            require(isinstance(init, str) and len(init) == 16 and set(init) <= {'0', '1'}, 'Undefined LUT INIT')
            address = sum(values[pin] << bit for bit, pin in enumerate('ABCD'))
            cache[name] = (int(init, 2) >> address) & 1
        else:
            require(cell['parameters'] == {}, 'Unexpected decoder mux parameters')
            cache[name] = values['ALUT'] if values['C0'] else values['BLUT']
        pending.remove(name)
    require(port == 'Z' and index == 0, 'Unexpected decoder evaluator output')
    return cache[name]


def audit(before, after, rtl, address_auditor):
    old, new = before['modules']['mkTop'], after['modules']['mkTop']
    require(not any(name.startswith(DECODER_PREFIX) for name in old['cells']), 'Preserved input already has page copies')
    old_aliases, old_proof = address_auditor.inspect_replicas(old, rtl)
    require(old_proof.get('status') == 'pass' and len(old_aliases) == 56, 'Expected reviewed56 existing low-address copies')
    plan = derive_plan(old, old_aliases)
    cells, nets = old['cells'], old['netnames']
    additions = set(new['cells']) - set(cells)
    net_additions = set(new['netnames']) - set(nets)
    require(set(cells) <= set(new['cells']) and set(nets) <= set(new['netnames']), 'An original cell or net was removed')
    originals = sorted(plan['pure'])
    constant_originals = sorted(plan['constant_cells'])
    names = {(group, name): DECODER_PREFIX + str(group) + '_'
             + (str(originals.index(name)) if name in plan['pure']
                else 'zero_' + str(constant_originals.index(name)))
             for group, selected in plan['groups'].items() for name in selected}
    high_names = {(group, bit): ADDRESS_PREFIX + str(group) + '_' + str(bit)
                  for group in range(1, 8) for bit in HIGH_BITS}
    require(additions == set(names.values()) | set(high_names.values()), 'Unexpected/missing decoder or high-address FF additions')
    require(Counter(new['cells'][name]['type'] for name in names.values()) == Counter({'LUT4': 342, 'PFUMX': 171}),
            'Expected513 decoder copies including171 private constant-zero leaves')
    high_q, mapped_outputs, output_mapping = {}, {}, {}
    allocated = set()
    old_bits = {wire for cell in cells.values() for bits in cell['connections'].values() for wire in bits if type(wire) is int}
    old_bits.update(wire for net in nets.values() for wire in net['bits'] if type(wire) is int)
    old_bits.update(wire for port in old['ports'].values() for wire in port['bits'] if type(wire) is int)

    def fresh(bits):
        require(len(bits) == 1 and type(bits[0]) is int and bits[0] not in old_bits | allocated,
                'Clone output must be a unique fresh wire')
        allocated.add(bits[0])
        return bits[0]

    for pair, name in sorted(high_names.items()):
        group, bit = pair
        source = plan['address'][bit]
        canonical_name, port, index = plan['drivers'][source][0]
        require(port == 'Q' and index == 0, 'High address is not a canonical FF Q')
        original = cells[canonical_name]
        clone = new['cells'][name]
        q = fresh(clone['connections'].get('Q', []))
        expected = deepcopy(original)
        expected['connections']['Q'] = [q]
        require(clone == expected, 'High-address FF is not an exact same-cycle clone: ' + name)
        require(new['netnames'].get(name) == {'hide_name': 0, 'bits': [q], 'attributes': {}}, 'Unexpected high-FF net')
        high_q[pair] = q
    expected_nets = set(high_names.values())
    for (group, source_name), clone_name in sorted(names.items()):
        original, clone = cells[source_name], new['cells'][clone_name]
        for port, direction in original['port_directions'].items():
            if direction == 'output':
                q = fresh(clone['connections'].get(port, []))
                old_q = original['connections'][port][0]
                mapped_outputs[group, old_q] = q
                output_mapping[q] = old_q
                net_name = clone_name + '_' + port
                expected_nets.add(net_name)
                require(new['netnames'].get(net_name) == {'hide_name': 0, 'bits': [q], 'attributes': {}},
                        'Unexpected copied-decoder net metadata')
    require(net_additions == expected_nets, 'Unexpected or missing clone netnames')
    address_positions = {wire: bit for bit, wire in enumerate(plan['address'])}
    for (group, source_name), clone_name in names.items():
        expected = deepcopy(cells[source_name])
        for port, direction in expected['port_directions'].items():
            mapped = []
            for wire in expected['connections'][port]:
                if direction == 'output' or wire in plan['pure_outputs'] or wire in plan['constant_outputs']:
                    mapped.append(mapped_outputs[group, wire])
                elif wire in address_positions:
                    require(address_positions[wire] in HIGH_BITS, 'A low-address truth plane would be cloned')
                    mapped.append(high_q[group, address_positions[wire]])
                else:
                    require(wire in ('0', '1'), 'Foreign copied decoder input')
                    mapped.append(wire)
            expected['connections'][port] = mapped
        require(new['cells'][clone_name] == expected, 'Decoder INIT/inputs/attributes differ: ' + clone_name)
    restored = deepcopy(after)
    restored_module = restored['modules']['mkTop']
    for name, port, index, old_wire, group in plan['borders']:
        require(restored_module['cells'][name]['connections'][port][index] == mapped_outputs[group, old_wire],
                'Original coefficient consumer uses the wrong page decoder: ' + name)
        restored_module['cells'][name]['connections'][port][index] = old_wire
    for name in additions:
        del restored_module['cells'][name]
    for name in net_additions:
        del restored_module['netnames'][name]
    require(restored == before, 'Unapproved change outside exact decoder/FF additions and derived coefficient input pins')
    del restored
    new_aliases, new_proof = address_auditor.inspect_replicas(new, rtl)
    require(new_proof.get('status') == 'pass' and len(new_aliases) == 91,
            'Complete same-cycle FF and coefficient observation audit failed')
    require(new_proof['initialization_and_induction']['relies_on_mkRegU_powerup_value'] is False
            and new_proof['initialization_and_induction']['extra_weight_pipeline_cycles'] == 0,
            'Address copies lack the startup/hold induction proof')
    # The frozen FF auditor predates decoder copying. Its structural/startup
    # proof remains applicable, but its historical constant duplication metric
    # is not a measurement of this transform; report the independently derived
    # decoder counts below instead.
    new_proof.pop('coefficient_cells_duplicated', None)
    new_proof['scope'] = 'Same-cycle address FF identity, startup and coefficient observation boundary'
    # The numeric check uses its own scalar primitive interpreter, not either
    # the transformer or the existing bit-parallel mapped-weight evaluator.
    old_eval = {'cells': cells, '_drivers': plan['drivers']}
    new_drivers, _ = connectivity(new)
    new_eval = {'cells': new['cells'], '_drivers': new_drivers}
    old_roots = {plan['address'][bit]: bit for bit in HIGH_BITS}
    new_roots = {**old_roots, **{wire: bit for (_, bit), wire in high_q.items()}}
    comparisons = 0
    for page in range(32):
        old_cache, new_cache = {}, {}
        for (group, old_wire), new_wire in sorted(mapped_outputs.items()):
            expected = evaluate_high_only(old_eval, old_roots, old_wire, page, old_cache, set())
            actual = evaluate_high_only(new_eval, new_roots, new_wire, page, new_cache, set())
            require(expected == actual, 'Copied decoder truth mismatch')
            comparisons += 1
    return {'status': 'pass', 'engine': ENGINE,
            'source_decoder_cells': len(plan['pure']) + len(plan['constant_cells']),
            'source_high_address_decoder_cells': len(plan['pure']),
            'copied_decoder_cells': len(names), 'copied_primitives': {'LUT4': 342, 'PFUMX': 171},
            'copied_high_address_decoder_cells': sum(name in plan['pure'] for _, name in names),
            'copied_constant_zero_luts': sum(name in plan['constant_cells'] for _, name in names),
            'added_high_address_ffs': len(high_names), 'existing_low_address_ffs': len(old_aliases),
            'total_address_replicas': len(new_aliases),
            'decoder_cells_per_consumer_group': {str(g): len(v) for g, v in plan['groups'].items()},
            'rewired_original_coefficient_inputs': len(plan['borders']),
            'retained_constant_zero_luts': len(plan['constant_cells']),
            'allowed_decoder_address_bits': sorted(HIGH_BITS), 'low_address_dependent_cells_copied': 0,
            'whole_netlist_reverse_comparison': 'pass', 'extra_weight_pipeline_cycles': 0,
            'scalar_decoder_truth': {'status': 'pass', 'page_addresses': 32, 'output_comparisons': comparisons,
                                     'mismatches': 0, 'equations': 'LUT4 INIT[{D,C,B,A}]; PFUMX C0?ALUT:BLUT'},
            'same_cycle_and_observation_proof': new_proof,
            'scope': 'Exact high-address decoder/constant-leaf/FF-copy equivalence; no timing or full-design simulation claim'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--rtl', type=Path, required=True)
    parser.add_argument('--address-auditor', type=Path,
                        default=Path(__file__).with_name('check_address_replicas.py'))
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    inputs = {key: getattr(args, key.replace('-', '_')).resolve()
              for key in ('before', 'after', 'rtl', 'address-auditor')}
    hashes = {key: sha(path) for key, path in inputs.items()}
    report = {'status': 'fail', 'input_sha256': hashes, 'input_paths': {k: str(v) for k, v in inputs.items()},
              'checker_sha256': sha(Path(__file__).resolve())}
    try:
        require(hashes['address-auditor'] == FROZEN_ADDRESS_AUDITOR, 'Address auditor differs from independently reviewed frozen source')
        spec = importlib.util.spec_from_file_location('frozen_address_auditor', inputs['address-auditor'])
        require(spec is not None and spec.loader is not None, 'Cannot load frozen address auditor')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        report.update(audit(json.loads(args.before.read_text()), json.loads(args.after.read_text()), args.rtl.read_text(), helper))
        require(all(sha(path) == hashes[key] for key, path in inputs.items()), 'Audit inputs changed during verification')
        report['inputs_unchanged'] = True
    except (OSError, RuntimeError, KeyError, TypeError, ValueError, AssertionError) as exc:
        report['status'], report['error'] = 'fail', str(exc)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'error', 'copied_decoder_cells', 'added_high_address_ffs',
                                                 'rewired_original_coefficient_inputs', 'scalar_decoder_truth') if key in report}))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
