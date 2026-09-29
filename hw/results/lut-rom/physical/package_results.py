#!/usr/bin/env python3
"""Package a strict same-process live-verified PASS, preserving earlier failed runs."""
import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


TARGETS = {'$glbnet$clocks_pll_clk_100mhz': 100,
           '$glbnet$CLK_clk_25mhz$TRELLIS_IO_IN': 25}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    with path.open() as stream:
        return json.load(stream)


def checked_file(path, expected):
    require(path.is_file() and digest(path) == expected, 'Missing/changed input: ' + str(path))


def verify_commands(commands, directory):
    for command in commands:
        require(command['exit_code'] == 0 and isinstance(command['command'], list)
                and bool(command['command']), 'A required executed command failed or is missing')
        require(command.get('execution', 'executed') == 'executed', 'Unexpected command reuse claim')
        checked_file(directory / command['log'], command['log_sha256'])


def verify_sources(sources, repo, blueyosys):
    for name, expected in sources.items():
        prefix, relative = name.split('/', 1)
        require(prefix in {'sway', 'blueyosys'}, 'Unknown source namespace: ' + prefix)
        checked_file((repo if prefix == 'sway' else blueyosys) / relative, expected)


def check_clocks(clocks):
    require(set(clocks) == set(TARGETS), 'Missing or unexpected reported clock')
    for name, target in TARGETS.items():
        clock = clocks[name]
        require(clock['constraint'] == target and math.isfinite(clock['achieved'])
                and clock['achieved'] >= target, 'Strict clock target failed: ' + name)


def pnr_arguments(command):
    argv = command['command']
    for name, value in (('--package', 'CABGA381'), ('--speed', '6'), ('--seed', '1'),
                        ('--freq', '100'), ('--router', 'router1')):
        require(argv.count(name) == 1 and argv[argv.index(name) + 1] == value,
                'Unexpected physical argument: ' + name)
    require('--85k' in argv and '--tmg-ripup' in argv, 'Required device/router options missing')
    require(not set(argv) & {'--timing-allow-fail', '--ignore-loops', '--ignore-rel-clk'},
            'Strict timing override in physical command')


def verify_live_proof(path, expected_sha, embedded, phase, live_checker, reference_dir):
    checked_file(path, expected_sha)
    proof = read_json(path)
    require(proof == embedded and proof['status'] == 'pass' and proof['phase'] == phase
            and proof['inputs_unchanged'] is True, 'Live graph/placement proof did not pass')
    expected_paths = {'packed_graph': reference_dir / 'packed.json',
                      'observed_bels': reference_dir / 'observed_bels.json', 'hook': live_checker.resolve()}
    require(set(proof['input_paths']) == set(proof['input_sha256']) == set(expected_paths),
            'Unexpected live proof input set')
    for name, expected_path in expected_paths.items():
        require(Path(proof['input_paths'][name]).resolve() == expected_path.resolve(), 'Wrong live proof input: ' + name)
        checked_file(expected_path, proof['input_sha256'][name])
    topology, placement = proof['topology'], proof['placement']
    require(topology['status'] == placement['status'] == 'pass'
            and topology['cells'] == placement['exact_bel_count'] == placement['unique_bels'] == 104869
            and topology['bijective_wires'] == 123513 and placement['no_cell_moved'] is True,
            'Live graph/BEL identity incomplete')
    bels = read_json(reference_dir / 'observed_bels.json')
    require(len(bels) == 104869 and len({item['bel'] for item in bels.values()}) == len(bels),
            'Reference BEL inventory incomplete or overlapping')
    actual_bels = {name: item['bel'] for name, item in bels.items()}
    require(hashlib.sha256(json.dumps(actual_bels, sort_keys=True).encode()).hexdigest()
            == placement['actual_bels_sha256'], 'Live BEL digest differs from observed placement')
    require(topology['top_scalar_io_pads'] == 11, 'Live top I/O pad boundary incomplete')
    require('physical_settings_evidence' in proof and 'live_settings' not in proof,
            'Live proof must describe the pinned API settings limitation honestly')
    require(proof['clock_targets_mhz'] == TARGETS, 'Live clock targets differ')
    return proof


def load_physical_checker(repo):
    reference = repo / 'hw/reference'
    sys.path.insert(0, str(reference))
    spec = importlib.util.spec_from_file_location('packaging_physical_checker', reference / 'check_physical.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--recovered', type=Path, required=True)
    parser.add_argument('--mapped', type=Path, required=True)
    parser.add_argument('--blueyosys', type=Path, required=True)
    parser.add_argument('--restoration', type=Path, required=True)
    parser.add_argument('--recovery-script', type=Path, required=True)
    parser.add_argument('--live-checker', type=Path, required=True)
    parser.add_argument('--independent-review', type=Path)
    parser.add_argument('--failed-recovery', type=Path, action='append', default=[])
    args = parser.parse_args()
    repo, original, recovered, blueyosys = (getattr(args, name).resolve()
                                          for name in ('repo', 'original', 'recovered', 'blueyosys'))
    old = read_json(original / 'report.json')
    new = read_json(recovered / 'report.json')
    mapped = read_json(args.mapped)
    restoration = read_json(args.restoration)
    fixed_inputs = {original / 'report.json': digest(original / 'report.json'),
                    recovered / 'report.json': digest(recovered / 'report.json'),
                    args.mapped: digest(args.mapped), args.restoration: digest(args.restoration),
                    args.recovery_script: digest(args.recovery_script), args.live_checker: digest(args.live_checker)}
    require(old['status'] == 'fail', 'Original failed run must remain recorded as failed')
    require(new['status'] == 'pass' and new['inputs_unchanged'] is True
            and new['source_hashes_unchanged'] is True, 'Recovery is not a frozen-input PASS')
    require(old['source_hashes_unchanged'] is True and new['source_sha256'] == old['source_sha256'],
            'Source identity differs between runs')
    verify_sources(new['source_sha256'], repo, blueyosys)
    require(len(old['commands']) == 2 and len(new['commands']) == 1, 'Unexpected executed-stage count')
    verify_commands(old['commands'], original)
    verify_commands(new['commands'], recovered)
    require('physical-netlist' in old['commands'][0]['command'], 'Original fresh synthesis command missing')
    require('--no-route' in old['commands'][1]['command'], 'Original placement-only command missing')
    command = new['commands'][0]
    pnr_arguments(command)
    argv = command['command']
    require({'--pre-place', '--pre-route'} <= set(argv)
            and not set(argv) & {'--no-pack', '--no-place', '--no-route', '--pack-only', '--write'},
            'Expected one process for full pack, exact BEL placement, live proof, and strict route')
    for flag, path in (('--json', original / 'build/mkTop.json'),
                       ('--pre-place', recovered / 'replay_observed_bels.py'),
                       ('--pre-route', recovered / 'verify_live_before_route.py'),
                       ('--report', recovered / 'nextpnr.json'), ('--log', recovered / 'nextpnr.log'),
                       ('--textcfg', recovered / 'mkTop.config'), ('--lpf', original / 'build/ulx3s.lpf')):
        require(argv.count(flag) == 1 and Path(argv[argv.index(flag) + 1]).resolve() == path,
                'Wrong full implementation input/output: ' + flag)
    expected_command = [new['nextpnr']['path'], '--85k', '--package', 'CABGA381', '--speed', '6',
        '--seed', '1', '--freq', '100', '--router', 'router1', '--tmg-ripup', '--lpf',
        str(original / 'build/ulx3s.lpf'), '--detailed-timing-report', '--json',
        str(original / 'build/mkTop.json'), '--pre-place', str(recovered / 'replay_observed_bels.py'),
        '--pre-route', str(recovered / 'verify_live_before_route.py'), '--report',
        str(recovered / 'nextpnr.json'), '--log', str(recovered / 'nextpnr.log'),
        '--textcfg', str(recovered / 'mkTop.config')]
    require(argv == expected_command, 'Full implementation argv differs from the reviewed strict flow')
    require(new['routing_complete'] is True, 'Full implementation did not complete')
    require(new['clock_targets_mhz'] == old['clock_targets_mhz'] == {'core': 100, 'uart': 25},
            'Clock targets changed')
    check_clocks(new['reported_clocks'])
    timing = read_json(recovered / 'nextpnr.json')
    require(timing['fmax'] == new['reported_clocks'] and timing['utilization'] == new['routed_utilization'],
            'Summary differs from raw routed results')
    require(set(new['clocks']) == {'core', 'uart'} and all(c['pass'] is True for c in new['clocks'].values()),
            'Detailed strict clock assessment did not pass')
    for name, target in (('core', 100), ('uart', 25)):
        c = new['clocks'][name]
        require(c['required_mhz'] == c['constraint_mhz'] == target
                and math.isfinite(c['achieved_mhz']) and c['achieved_mhz'] >= target,
                'Detailed strict clock constraint differs')
    for report, directory in ((old, original), (new, recovered)):
        for name, expected in report['evidence_sha256'].items():
            checked_file(directory / name, expected)
    for name, expected in new['watched_input_sha256'].items():
        checked_file(Path(name), expected)
    checked_file(original / 'report.json', new['original_report_sha256'])
    require(new['watched_input_sha256'].get(str(args.recovery_script.resolve())) == digest(args.recovery_script),
            'Final report does not bind the live runner')
    checked_file(args.live_checker, new['live_checker_sha256'])
    require(Path(new['original_report']).resolve() == original / 'report.json', 'Wrong original report')
    provenance = new['synthesis_provenance']
    require(provenance['reexecuted'] is False and provenance['command'] == old['commands'][0]
            and provenance['source_identity_verified'] is True
            and provenance['netlist_sha256'] == old['netlist_sha256'], 'Invalid original synthesis provenance')
    checked_file(original / 'build/mkTop.json', old['netlist_sha256'])
    for name, expected in old['generated_rtl_sha256'].items():
        checked_file(original / 'build' / name, expected)
    checked_file(original / 'build/ulx3s.lpf', old['constraints']['sha256'])
    checked_file(original / 'placed.json', new['observed_placement']['partial_sha256'])
    require(new['observed_placement']['partial_file_is_not_a_complete_checkpoint'] is True,
            'Truncated original incorrectly treated as a checkpoint')
    try:
        read_json(original / 'placed.json')
    except json.JSONDecodeError:
        pass
    else:
        raise RuntimeError('Original placement artifact is not the recorded truncated dump')
    require(restoration['status'] == 'pass', 'Tool restoration verification failed')
    for name in ('bsc', 'yosys', 'nextpnr'):
        restored = restoration['tools'][name]
        require(all(restored[field] == old['tools'][name][field] for field in ('sha256', 'version')),
                'Restored tool identity differs: ' + name)
        checked_file(Path(restored['path']), restored['sha256'])
    require(all(new['nextpnr'][field] == old['tools']['nextpnr'][field] for field in ('sha256', 'version')),
            'Recovered route used a different nextpnr')
    head = subprocess.check_output(['git', '-C', str(blueyosys), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == old['blueyosys_commit'] == new['blueyosys_commit'] == restoration['blueyosys']['commit'],
            'blueYosys revision differs')
    physical = load_physical_checker(repo)
    require(physical.source_hashes(repo / 'hw', blueyosys) == new['source_sha256'],
            'Complete current source inventory differs from validated inventory')
    physical.verify_transform_evidence(original / 'build', new['source_sha256'], old['netlist_sha256'])
    recovery_pack_report = Path(new['recovery_pack_report']).resolve()
    checked_file(recovery_pack_report, new['recovery_pack_report_sha256'])
    reference_dir = recovery_pack_report.parent
    reference = read_json(recovery_pack_report)
    require(reference['status'] == 'fail', 'Reference recovery failure must remain separately recorded')
    require(reference['inputs_unchanged'] is True and reference['source_hashes_unchanged'] is True
            and reference['source_sha256'] == new['source_sha256'], 'Packed reference source/input identity differs')
    require(reference['fresh_pack_topology']['status'] == new['packed_to_observed_topology']['status'] == 'pass'
            and reference['fresh_pack_topology'] == new['packed_to_observed_topology'], 'Packed/observed graph proof differs')
    checked_file(reference_dir / 'packed.json', reference['fresh_packed_sha256'])
    require(new['netlist_sha256'] == old['netlist_sha256'], 'Live run mapped netlist differs')
    live = verify_live_proof(recovered / 'live-placement-proof.json', new['live_placement_proof_sha256'],
                            new['live_placement_proof'], 'pre-route', args.live_checker, reference_dir)
    checked_file(recovered / 'replay_observed_bels.py', new['placement_hook_sha256'])
    checked_file(recovered / 'mkTop.config', new['config_sha256'])
    require((recovered / 'mkTop.config').stat().st_size > 0, 'Empty routed configuration')
    settings = new['routing_settings']
    require(settings['fresh_process_no_checkpoint_settings_imported'] is True
            and settings['large_optional_json_export'] is False
            and settings['timing_allow_fail'] is False and settings['ignore_loops'] is False,
            'Unexpected implementation flow or relaxed timing')
    console = (recovered / command['log']).read_text()
    require('SWAY_LIVE_PLACEMENT_PASS phase=pre-route' in console
            and 'Program finished normally.' in console, 'Live proof or normal completion marker missing')
    failed_runs = []
    for directory in {reference_dir, *(p.resolve() for p in args.failed_recovery)}:
        failed = read_json(directory / 'report.json')
        require(failed['status'] == 'fail', 'Failure-history input is not a failed run')
        fixed_inputs[directory / 'report.json'] = digest(directory / 'report.json')
        failed_runs.append((directory, failed))
    require(mapped['status'] == 'pass' and mapped['engine_count'] == 17
            and mapped['coefficient_addresses_checked'] == 139264 and mapped['mismatched_addresses'] == 0,
            'Exhaustive mapped coefficient check failed')
    require(mapped['input_sha256']['netlist'] == old['netlist_sha256'], 'Mapped coefficient netlist differs')
    checked_file(original / 'build/mkTop.v', mapped['input_sha256']['generated_rtl'])
    checked_file(repo / 'hw/reference/check_mapped_weights.py', mapped['input_sha256']['checker'])
    for name, expected in mapped['binary_sha256'].items():
        checked_file(repo / 'hw/model/export' / name, expected)
    require(all(e['intermediate_memories'] == 0 and e['intermediate_registers'] == 0
                and e['other_engine_or_control_inputs'] == 0 for e in mapped['engines']),
            'A coefficient cone violates the combinational dedicated LUT contract')
    require(new['affine_weight_mapping']['status'] == 'pass'
            and new['affine_weight_mapping']['engine_count'] == 17
            and new['packed_utilization']['DP16KD']['used'] == 2
            and new['routed_utilization']['DP16KD']['used'] == 2,
            'Unexpected affine mapping or block SRAM use')
    review = None
    if args.independent_review:
        fixed_inputs[args.independent_review] = digest(args.independent_review)
        review = read_json(args.independent_review)
        require(review['status'] == 'review_pass'
                and review['runner_sha256'] == digest(args.recovery_script)
                and review['physical_report_sha256'] == digest(recovered / 'report.json')
                and review['live_proof_sha256'] == digest(recovered / 'live-placement-proof.json'),
                'Independent review does not bind this runner/final report/live proof')
        for name, expected in review['reviewed_input_sha256'].items():
            checked_file(Path(name), expected)

    output = repo / 'hw/results/lut-rom'
    destination = output / 'physical'
    require(not destination.exists(), 'Refusing to mix with an existing physical evidence directory')
    require(not (output / 'physical-summary.json').exists(), 'Physical summary already exists')
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='recovered-physical-', dir=output))
    inventory = {}
    def save(source, relative):
        payload = source.read_bytes()
        stored = relative + ('.gz' if len(payload) > 200000 else '')
        data = gzip.compress(payload, mtime=0) if stored.endswith('.gz') else payload
        if stored.endswith('.gz'):
            require(gzip.decompress(data) == payload, 'Compression round-trip differs')
        target = staging / stored
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        inventory[relative] = {'stored_path': 'physical/' + stored, 'original_bytes': len(payload),
                               'original_sha256': digest(source), 'stored_bytes': len(data),
                               'stored_sha256': digest(target)}
    try:
        for name in ('report.json', 'synthesis.log', 'placement.log', 'placement.console.log', 'placement.json',
                     'place_core_reset.py', 'build/ulx3s.lpf', 'build/mkTop.yosys.json', 'build/mkTop.yosys.rpt',
                     'build/reset_merge.json', 'build/address_replicas.json', 'build/page_decoders.json',
                     'build/page_decoder_audit.json'):
            save(original / name, 'original/' + name)
        for name in ('report.json', 'nextpnr.log', 'nextpnr.console.log', 'nextpnr.json',
                     'replay_observed_bels.py', 'verify_live_before_route.py', 'live-placement-proof.json'):
            save(recovered / name, 'live/' + name)
        save(reference_dir / 'observed_bels.json', 'observed-bels.json')
        for directory, failed in sorted(failed_runs, key=lambda pair: str(pair[0])):
            label = directory.name
            save(directory / 'report.json', 'failed-recovery/' + label + '/report.json')
            logs = {item['log'] for item in failed.get('commands', [])}
            logs.update(p.name for p in directory.glob('*.log'))
            for name in sorted(logs):
                save(directory / name, 'failed-recovery/' + label + '/' + name)
        save(args.recovery_script, 'live_runner.py')
        save(args.recovery_script.parent / 'recover_placement.py', 'recover_placement.py')
        save(args.live_checker, 'live_checker.py')
        save(Path(__file__), 'package_results.py')
        save(args.restoration, 'tool-restoration.json')
        save(args.mapped, 'mapped-weight-values.json')
        if args.independent_review:
            save(args.independent_review, 'independent-recovery-review.json')
        manifest = {'status': 'pass', 'compression': 'gzip with mtime=0 for source files larger than 200000 bytes',
                    'files': inventory, 'large_artifact_identity': {
                        'original_mapped_netlist': {'sha256': old['netlist_sha256'], 'content_included': False},
                        'original_truncated_placement_dump': {'sha256': new['observed_placement']['partial_sha256'],
                            'complete_checkpoint': False, 'content_included': False},
                        'reference_packed_graph': {'sha256': reference['fresh_packed_sha256'], 'content_included': False},
                        'live_implementation': {'full_checkpoint_export_requested': False,
                            'identity_evidence': 'live/live-placement-proof.json',
                            'post_route_identity_checked': False},
                        'routed_configuration': {'sha256': new['config_sha256'], 'content_included': False}}}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        summary = {'status': 'pass', 'scope': 'Original fresh synthesis; one nextpnr process freshly packs, replays every observed BEL, verifies the live graph and placement, then performs strict routing. No large checkpoint dump is used or claimed complete. Earlier failed reports remain unchanged. No FPGA programming or board-operation claim.',
            'board': old['board'], 'top': old['top'], 'parallelism_divisor': 4,
            'speed_grade': old['speed_grade'], 'seed': old['seed'], 'router': old['router'],
            'blueyosys_commit': head, 'tools': restoration['tools'],
            'synthesis_original': {**provenance, 'status': 'pass', 'synthesis_executed_once': True},
            'original_physical_run': {'status': old['status'], 'error': old['error'],
                'report_sha256': digest(original / 'report.json'), 'placement_command_exit_code': 0,
                'placement_dump_complete': False, 'original_report_relabelled': False},
            'placement_live_verified': {'status': 'pass', 'proof': live,
                'reference_packed_topology': new['packed_to_observed_topology'],
                'proof_phase': 'pre-route', 'large_checkpoint_export_requested': False},
            'strict_routed_timing': {'status': 'pass', 'command': command,
                'timing_report_sha256': digest(recovered / 'nextpnr.json'),
                'config_sha256': new['config_sha256'], 'config_bytes': (recovered / 'mkTop.config').stat().st_size},
            'failed_recovery_runs': [{'directory': directory.name, 'status': failed['status'],
                'error': failed.get('error'), 'report_sha256': digest(directory / 'report.json')}
                for directory, failed in failed_runs],
            'clock_targets_mhz': new['clock_targets_mhz'], 'reported_clocks': new['reported_clocks'],
            'packed_utilization': new['packed_utilization'], 'routed_utilization': new['routed_utilization'],
            'affine_weight_block_rams': 0, 'scan_state_block_rams': 2, 'dedicated_affine_engines': 17,
            'mapped_weight_addresses_checked': 139264, 'mapped_weight_mismatches': 0,
            'netlist_sha256': old['netlist_sha256'], 'source_sha256': new['source_sha256'],
            'source_hashes_unchanged': True, 'publication_source_identity_verified': True,
            'recovered_report_sha256': digest(recovered / 'report.json'), 'mapped_report_sha256': digest(args.mapped),
            'restoration_manifest_sha256': digest(args.restoration),
            'independent_review_included': review is not None, 'evidence': inventory,
            'physical_manifest': {'path': 'physical/manifest.json', 'sha256': digest(staging / 'manifest.json')}}
        verify_sources(new['source_sha256'], repo, blueyosys)
        checked_file(original / 'report.json', new['original_report_sha256'])
        for path, expected in fixed_inputs.items():
            checked_file(path, expected)
        for name, expected in new['evidence_sha256'].items():
            checked_file(recovered / name, expected)
        staging.rename(destination)
        (output / 'physical-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        shutil.copyfile(args.mapped, output / 'mapped-weight-values.json')
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(json.dumps({'status': 'pass', 'summary': str(output / 'physical-summary.json'),
                      'clocks': new['reported_clocks']}))


if __name__ == '__main__':
    main()
