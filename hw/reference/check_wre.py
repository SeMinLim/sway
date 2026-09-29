#!/usr/bin/env python3
"""Independent fail-closed audit of gateDelayQ WRE LUT replication.

Does not import the transformer. Reconstructs every permitted change from the
original netlist, checks exact copied functions and input nets, and reverses
the entire JSON with type-sensitive comparison. Requires the reviewed Yosys
0.67+24 ECP5 models. P&R and timing are separate.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

COMMON_SIM_SHA256 = '3d55fcb1b659f9a99e081d0e8cd3e0a74fb8d659644103db9ad10b04a64ce75d'
CELLS_SIM_SHA256 = 'c1ddb9de055c6c2a2225215ffd6ded48c884d12d5c7321eaf1a2f417d4f23ce0'
PACK_SOURCE_SHA256 = 'b6a97ff9e768fe1e93a56310d53f09e706cf0578b0b08a03e46f5e69631e32bc'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def same(left, right, path='document'):
    """JSON identity, including bool/int and int/float distinctions."""
    require(type(left) is type(right), f'Typed JSON mismatch at {path}')
    if isinstance(left, dict):
        require(left.keys() == right.keys(), f'JSON keys differ at {path}')
        for key in left:
            same(left[key], right[key], path + '/' + key)
    elif isinstance(left, list):
        require(len(left) == len(right), f'JSON array length differs at {path}')
        for index, (a, b) in enumerate(zip(left, right)):
            same(a, b, path + '/' + str(index))
    else:
        require(left == right, f'JSON value differs at {path}')


def load(path):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'Duplicate JSON key {key!r}')
            result[key] = value
        return result
    with Path(path).open() as stream:
        return json.load(stream, object_pairs_hook=reject_duplicates)


def source_model(cells_sim):
    cells_sim = Path(cells_sim)
    common = cells_sim.with_name('common_sim.vh')
    require(sha(cells_sim) == CELLS_SIM_SHA256, 'Unreviewed cells_sim.v source')
    require(sha(common) == COMMON_SIM_SHA256, 'Unreviewed common_sim.vh source')
    return {'cells_sim_sha256': CELLS_SIM_SHA256, 'common_sim_sha256': COMMON_SIM_SHA256,
            'yosys_revision': '0e82bbefe', 'nextpnr_revision': '2b560ad0ccc6e7e93ad8bd6cb0f88f925bbb314b',
            'reviewed_nextpnr_ecp5_pack_cc_sha256': PACK_SOURCE_SHA256}


def audit(before, after, group_size=8, *, cells_sim):
    """Independent structural proof with required reviewed model entry/exit pins."""
    model = source_model(cells_sim)
    require(type(group_size) is int and 1 <= group_size <= 8, 'Invalid group size')
    old = before['modules']['mkTop']
    new = after['modules']['mkTop']
    cells = old['cells']
    intended_rams = {'main_core_block%d_gateDelayQ.arr.0.%d' % (block, index)
                     for block in (0, 1) for index in range(80)}
    discovered_rams = set()
    for name, cell in cells.items():
        if cell['type'] != 'TRELLIS_DPR16X4':
            continue
        for block in (0, 1):
            for scan in ('', 'scan_'):
                if name.startswith('main_core_block%d_%sgateDelayQ.arr.' % (block, scan)):
                    discovered_rams.add(name)
    require(discovered_rams == intended_rams, 'Unexpected complete gateDelayQ RAM inventory')
    old_bits = set()
    drivers = {}
    original_users = {}
    for name, cell in cells.items():
        for port, wires in cell['connections'].items():
            for index, wire in enumerate(wires):
                if type(wire) is int:
                    old_bits.add(wire)
                    table = drivers if cell['port_directions'][port] == 'output' else original_users
                    table.setdefault(wire, []).append((name, port, index))
    for record in list(old['ports'].values()) + list(old['netnames'].values()):
        old_bits.update(wire for wire in record['bits'] if type(wire) is int)
    next_wire = max(old_bits) + 1
    plans = []
    groups = []
    expected_new_cells = set()
    expected_new_aliases = set()
    for block in (0, 1):
        prefix = 'main_core_block%d_gateDelayQ.arr.0.' % block
        targets = [prefix + str(index) for index in range(80)]
        require({name for name, cell in cells.items()
                 if name.startswith(prefix) and cell['type'] == 'TRELLIS_DPR16X4'} == set(targets),
                'Original RAM instance set differs')
        for target in targets:
            ram = cells[target]
            require(ram['type'] == 'TRELLIS_DPR16X4', 'Expected full four-bit RAM primitive')
            same(ram['port_directions'], {'DI': 'input', 'DO': 'output', 'RAD': 'input', 'WAD': 'input',
                                         'WCK': 'input', 'WRE': 'input'}, 'RAM port schema')
            require(set(ram['connections']) == {'DI', 'DO', 'RAD', 'WAD', 'WCK', 'WRE'}, 'Unexpected RAM ports')
            require(all(len(ram['connections'][p]) == (1 if p in ('WCK', 'WRE') else 4)
                        for p in ram['connections']), 'Unexpected RAM widths')
            require(set(ram['parameters']) == {'INITVAL', 'WCKMUX', 'WREMUX'}, 'Unexpected RAM parameters')
            require(len(ram['parameters']['INITVAL']) == 64, 'Unexpected RAM INITVAL width')
            require(ram['parameters']['WCKMUX'] in ('WCK', 'INV'), 'Unsupported WCKMUX')
            require(ram['parameters']['WREMUX'] in ('WRE', 'INV', '0', '1'), 'Unsupported WREMUX')
        old_wre = cells[targets[0]]['connections']['WRE'][0]
        require(type(old_wre) is int and all(cells[t]['connections']['WRE'] == [old_wre] for t in targets),
                'Original RAM WRE group is not uniform')
        require(len(drivers.get(old_wre, [])) == 1, 'WRE must have exactly one driver')
        driver, port, index = drivers[old_wre][0]
        source = cells[driver]
        require(source['type'] == 'LUT4' and (port, index) == ('Z', 0), 'WRE driver is not LUT4.Z')
        same(source['port_directions'], {'A': 'input', 'B': 'input', 'C': 'input', 'D': 'input', 'Z': 'output'},
             'Source LUT ports')
        require(set(source['connections']) == set('ABCDZ'), 'Unexpected source LUT ports')
        require(all(len(v) == 1 for v in source['connections'].values()), 'Unexpected source LUT widths')
        require(set(source['parameters']) == {'INIT'}, 'Unexpected source LUT parameters')
        init = source['parameters']['INIT']
        require(type(init) is str and len(init) == 16 and not set(init) - set('01'), 'Undefined source LUT truth table')
        other_users = [list(u) for u in original_users.get(old_wre, []) if u[0] not in targets or u[1:] != ('WRE', 0)]
        group_report = {'block': block, 'source_driver': driver, 'original_WRE_bit': old_wre,
                        'source_INIT': init, 'source_inputs': {p: source['connections'][p] for p in 'ABCD'},
                        'original_other_users_preserved': other_users, 'copies': []}
        for offset in range(0, 80, group_size):
            name = 'sway_wre_copy_block%d_%d' % (block, offset // group_size)
            alias = name + '$WRE'
            require(name not in cells and alias not in old['netnames'], 'Copy names collide with originals')
            expected_new_cells.add(name)
            expected_new_aliases.add(alias)
            require(name in new['cells'] and alias in new['netnames'], 'Missing required WRE copy')
            copied = new['cells'][name]
            expected = deepcopy(source)
            expected['connections']['Z'] = [next_wire]
            same(copied, expected, 'Exact clone ' + name)
            same(new['netnames'][alias], {'hide_name': 0, 'bits': [next_wire], 'attributes': {}},
                 'Exact new alias ' + alias)
            # Independent bit indexing follows the reviewed LUT4 mux model:
            # D selects 8 bits, C 4 bits, B 2 bits, A the final bit.
            for vector in range(16):
                a, b, c, d = ((vector >> bit) & 1 for bit in range(4))
                expected_z = (int(init, 2) >> (8*d + 4*c + 2*b + a)) & 1
                actual_z = int(copied['parameters']['INIT'][-1 - vector])
                require(actual_z == expected_z, 'LUT truth-table mismatch')
            members = targets[offset:offset + group_size]
            for target in members:
                require(target in new['cells'], 'Original RAM deleted')
                restored_ram = deepcopy(new['cells'][target])
                same(restored_ram['connections']['WRE'], [next_wire], 'Assigned WRE ' + target)
                restored_ram['connections']['WRE'] = [old_wre]
                same(restored_ram, cells[target], 'RAM state/data/clock identity ' + target)
            plans.append((name, alias, next_wire, members, old_wre))
            group_report['copies'].append({'cell': name, 'WRE_bit': next_wire, 'RAM_cells': members,
                                            'raw_WRE_sinks': len(members), 'packed_WRE_sinks': 4 * len(members)})
            next_wire += 1
        groups.append(group_report)
    require(set(new['cells']) == set(cells) | expected_new_cells, 'Cell addition/deletion set differs')
    require(set(new['netnames']) == set(old['netnames']) | expected_new_aliases, 'Alias addition/deletion set differs')
    # Reverse only the independently derived permitted edit set. Any additional
    # cell input, alias, module attribute, port or library edit remains and fails.
    reversed_document = dict(after)
    reversed_document['modules'] = dict(after['modules'])
    reversed_module = dict(new)
    reversed_document['modules']['mkTop'] = reversed_module
    reversed_module['cells'] = dict(new['cells'])
    reversed_module['netnames'] = dict(new['netnames'])
    for name, alias, wire, members, old_wre in plans:
        del reversed_module['cells'][name]
        del reversed_module['netnames'][alias]
        for target in members:
            restored = deepcopy(new['cells'][target])
            restored['connections']['WRE'] = [old_wre]
            reversed_module['cells'][target] = restored
    same(before, reversed_document, 'Whole JSON reversal')
    inventory = Counter(cell['type'] for cell in cells.values())
    proof = {
        'status': 'pass', 'group_size': group_size, 'copied_LUT4_cells': len(plans),
        'rewired_RAM_WRE_pins': 160, 'LUT_truth_table_comparisons': len(plans) * 16,
        'whole_JSON_typed_reversal': 'pass', 'sequential_equivalence': 'pass',
        'proof': [
            'Every new LUT has the original INIT, inputs, ports, attributes and other fields; only Z uses a fresh wire.',
            'Every rewired WRE equals its original Boolean function for every input assignment.',
            'All original RAM parameters, initialization, WCK, DI, DO, RAD and WAD are identical.',
            'The reviewed RAM model initializes the same state, reads mem[RAD], and updates mem[WAD] from DI only on the same selected WCK edge with equal selected WRE.',
            'By induction in the reviewed functional model, corresponding RAM states and read values remain identical for arbitrary input histories and identical initial states, including arbitrary unknown initialization.',
            'Typed whole-JSON reversal proves all other cells, ports, aliases, metadata and library modules unchanged.'
        ],
        'original_cell_inventory': dict(sorted(inventory.items())), 'groups': groups,
        'architecture': 'All FFs, memories, DSPs, coefficients, precision, pipeline, clocks and resets preserved.',
        'timing': 'not evaluated', 'placer_legality': 'not evaluated',
    }
    same(source_model(cells_sim), model, 'Reviewed model entry/exit identity')
    proof['source_model'] = model
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'report', 'cells-sim'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--group-size', type=int, default=8)
    args = parser.parse_args()
    inputs = {'before': args.before, 'after': args.after, 'cells_sim': args.cells_sim,
              'common_sim': args.cells_sim.with_name('common_sim.vh')}
    hashes = {name: sha(path) for name, path in inputs.items()}
    require(args.report.resolve() not in {path.resolve() for path in inputs.values()},
            'Report must differ from every input')
    result = audit(load(args.before), load(args.after), args.group_size, cells_sim=args.cells_sim)
    require(all(sha(path) == hashes[name] for name, path in inputs.items()), 'Audit input changed')
    result.update({'input_sha256': hashes, 'checker_sha256': sha(Path(__file__)), 'inputs_unchanged': True})
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({key: result[key] for key in ('status', 'copied_LUT4_cells', 'rewired_RAM_WRE_pins',
                                                   'whole_JSON_typed_reversal', 'sequential_equivalence', 'timing')}))


if __name__ == '__main__':
    main()
