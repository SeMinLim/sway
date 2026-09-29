#!/usr/bin/env python3
"""Replay every observed BEL from a truncated post-placement dump after proving a fresh packed graph identical."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent
REFERENCE = BASE / 'sway-lut-decoder-constants/hw/reference'
sys.path.insert(0, str(REFERENCE))
import check_physical as physical


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return physical.sha256(path)


def graph_proof(packed, placed):
    """Exact labelled-port graph equality, allowing only bijective wire renumbering."""
    forward, reverse = {}, {}
    def wire_pair(a, b):
        if type(a) is not int or type(b) is not int:
            require(a == b, 'Constant connection differs')
            return
        require(a not in forward or forward[a] == b, 'Packed net split in placement')
        require(b not in reverse or reverse[b] == a, 'Packed nets merged in placement')
        forward[a], reverse[b] = b, a
    require(set(packed['cells']) == set(placed['cells']), 'Cell set differs')
    for name, original in packed['cells'].items():
        actual = placed['cells'][name]
        for field in ('hide_name', 'type', 'parameters', 'port_directions'):
            require(original[field] == actual[field], 'Cell field differs: ' + name + '.' + field)
        require(set(original['connections']) == set(actual['connections']), 'Cell port set differs')
        for pin, bits in original['connections'].items():
            require(len(bits) == len(actual['connections'][pin]), 'Cell port width differs')
            for a, b in zip(bits, actual['connections'][pin]):
                wire_pair(a, b)
    require(set(packed['ports']) == set(placed['ports']), 'Top port set differs')
    for name, original in packed['ports'].items():
        actual = placed['ports'][name]
        require(original['direction'] == actual['direction'], 'Top port direction differs')
        require(len(original['bits']) == len(actual['bits']), 'Top port width differs')
        for a, b in zip(original['bits'], actual['bits']):
            wire_pair(a, b)
    return {'status': 'pass', 'cells': len(packed['cells']), 'bijective_wires': len(forward),
            'comparison': 'Every named cell, type, parameter, labelled port, connection and top port; wire IDs may only be bijectively renamed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nextpnr', required=True, type=Path)
    parser.add_argument('--blueyosys', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    original_dir = BASE / 'lut-physical-decoder-constants'
    original_report = original_dir / 'report.json'
    prior = json.loads(original_report.read_text())
    partial = original_dir / 'placed.json'
    out = args.output.resolve()
    require(not out.exists() or not any(out.iterdir()), 'Output must be empty or new')
    out.mkdir(parents=True, exist_ok=True)
    report = {'status': 'fail', 'started_utc': datetime.now(timezone.utc).isoformat(),
              'original_report': str(original_report), 'original_report_sha256': sha(original_report),
              'recovery_script_sha256': sha(Path(__file__)), 'commands': [],
              'clock_targets_mhz': {'core': 100, 'uart': 25},
              'reason': 'The original nextpnr placement exited zero but its JSON dump was truncated. Repack the unchanged mapped netlist, prove complete-cell topology identity, and constrain every BEL to the original observed placement before routing.'}
    watched = {partial: sha(partial), original_report: sha(original_report)}
    try:
        require(prior['source_hashes_unchanged'] is True, 'Original source freeze failed')
        require(prior['commands'][0]['exit_code'] == 0 and prior['commands'][1]['exit_code'] == 0,
                'Original synthesis or placement did not complete normally')
        sources = physical.source_hashes(REFERENCE.parent, args.blueyosys)
        require(sources == prior['source_sha256'], 'Current hardware or blueYosys source differs')
        report['source_sha256'] = sources
        tool = physical.tool_info(str(args.nextpnr), '--version')
        require(all(tool[k] == prior['tools']['nextpnr'][k] for k in ('sha256', 'version')),
                'Restored nextpnr differs from original')
        report['nextpnr'] = tool
        report['synthesis_provenance'] = {'original_command': prior['commands'][0],
                'original_report_sha256': sha(original_report), 'reexecuted': False,
                'netlist_sha256': prior['netlist_sha256']}
        report['blueyosys_commit'] = physical.git_head(args.blueyosys)
        require(report['blueyosys_commit'] == prior['blueyosys_commit'], 'blueYosys revision differs')
        netlist, rtl = original_dir / 'build/mkTop.json', original_dir / 'build/mkTop.v'
        require(sha(netlist) == prior['netlist_sha256'], 'Synthesized netlist changed')
        require(sha(original_dir / 'synthesis.log') == prior['commands'][0]['log_sha256'], 'Synthesis log changed')
        physical.verify_transform_evidence(original_dir / 'build', sources, sha(netlist))
        watched.update({netlist: sha(netlist), rtl: sha(rtl)})
        text = partial.read_text()
        # Parse only a syntactically complete prefix through the cells object.
        # This is a cell/BEL inventory, never used as a complete checkpoint.
        marker = '"netnames":'
        require(text.count(marker) == 1, 'Ambiguous partial JSON boundary')
        observed = json.loads(text[:text.index(marker)] + '"netnames": {}}}}')['modules']['top']
        bels = {name: cell['attributes']['NEXTPNR_BEL'] for name, cell in observed['cells'].items()}
        require(len(bels) == 104869 and len(set(bels.values())) == len(bels), 'Incomplete or overlapping BEL inventory')
        report['observed_placement'] = {'partial_sha256': sha(partial), 'cells': len(bels),
                'unique_bels': len(set(bels.values())), 'partial_file_is_not_a_complete_checkpoint': True}
        lpf = original_dir / 'build/ulx3s.lpf'
        require(sha(lpf) == prior['constraints']['sha256'], 'LPF changed')
        watched[lpf] = sha(lpf)
        common = [str(args.nextpnr), '--85k', '--package', 'CABGA381', '--speed', '6', '--seed', '1',
                  '--freq', '100', '--router', 'router1', '--tmg-ripup', '--lpf', str(lpf), '--detailed-timing-report']
        packed_path = out / 'packed.json'
        pack = physical.run_command([*common, '--json', str(netlist), '--no-place', '--no-route',
                '--write', str(packed_path), '--log', str(out / 'pack.log')], out / 'pack.console.log', REFERENCE.parent)
        report['commands'].append(pack)
        require(pack['exit_code'] == 0, 'Fresh pack failed')
        packed = json.loads(packed_path.read_text())['modules']['top']
        report['fresh_pack_topology'] = graph_proof(packed, observed)
        report['fresh_packed_sha256'] = sha(packed_path)
        cells_file = out / 'observed_bels.json'
        cells_file.write_text(json.dumps({name: {'type': observed['cells'][name]['type'], 'bel': bel}
                                       for name, bel in sorted(bels.items())}, sort_keys=True) + '\n')
        hook = out / 'replay_observed_bels.py'
        hook.write_text("import json\nfrom pathlib import Path\nimport hashlib\n"
            + "path = Path(" + repr(str(cells_file)) + ")\n"
            + "if hashlib.sha256(path.read_bytes()).hexdigest() != " + repr(sha(cells_file)) + ":\n    raise RuntimeError('BEL inventory changed')\n"
            + "observed = json.loads(path.read_text())\n"
            + "if {entry.first for entry in ctx.cells} != set(observed):\n    raise RuntimeError('Packed cell set differs')\n"
            + "for name, entry in observed.items():\n"
            + "    cell = ctx.cells[name]\n"
            + "    if cell.type != entry['type']:\n        raise RuntimeError('Cell type differs: ' + name)\n"
            + "    if 'BEL' in cell.attrs and cell.attrs['BEL'] != entry['bel']:\n        raise RuntimeError('Existing BEL differs: ' + name)\n"
            + "    if cell.bel:\n        if cell.bel != entry['bel']:\n            raise RuntimeError('Prebound BEL differs: ' + name)\n    else:\n        cell.setAttr('BEL', entry['bel'])\n"
            + "print('SWAY_OBSERVED_BELS_REPLAYED ' + str(len(observed)), flush=True)\n")
        watched.update({hook: sha(hook), cells_file: sha(cells_file), packed_path: sha(packed_path)})
        placed_path = out / 'placed.json'
        placement = physical.run_command([*common, '--json', str(netlist), '--no-route',
                '--pre-place', str(hook), '--write', str(placed_path), '--report', str(out / 'placement.json'),
                '--log', str(out / 'placement.log')], out / 'placement.console.log', REFERENCE.parent)
        report['commands'].append(placement)
        require(placement['exit_code'] == 0, 'All-BEL placement replay failed')
        actual = json.loads(placed_path.read_text())['modules']['top']
        report['replayed_topology'] = graph_proof(packed, actual)
        require({name: c['attributes']['NEXTPNR_BEL'] for name, c in actual['cells'].items()} == bels,
                'Replayed BEL placement differs from observed original')
        report['placement_recovery'] = {'status': 'pass', 'exact_bel_count': len(bels),
                'no_cell_moved': True, 'complete_json_generated_by_nextpnr': True,
                'replay_hook_sha256': sha(hook), 'bel_inventory_sha256': sha(cells_file),
                'placed_sha256': sha(placed_path)}
        report['packed_utilization'] = physical.packed_utilization((out / 'pack.log').read_text())
        report['reset_placement'] = physical.verify_reset_placement(
                (original_dir / 'placement.console.log').read_text(), placed_path)
        report['routing_settings'] = {'checkpoint': physical.verify_router_settings(placed_path, False)}
        report['affine_weight_mapping'] = deepcopy(prior['affine_weight_mapping'])
        state = physical.inspect_state_block_ram({'modules': {'top': actual}})
        require(state['pass'], 'Replayed scan-state RAM identity differs')
        report['affine_weight_mapping']['placed_dp16kd'] = state
        del packed, observed, actual
        clock_hook = out / 'route_clocks.py'
        clock_hook.write_text(physical.route_clock_hook())
        watched.update({placed_path: sha(placed_path), clock_hook: sha(clock_hook)})
        report['route_clocks'] = {'targets_mhz': physical.ROUTE_CLOCKS, 'hook_sha256': sha(clock_hook)}
        (out / 'recovery-checkpoint.json').write_text(json.dumps(report, indent=2) + '\n')
        route = physical.run_command([*common, '--json', str(placed_path), '--no-pack', '--no-place',
                '--pre-route', str(clock_hook), '--write', str(out / 'routed.json'), '--report', str(out / 'nextpnr.json'),
                '--log', str(out / 'nextpnr.log'), '--textcfg', str(out / 'mkTop.config')],
                out / 'nextpnr.console.log', REFERENCE.parent)
        report['commands'].append(route)
        timing = json.loads((out / 'nextpnr.json').read_text())
        report['reported_clocks'] = timing.get('fmax', {})
        if (out / 'routed.json').is_file():
            report['routing_settings']['actual_routed'] = physical.verify_router_settings(out / 'routed.json', True)
            report['routed_netlist_sha256'] = sha(out / 'routed.json')
        require(route['exit_code'] == 0, 'Strict routing/timing failed')
        require((out / 'mkTop.config').is_file() and (out / 'mkTop.config').stat().st_size > 0,
                'No routed configuration')
        report['clocks'] = physical.verify_clocks(timing)
        require(all(clock['pass'] for clock in report['clocks'].values()),
                'Exact 100 MHz core and 25 MHz UART constraints must both pass')
        report['routed_utilization'] = timing['utilization']
        physical.verify_state_block_ram_usage(report['affine_weight_mapping'], 'routed_design',
                timing['utilization'].get('DP16KD', {}).get('used'), 'nextpnr.json utilization')
        report['affine_weight_mapping']['status'] = 'pass'
        report['config_sha256'] = sha(out / 'mkTop.config')
        report['status'] = 'pass'
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        report['watched_input_sha256'] = {str(path): digest for path, digest in watched.items()}
        report['inputs_unchanged'] = all(path.is_file() and sha(path) == digest for path, digest in watched.items())
        report['source_hashes_unchanged'] = physical.source_hashes(REFERENCE.parent, args.blueyosys) == prior['source_sha256']
        if not report['inputs_unchanged'] or not report['source_hashes_unchanged']:
            report['status'] = 'fail'
            report['input_error'] = 'A source or watched artifact changed'
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report['evidence_sha256'] = {p.name: sha(p) for p in out.iterdir() if p.is_file() and p.name != 'report.json'}
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('SWAY_RECOVERED_PHYSICAL_' + report['status'].upper(), report.get('error', ''), flush=True)
    return 0 if report['status'] == 'pass' else 1

if __name__ == '__main__':
    raise SystemExit(main())
