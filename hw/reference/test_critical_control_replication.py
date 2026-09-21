#!/usr/bin/env python3
"""Check the selected control repair and unchanged independent proof checker."""

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import check_control_replication
import replicate_critical_controls


def cell(kind, inputs, outputs, params=None):
    connections = dict(inputs, **outputs)
    return {'hide_name': 0, 'type': kind, 'parameters': params or {}, 'attributes': {},
            'port_directions': {port: 'output' if port in outputs else 'input'
                                for port in connections}, 'connections': connections}


class TestCriticalControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.args = SimpleNamespace(input=root/'old.json', output=root/'new.json',
                                    manifest=root/'manifest.json', report=root/'audit.json', top='mkTop')
        cells = {}
        for index, name in enumerate(replicate_critical_controls.DRIVERS):
            cells[name] = cell('LUT4', {'A': [1 if index == 0 else 9 + index],
                                      'B': [2], 'C': ['0'], 'D': ['0']},
                               {'Z': [10 + index]}, {'INIT': '1000100010001000'})
            for sink in range(24):
                cells[f'reg{index}_{sink}'] = cell(
                    'TRELLIS_FF', {'CLK': [3], 'CE': ['1'], 'DI': [1], 'LSR': [10 + index]},
                    {'Q': [100 + index * 24 + sink]}, {'REGSET': 'RESET'})
        doc = {'modules': {'mkTop': {'attributes': {},
                                    'ports': {'input': {'direction': 'input', 'bits': [1, 2, 3]}},
                                    'cells': cells, 'netnames': {}}}}
        self.args.input.write_text(json.dumps(doc))
        with contextlib.redirect_stdout(io.StringIO()):
            replicate_critical_controls.run(self.args)
        self.original = json.loads(self.args.output.read_text())

    def audit(self):
        manifest = json.loads(self.args.manifest.read_text())
        manifest['output_sha256'] = hashlib.sha256(self.args.output.read_bytes()).hexdigest()
        self.args.manifest.write_text(json.dumps(manifest))
        with contextlib.redirect_stdout(io.StringIO()):
            check_control_replication.audit(self.args)

    def test_selected_reverse_order_cap_and_proof(self):
        self.audit()
        manifest = json.loads(self.args.manifest.read_text())
        self.assertEqual(manifest['processing_order'], list(reversed(replicate_critical_controls.DRIVERS)))
        self.assertEqual(set(manifest['clone_origins'].values()), set(replicate_critical_controls.DRIVERS))
        for driver in manifest['selected_drivers'].values():
            self.assertLessEqual(max(driver['final_output_fanout'].values()), 8)

    def mutation_rejected(self, mutate):
        doc = copy.deepcopy(self.original)
        mutate(doc['modules']['mkTop']['cells'])
        self.args.output.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            self.audit()

    def test_wrong_clone_truth_table_rejected(self):
        name = next(name for name in self.original['modules']['mkTop']['cells']
                    if '$critical_fanout$' in name)
        self.mutation_rejected(lambda cells: cells[name]['parameters'].update(INIT='0' * 16))

    def test_wrong_sink_reconnection_rejected(self):
        self.mutation_rejected(lambda cells: cells['reg2_23']['connections'].update(LSR=[2]))

    def test_state_parameter_change_rejected(self):
        self.mutation_rejected(lambda cells: cells['reg2_23']['parameters'].update(REGSET='SET'))


class TestB2CriticalControl(unittest.TestCase):
    def test_only_reported_driver_is_split_and_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = SimpleNamespace(input=root/'old.json', output=root/'new.json',
                                   manifest=root/'manifest.json', report=root/'audit.json',
                                   top='mkTop', variant='B2')
            name, = replicate_critical_controls.DRIVERS_BY_VARIANT['B2']
            cells = {name: cell('LUT4', {'A': ['0'], 'B': ['0'], 'C': [1], 'D': [2]},
                                {'Z': [10]}, {'INIT': '0000111100000000'}),
                     'unselected': cell('LUT4', {'A': [1], 'B': [2], 'C': ['0'], 'D': ['0']},
                                        {'Z': [11]}, {'INIT': '1000100010001000'})}
            for index in range(36):
                cells[f'selected_sink_{index}'] = cell(
                    'TRELLIS_FF', {'CLK': [3], 'CE': [11], 'DI': [1], 'LSR': [10]},
                    {'Q': [100 + index]}, {'REGSET': 'RESET'})
            doc = {'modules': {'mkTop': {'attributes': {},
                                        'ports': {'input': {'direction': 'input', 'bits': [1, 2, 3]}},
                                        'cells': cells, 'netnames': {}}}}
            args.input.write_text(json.dumps(doc))
            with contextlib.redirect_stdout(io.StringIO()):
                replicate_critical_controls.run(args)
                check_control_replication.audit(args)
            manifest = json.loads(args.manifest.read_text())
            self.assertEqual(set(manifest['selected_drivers']), {name})
            self.assertEqual(set(manifest['clone_origins'].values()), {name})
            self.assertEqual(manifest['cloned_cell_types'], {'LUT4': 4})
            self.assertEqual(sorted(manifest['selected_drivers'][name]['final_output_fanout'].values()),
                             [4, 8, 8, 8, 8])
            result = json.loads(args.output.read_text())['modules']['mkTop']['cells']
            self.assertEqual(result['unselected'], cells['unselected'])
            self.assertEqual(sum(c['type'] == 'TRELLIS_FF' for c in result.values()), 36)
            self.assertEqual(json.loads(args.report.read_text())['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
