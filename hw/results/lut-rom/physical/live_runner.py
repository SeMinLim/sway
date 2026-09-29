#!/usr/bin/env python3
"""Route the unchanged mapped design with exactly the observed placement in one nextpnr process."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys

BASE = Path(__file__).resolve().parent
REF = BASE / 'sway-lut-decoder-constants/hw/reference'
sys.path.insert(0, str(REF))
import check_physical as physical
from recover_placement import graph_proof, require


def main():
    old = BASE / 'lut-physical-decoder-constants'
    replay = BASE / 'lut-physical-recovered4'
    out = BASE / 'lut-physical-live'
    require(not out.exists() or not any(out.iterdir()), 'Live output directory must be new or empty')
    out.mkdir(parents=True, exist_ok=True)
    prior = json.loads((old / 'report.json').read_text())
    preserved = json.loads((replay / 'report.json').read_text())
    sources = physical.source_hashes(REF.parent, BASE / 'blueyosys')
    require(sources == prior['source_sha256'], 'Current source freeze differs')
    sha = physical.sha256
    nextpnr = BASE / 'tools/oss-cad-suite/bin/nextpnr-ecp5'
    tool = physical.tool_info(str(nextpnr), '--version')
    require(all(tool[k] == prior['tools']['nextpnr'][k] for k in ('sha256', 'version')), 'Restored binary identity differs')
    netlist, rtl, lpf = (old / 'build' / name for name in ('mkTop.json', 'mkTop.v', 'ulx3s.lpf'))
    require(sha(netlist) == prior['netlist_sha256'], 'Mapped netlist changed')
    require(sha(lpf) == prior['constraints']['sha256'], 'LPF changed')
    require(prior['source_hashes_unchanged'] and prior['commands'][0]['exit_code'] == 0, 'Original synthesis did not pass')
    require(sha(old / 'synthesis.log') == prior['commands'][0]['log_sha256'], 'Synthesis log changed')
    physical.verify_transform_evidence(old / 'build', sources, sha(netlist))
    packed = replay / 'packed.json'
    require(sha(packed) == preserved['fresh_packed_sha256'], 'Proven packed input changed')
    partial = old / 'placed.json'
    require(sha(partial) == preserved['observed_placement']['partial_sha256'], 'Observed placement prefix changed')
    text = partial.read_text()
    require(text.count('"netnames":') == 1, 'Ambiguous complete-cell prefix')
    observed = json.loads(text[:text.index('"netnames":')] + '"netnames": {}}}}')['modules']['top']
    pack_design = json.loads(packed.read_text())['modules']['top']
    topology = graph_proof(pack_design, observed)
    del pack_design
    bel_inventory = replay / 'observed_bels.json'
    bels = json.loads(bel_inventory.read_text())
    require({n: {'type': c['type'], 'bel': c['attributes']['NEXTPNR_BEL']}
             for n, c in observed['cells'].items()} == bels, 'BEL inventory differs from observed prefix')
    del observed
    placement_hook = out / 'replay_observed_bels.py'
    shutil.copyfile(replay / 'replay_observed_bels.py', placement_hook)
    live_hook = BASE / 'verify_live_placement.py'
    require(live_hook.is_file(), 'Live proof hook missing')
    wrapper = out / 'verify_live_before_route.py'
    wrapper.write_text("import os\nfrom pathlib import Path\n"
                      + "os.environ['SWAY_LIVE_PROOF_OUTPUT'] = " + repr(str(out / 'live-placement-proof.json')) + "\n"
                      + "exec(compile(Path(" + repr(str(live_hook)) + ").read_text(), " + repr(str(live_hook)) + ", 'exec'))\n")
    paths = [old / 'report.json', replay / 'report.json', partial, packed, bel_inventory, netlist, rtl, lpf,
             placement_hook, live_hook, wrapper, Path(__file__), BASE / 'recover_placement.py']
    watched = {p: sha(p) for p in paths}
    result = {'status': 'fail', 'started_utc': datetime.now(timezone.utc).isoformat(), 'commands': [],
              'flow': 'One nextpnr process: fresh pack, exact observed BEL placement, live graph/BEL proof, strict routing',
              'original_report': str(old / 'report.json'), 'original_report_sha256': sha(old / 'report.json'),
              'recovery_pack_report': str(replay / 'report.json'), 'recovery_pack_report_sha256': sha(replay / 'report.json'),
              'source_sha256': sources, 'nextpnr': tool, 'blueyosys_commit': physical.git_head(BASE / 'blueyosys'),
              'clock_targets_mhz': {'core': 100, 'uart': 25}, 'netlist_sha256': sha(netlist),
              'synthesis_provenance': {'reexecuted': False, 'command': prior['commands'][0],
                                      'netlist_sha256': sha(netlist), 'source_identity_verified': True},
              'observed_placement': preserved['observed_placement'], 'packed_to_observed_topology': topology,
              'live_checker_sha256': sha(live_hook), 'placement_hook_sha256': sha(placement_hook),
              'affine_weight_mapping': deepcopy(prior['affine_weight_mapping'])}
    try:
        command = [str(nextpnr), '--85k', '--package', 'CABGA381', '--speed', '6', '--seed', '1',
                   '--freq', '100', '--router', 'router1', '--tmg-ripup', '--lpf', str(lpf),
                   '--detailed-timing-report', '--json', str(netlist), '--pre-place', str(placement_hook),
                   '--pre-route', str(wrapper), '--report', str(out / 'nextpnr.json'),
                   '--log', str(out / 'nextpnr.log'), '--textcfg', str(out / 'mkTop.config')]
        result['routing_settings'] = {'fresh_process_no_checkpoint_settings_imported': True,
                'router': 'router1', 'timing_ripup_requested': True, 'frequency_mhz': 100,
                'speed_grade': 6, 'seed': 1, 'timing_allow_fail': False, 'ignore_loops': False,
                'large_optional_json_export': False}
        route = physical.run_command(command, out / 'nextpnr.console.log', REF.parent)
        result['commands'].append(route)
        proof = json.loads((out / 'live-placement-proof.json').read_text())
        result['live_placement_proof'] = proof
        result['live_placement_proof_sha256'] = sha(out / 'live-placement-proof.json')
        require(proof['status'] == 'pass', 'Live topology/BEL proof failed')
        timing = json.loads((out / 'nextpnr.json').read_text())
        result['reported_clocks'] = timing.get('fmax', {})
        result['packed_utilization'] = physical.packed_utilization((out / 'nextpnr.log').read_text())
        rounds = re.findall(r'(\d+) arcs ripped up due to negative slack WNS=([-0-9.]+)ns TNS=([-0-9.]+)ns',
                            (out / 'nextpnr.log').read_text())
        result['routing_settings']['timing_ripup_rounds'] = [
                {'arcs_ripped_up': int(a), 'wns_ns': float(w), 'tns_ns': float(t)} for a, w, t in rounds]
        require(route['exit_code'] == 0, 'Strict place/route/timing command failed')
        config = out / 'mkTop.config'
        require(config.is_file() and config.stat().st_size > 0, 'No routed configuration')
        result['clocks'] = physical.verify_clocks(timing)
        require(all(c['pass'] for c in result['clocks'].values()), 'Exact 100 MHz core and 25 MHz UART constraints must both pass')
        result['routed_utilization'] = timing['utilization']
        physical.verify_state_block_ram_usage(result['affine_weight_mapping'], 'routed_design',
                timing['utilization'].get('DP16KD', {}).get('used'), 'nextpnr.json utilization')
        result['affine_weight_mapping']['status'] = 'pass'
        result['config_sha256'] = sha(config)
        result['routing_complete'] = True
        result['status'] = 'pass'
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        result['watched_input_sha256'] = {str(p): h for p, h in watched.items()}
        result['inputs_unchanged'] = all(p.is_file() and sha(p) == h for p, h in watched.items())
        result['source_hashes_unchanged'] = physical.source_hashes(REF.parent, BASE / 'blueyosys') == sources
        if not result['inputs_unchanged'] or not result['source_hashes_unchanged']:
            result['status'] = 'fail'
            result['input_error'] = 'Input or source changed'
        result['finished_utc'] = datetime.now(timezone.utc).isoformat()
        result['evidence_sha256'] = {p.name: sha(p) for p in out.iterdir() if p.is_file() and p.name != 'report.json'}
        (out / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print('SWAY_LIVE_PHYSICAL_' + result['status'].upper(), result.get('error', ''), flush=True)
    return 0 if result['status'] == 'pass' else 1

if __name__ == '__main__':
    raise SystemExit(main())
