#!/usr/bin/env python3
"""Small proof-checker negative controls, independent of full-chip timing."""
import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import check_control_replication
import replicate_controls


def cell(kind, inputs, output, params=None):
    connections = dict(inputs, **output)
    return {'hide_name': 0, 'type': kind, 'parameters': params or {}, 'attributes': {},
            'port_directions': {p: 'output' if p in output else 'input' for p in connections},
            'connections': connections}


class TestFanout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.args = SimpleNamespace(input=root/'old.json', output=root/'new.json',
                                    manifest=root/'manifest.json', report=root/'audit.json',
                                    top='mkTop', cap=64, max_clones=1000)
        cells = {
            'lut0': cell('LUT4', {'A': [2], 'B': [3], 'C': ['0'], 'D': ['0']},
                         {'Z': [5]}, {'INIT': '0110011001100110'}),
            'lut1': cell('LUT4', {'A': [2], 'B': [3], 'C': ['0'], 'D': ['0']},
                         {'Z': [6]}, {'INIT': '1001100110011001'}),
            'mux': cell('PFUMX', {'ALUT': [5], 'BLUT': [6], 'C0': [4]}, {'Z': [7]}),
        }
        for n in range(130):
            cells[f'reg{n}'] = cell('TRELLIS_FF', {'CLK': [8], 'CE': [7], 'DI': [2], 'LSR': ['0']}, {'Q': [100+n]}, {'REGSET': 'RESET'})
        doc = {'modules': {'mkTop': {'attributes': {}, 'ports': {
            'input': {'direction': 'input', 'bits': [2, 3, 4, 8]}},
            'cells': cells, 'netnames': {}}}}
        self.input_doc = copy.deepcopy(doc)
        self.args.input.write_text(json.dumps(doc))
        with contextlib.redirect_stdout(io.StringIO()):
            replicate_controls.run(self.args)
        self.original = json.loads(self.args.output.read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self):
        # Recompute manifest hash so corruption must fail structural checks.
        m = json.loads(self.args.manifest.read_text())
        m['output_sha256'] = hashlib.sha256(self.args.output.read_bytes()).hexdigest()
        self.args.manifest.write_text(json.dumps(m))
        with contextlib.redirect_stdout(io.StringIO()):
            check_control_replication.audit(self.args)

    def mutation(self, fn):
        doc = copy.deepcopy(self.original)
        fn(doc['modules']['mkTop']['cells'])
        self.args.output.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            self.verify()

    def test_pass_private_lut_pair_clones(self):
        self.verify()
        m = json.loads(self.args.manifest.read_text())
        self.assertEqual(m['cloned_cell_types'], {'LUT4': 4, 'PFUMX': 2})
        self.assertEqual(m['max_final_seed_fanout'], 64)

    def sequential_seed(self, kind='TRELLIS_FF', output='Q', clock_sink=None,
                        exposed=False):
        doc = copy.deepcopy(self.input_doc)
        module = doc['modules']['mkTop']
        cells = module['cells']
        cells['controller'] = cell(
            kind, {'CLK': [8], 'CE': ['1'], 'DI': [2], 'LSR': ['0']},
            {output: [9]}, {'REGSET': 'RESET'})
        for n in range(130):
            cells[f'reg{n}']['connections']['CE'] = [9]
        if clock_sink:
            cells['reg0']['connections'][clock_sink] = [9]
            cells['reg0']['port_directions'][clock_sink] = 'input'
        if exposed:
            module['ports']['controller_output'] = {'direction': 'output', 'bits': [9]}
        self.args.input.write_text(json.dumps(doc))
        return doc

    def test_preserve_and_record_direct_ff_ce_boundary(self):
        before = self.sequential_seed()
        with contextlib.redirect_stdout(io.StringIO()):
            replicate_controls.run(self.args)
        self.assertEqual(json.loads(self.args.output.read_text()), before)
        self.verify()
        m = json.loads(self.args.manifest.read_text())
        self.assertEqual(m['clone_origins'], {})
        self.assertEqual(m['unmodified_sequential_ce_boundaries'], [
            {'bit': 9, 'driver': ['controller', 'Q', 0], 'type': 'TRELLIS_FF',
             'before_fanout': 130, 'fanout': 130, 'sink_ports': {'CE': 130}}])

    def test_reject_other_initial_ce_drivers(self):
        for kind, output in [('OTHER_FF', 'Q'), ('TRELLIS_FF', 'QN')]:
            with self.subTest(kind=kind, output=output):
                self.sequential_seed(kind=kind, output=output)
                with self.assertRaisesRegex(ValueError, 'initial CE driver is unsupported'):
                    replicate_controls.run(self.args)

    def test_reject_sequential_ce_boundary_with_clock_sinks(self):
        for port in ('CLK', 'CLKA', 'WCK'):
            with self.subTest(port=port):
                self.sequential_seed(clock_sink=port)
                with self.assertRaisesRegex(ValueError, 'initial CE driver is unsupported'):
                    replicate_controls.run(self.args)

    def test_reject_sequential_ce_boundary_exposed_at_top(self):
        self.sequential_seed(exposed=True)
        with self.assertRaisesRegex(ValueError, 'initial CE driver is unsupported'):
            replicate_controls.run(self.args)

    def test_reject_altered_clone_truth_table(self):
        name = next(n for n,c in self.original['modules']['mkTop']['cells'].items() if '$fanout$' in n and c['type']=='LUT4')
        self.mutation(lambda c: c[name]['parameters'].update(INIT='0'*16))

    def test_reject_wrong_sink_input(self):
        self.mutation(lambda c: c['reg129']['connections'].update(CE=[2]))

    def test_reject_state_parameter_change(self):
        self.mutation(lambda c: c['reg0']['parameters'].update(REGSET='SET'))

    def test_reject_extra_state_cell(self):
        self.mutation(lambda c: c.update(unapproved_ff=copy.deepcopy(c['reg0'])))

    def test_failed_audit_removes_previous_pass_report(self):
        self.verify()
        self.assertTrue(self.args.report.exists())
        self.mutation(lambda c: c['reg0']['parameters'].update(REGSET='SET'))
        self.assertFalse(self.args.report.exists())


if __name__ == '__main__':
    unittest.main()
