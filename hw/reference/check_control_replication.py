#!/usr/bin/env python3
"""Independently verify duplication by canonicalizing every rewritten net.

No state cell may be added, removed, or modified. Every extra cell must duplicate
an original LUT4/PFUMX with identical truth function and equivalent inputs.
Consequently all original sequential/IO endpoints retain identical combinational
functions, with the original initialization, clocks, resets and cycle boundaries.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, context):
    # Explicit checks remain active under Python's -O/PYTHONOPTIMIZE modes.
    if not condition:
        raise ValueError(f'Control replication audit failed: {context}')


def audit(args):
    require(args.report.resolve() not in {args.input.resolve(), args.output.resolve(),
                                         args.manifest.resolve()}, 'report would replace an input')
    args.report.unlink(missing_ok=True)
    old_doc = json.loads(args.input.read_text())
    new_doc = json.loads(args.output.read_text())
    manifest = json.loads(args.manifest.read_text())
    require(manifest['input_sha256'] == sha(args.input), 'control replication invariant failed')
    require(manifest['output_sha256'] == sha(args.output), 'control replication invariant failed')
    top = manifest['top']
    old = old_doc['modules'][top]
    new = new_doc['modules'][top]
    origins = manifest['clone_origins']
    aliases = {int(a): b for a, b in manifest['net_aliases'].items()}
    original_bits = {b for c in old['cells'].values() for bits in c['connections'].values() for b in bits}
    require(set(aliases).isdisjoint(original_bits), 'control replication invariant failed')
    require(all(bit in original_bits for bit in aliases.values()), 'control replication invariant failed')
    def canon(bit):
        return aliases.get(bit, bit)
    require(old_doc.keys() == new_doc.keys(), 'control replication invariant failed')
    for key in old_doc:
        if key != 'modules':
            require(old_doc[key] == new_doc[key], 'control replication invariant failed')
    require(old_doc['modules'].keys() == new_doc['modules'].keys(), 'control replication invariant failed')
    for name in old_doc['modules']:
        if name != top:
            require(old_doc['modules'][name] == new_doc['modules'][name], 'control replication invariant failed')
    require(old.keys() == new.keys(), 'control replication invariant failed')
    for key in old:
        if key not in ('cells', 'netnames'):
            require(old[key] == new[key], key)
    require(set(new['cells']) == set(old['cells']) | set(origins), 'control replication invariant failed')
    require(set(old['cells']).isdisjoint(origins), 'control replication invariant failed')
    for name, cell in new['cells'].items():
        source = old['cells'][origins.get(name, name)]
        require(source.keys() == cell.keys(), name)
        for key in source:
            if key == 'connections':
                require(source[key].keys() == cell[key].keys(), name)
                for port, bits in source[key].items():
                    require(bits == [canon(b) for b in cell[key][port]], (name, port))
                    if name not in origins and source['port_directions'][port] == 'output':
                        require(bits == cell[key][port], (name, 'original driver rewired'))
                    if port.startswith('CLK') or port == 'WCK':
                        require(bits == cell[key][port], (name, 'clock rewired'))
            elif key == 'attributes' and name in origins:
                expected = dict(source[key], keep='00000000000000000000000000000001')
                require(expected == cell[key], name)
            else:
                require(source[key] == cell[key], (name, key))
        if name in origins:
            require(cell['type'] in ('LUT4', 'PFUMX'), name)
            if cell['type'] == 'LUT4':
                require(set(cell['connections']) == {'A', 'B', 'C', 'D', 'Z'}, 'control replication invariant failed')
                require(set(cell['parameters']['INIT']) <= {'0', '1'}, 'control replication invariant failed')
            else:
                require(set(cell['connections']) == {'ALUT', 'BLUT', 'C0', 'Z'}, 'control replication invariant failed')
            require(cell['port_directions']['Z'] == 'output', 'control replication invariant failed')
            require(cell['connections']['Z'][0] in aliases, 'control replication invariant failed')
    require(set(old['netnames']) <= set(new['netnames']), 'control replication invariant failed')
    for name, net in old['netnames'].items():
        require(new['netnames'][name] == net, name)
    for name in set(new['netnames']) - set(old['netnames']):
        net = new['netnames'][name]
        require(len(net['bits']) == 1 and net['bits'][0] in aliases, name)
        require(net['attributes'] == {} and net['hide_name'] == 1, 'control replication invariant failed')
    driven = collections.defaultdict(list)
    used = collections.Counter()
    for name, cell in new['cells'].items():
        for port, bits in cell['connections'].items():
            for bit in bits:
                if cell['port_directions'][port] == 'output':
                    driven[bit].append((name, port))
                else:
                    used[bit] += 1
    require(all(len(endpoints) == 1 for endpoints in driven.values()), 'multiple drivers')
    require(set(aliases) <= set(driven), 'control replication invariant failed')
    require(all(used[bit] > 0 for bit in aliases), 'unused clone')
    require(max((used[bit] for bit in aliases), default=0) <= manifest['cap'], 'control replication invariant failed')
    # Packing needs independent LUT pairs for each PFUMX, including the original.
    lut5_input_owners = collections.defaultdict(list)
    for name, cell in new['cells'].items():
        if cell['type'] == 'PFUMX':
            for port in ('ALUT', 'BLUT'):
                bit = cell['connections'][port][0]
                driver, driver_port = driven[bit][0]
                require(new['cells'][driver]['type'] == 'LUT4' and driver_port == 'Z', 'control replication invariant failed')
                lut5_input_owners[driver].append((name, port))
    require(all(len(owners) == 1 for owners in lut5_input_owners.values()), 'shared private LUT5 inputs')
    result = {'status': 'pass', 'scope': 'structural combinational-equivalence proof',
              'input_sha256': sha(args.input), 'output_sha256': sha(args.output),
              'manifest_sha256': sha(args.manifest),
              'original_cells_preserved': len(old['cells']),
              'added_cells': len(origins), 'new_equivalent_nets': len(aliases),
              'all_original_cell_ports_canonicalize_identically': True,
              'all_clone_parameters_and_inputs_match_originals': True,
              'all_state_io_clocks_resets_and_initialization_unchanged': True,
              'timing_constraints_unchanged': True,
              'physical_timing_improvement_not_established': True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(f"SWAY_CONTROL_REPLICATION_PASS original_cells={len(old['cells'])} "
          f"added_cells={len(origins)} output_sha256={result['output_sha256']}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    audit(parser.parse_args())
