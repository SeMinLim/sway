#!/usr/bin/env python3
"""Duplicate mapped combinational drivers, retaining the original state topology.

The physical-netlist build stage accepts un-packed Yosys JSON. It never clones sequential cells,
changes truth tables, inserts logic levels, or changes timing constraints.
"""
import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def natural(endpoint):
    return tuple((0, int(x)) if x.isdigit() else (1, x)
                 for x in re.split(r"(\d+)", repr(endpoint)))


def run(args):
    paths = [args.input.resolve(), args.output.resolve(), args.manifest.resolve()]
    if len(set(paths)) != len(paths) or args.cap < 2:
        raise ValueError('input/output/manifest must be distinct, and cap must be >=2')
    args.output.unlink(missing_ok=True)
    args.manifest.unlink(missing_ok=True)
    doc = json.loads(args.input.read_text())
    module = doc['modules'][args.top]
    cells = module['cells']
    users = collections.defaultdict(set)
    drivers = {}
    all_bits = set()
    for name, cell in cells.items():
        for port, bits in cell['connections'].items():
            direction = cell['port_directions'][port]
            for index, bit in enumerate(bits):
                if isinstance(bit, int):
                    all_bits.add(bit)
                if direction == 'output':
                    if bit in drivers:
                        raise ValueError(f'multiple drivers on {bit}')
                    drivers[bit] = (name, port, index)
                elif direction == 'input':
                    users[bit].add((name, port, index))
                else:
                    raise ValueError(f'unsupported inout {name}.{port}')
    for net in module['netnames'].values():
        all_bits.update(bit for bit in net['bits'] if isinstance(bit, int))
    top_bits = {bit for port in module['ports'].values() for bit in port['bits']}
    next_bit = max(all_bits) + 1
    clone_origins = {}
    aliases = {}
    splits = []
    pending = collections.deque()
    enqueued = set()
    unsupported = {}
    before_cells = len(cells)
    bit_aliases = collections.defaultdict(list)
    for name, net in module['netnames'].items():
        for index, bit in enumerate(net['bits']):
            if isinstance(bit, int):
                bit_aliases[bit].append((bool(net.get('hide_name')), name, index))

    def destination_order(endpoint):
        name, port, index = endpoint
        cell = cells[name]
        if cell['type'] == 'TRELLIS_FF' and 'Q' in cell['connections']:
            aliases_for_q = bit_aliases.get(cell['connections']['Q'][0], [])
            if aliases_for_q:
                hidden, logical_name, bit_index = min(aliases_for_q, key=lambda a: (a[0], len(a[1]), a[1]))
                return natural((logical_name, bit_index, port, name))
        return natural(endpoint)

    def enqueue(bit):
        if isinstance(bit, int) and bit not in enqueued:
            pending.append(bit)
            enqueued.add(bit)

    def canonical(bit):
        while bit in aliases:
            bit = aliases[bit]
        return bit

    def safe_nonclock_net(bit):
        if bit not in drivers or bit in top_bits:
            return False
        return not any(port.startswith('CLK') or port == 'WCK'
                       for name, port, index in users[bit])

    def sequential_boundary(bit):
        if not safe_nonclock_net(bit):
            return False
        name, port, index = drivers[bit]
        cell = cells[name]
        return (cell['type'] == 'TRELLIS_FF' and port == 'Q' and index == 0
                and cell['connections']['Q'] == [bit])

    def supported(bit):
        if not safe_nonclock_net(bit):
            return False
        name, port, index = drivers[bit]
        cell = cells[name]
        if port != 'Z' or index != 0:
            return False
        if cell['type'] == 'LUT4':
            return set(cell['connections']) == {'A', 'B', 'C', 'D', 'Z'} and all(
                len(bits) == 1 for bits in cell['connections'].values()) and bool(
                re.fullmatch('[01]{16}', cell['parameters'].get('INIT', '')))
        if cell['type'] == 'PFUMX':
            if set(cell['connections']) != {'ALUT', 'BLUT', 'C0', 'Z'}:
                return False
            for input_port in ('ALUT', 'BLUT'):
                source = cell['connections'][input_port][0]
                if source not in drivers:
                    return False
                source_name, source_port, source_index = drivers[source]
                if cells[source_name]['type'] != 'LUT4' or not supported(source):
                    return False
            return True
        return False

    def add_clone(name, private_inputs=None):
        nonlocal next_bit
        original = cells[name]
        clone = copy.deepcopy(original)
        serial = len(clone_origins)
        new_name = f'{name}$fanout${serial}'
        if new_name in cells or new_name + '.Z' in module['netnames']:
            raise ValueError('clone name collision')
        clone['attributes']['keep'] = '00000000000000000000000000000001'
        new_bit = next_bit
        next_bit += 1
        old_bit = original['connections']['Z'][0]
        aliases[new_bit] = canonical(old_bit)
        clone['connections']['Z'] = [new_bit]
        for port, bit in (private_inputs or {}).items():
            clone['connections'][port] = [bit]
        cells[new_name] = clone
        clone_origins[new_name] = clone_origins.get(name, name)
        drivers[new_bit] = (new_name, 'Z', 0)
        module['netnames'][new_name + '.Z'] = {
            'hide_name': 1, 'bits': [new_bit], 'attributes': {}}
        for port, bits in clone['connections'].items():
            if clone['port_directions'][port] == 'input':
                for index, bit in enumerate(bits):
                    users[bit].add((new_name, port, index))
                    enqueue(bit)
        return new_bit

    def clone_driver(bit):
        if not supported(bit):
            raise ValueError(f'unsupported cloning request {bit}')
        name = drivers[bit][0]
        cell = cells[name]
        private = {}
        if cell['type'] == 'PFUMX':
            # A PFUMX consumes a pair of LUT4s during packing. Sharing these
            # private inputs between duplicated muxes violates pack constraints.
            for port in ('ALUT', 'BLUT'):
                source = cell['connections'][port][0]
                private[port] = add_clone(drivers[source][0])
        return add_clone(name, private)

    seeds = []
    sequential_ce_boundaries = {}
    for bit, endpoints in list(users.items()):
        if not isinstance(bit, int):
            continue
        if len(endpoints) > args.cap and any(
                cells[n]['type'] == 'TRELLIS_FF' and p == 'CE' for n, p, i in endpoints):
            if not supported(bit):
                if sequential_boundary(bit):
                    # A registered controller is already a cycle boundary.
                    # Keep it intact; actual routing must validate its fanout.
                    sequential_ce_boundaries[bit] = len(endpoints)
                    continue
                raise ValueError(f'initial CE driver is unsupported: {bit} {drivers.get(bit)}')
            seeds.append(bit)
            enqueue(bit)

    while pending:
        bit = pending.popleft()
        enqueued.remove(bit)
        if len(users[bit]) <= args.cap:
            continue
        if not supported(bit):
            unsupported[bit] = drivers.get(bit)
            continue
        endpoints = sorted(users[bit], key=destination_order)
        groups = [endpoints[start:start + args.cap] for start in range(0, len(endpoints), args.cap)]
        copies = []
        for group in groups[1:]:
            new_bit = clone_driver(bit)
            copies.append(new_bit)
            for name, port, index in group:
                if cells[name]['connections'][port][index] != bit:
                    raise ValueError('stale endpoint')
                cells[name]['connections'][port][index] = new_bit
                users[bit].remove((name, port, index))
                users[new_bit].add((name, port, index))
        splits.append({'net_bit': bit, 'driver': drivers[bit][0],
                       'before_fanout': len(endpoints), 'copies': copies})
        if len(clone_origins) > args.max_clones:
            raise ValueError('clone budget exceeded')

    remaining = []
    for bit, driver in unsupported.items():
        if len(users[bit]) <= args.cap:
            continue
        remaining.append({'bit': bit, 'fanout': len(users[bit]),
                          'driver': driver,
                          'type': cells[driver[0]]['type'] if driver else 'input'})
    remaining.sort(key=lambda r: r['fanout'], reverse=True)
    all_remaining = []
    for bit, endpoints in users.items():
        if len(endpoints) <= args.cap or bit not in drivers:
            continue
        name, port, index = drivers[bit]
        if any(p in ('CLK', 'WCK', 'CLKA', 'CLKB') for n, p, i in endpoints):
            continue
        all_remaining.append({'bit': bit, 'driver': name, 'type': cells[name]['type'],
                              'fanout': len(endpoints),
                              'sink_ports': dict(collections.Counter(p for n, p, i in endpoints))})
    all_remaining.sort(key=lambda r: r['fanout'], reverse=True)
    result = {
        'schema': 1, 'scope': 'mapped combinational duplication, before nextpnr packing',
        'input_sha256': sha(args.input), 'top': args.top, 'cap': args.cap,
        'initial_ce_nets': len(seeds), 'splits': splits,
        'unmodified_sequential_ce_boundaries': [
            {'bit': bit, 'driver': drivers[bit], 'type': 'TRELLIS_FF',
             'before_fanout': before_fanout, 'fanout': len(users[bit]),
             'sink_ports': dict(collections.Counter(p for n, p, i in users[bit]))}
            for bit, before_fanout in sequential_ce_boundaries.items()],
        'clone_origins': clone_origins,
        'net_aliases': {str(bit): old for bit, old in aliases.items()},
        'cloned_cell_types': dict(collections.Counter(cells[n]['type'] for n in clone_origins)),
        'cells_before': before_cells, 'cells_after': len(cells),
        'unsupported_recursive_boundaries': remaining,
        'all_remaining_nonclock_high_fanout_nets': all_remaining,
        'partition_heuristic': 'logical destination FF Q net name and bit index, then cell name; no placement coordinates',
        'max_transformed_output_fanout': max((len(users[b]) for b in aliases), default=0),
        'max_final_seed_fanout': max((len(users[b]) for b in seeds), default=0),
        'no_sequential_cells_cloned': True,
        'no_timing_constraints_changed': True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, separators=(',', ':')) + '\n')
    result['output_sha256'] = sha(args.output)
    args.manifest.write_text(json.dumps(result, indent=2) + '\n')
    print(f"SWAY_CONTROL_REPLICATION generated={len(clone_origins)} "
          f"cap={args.cap} initial_ce_nets={len(seeds)} "
          f"remaining_nonclock_high_fanout_nets={len(all_remaining)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--top', default='mkTop')
    parser.add_argument('--cap', type=int, default=64)
    parser.add_argument('--max-clones', type=int, default=10000)
    args = parser.parse_args()
    if args.cap < 2 or args.input.resolve() in {args.output.resolve(), args.manifest.resolve()}:
        parser.error('cap must be >=2 and outputs must not replace the input')
    run(args)
