"""Synthetic-only tests for frozen PTQ bundle identity and deployment guards."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch

from evaluate_ptq import integerParameters, loadCheckpoint, profileDigest
from model import EXP_KNOTS, SILU_KNOTS, createModel
from quantize import calibrateModel, exportQuantizedModel, quantizedForward
from train import fileSHA256


class EvaluatePTQTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        torch.manual_seed(90210)
        cls.config = json.loads((Path(__file__).parent / 'config' / 'model.json').read_text())
        cls.config['silu_knots'] = list(SILU_KNOTS)
        cls.config['exp_knots'] = [-4., -3., -2., -1.5, -1., -.75, -.5,
                                   -.25, -.125, -.0625, 0., 1.]
        cls.network = createModel(cls.config).eval()
        cls.features = torch.randn(4, 8, 8, 5)
        cls.profile = calibrateModel(cls.network, cls.features, maxSamples=4,
                                     batchSize=2, percentile=100)
        cls.source = {'model': cls.network.state_dict(), 'config': cls.config,
                      'mode': 'fp32', 'epoch': 2, 'best_epoch': 2,
                      'dataset_sha256': {}}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def saveSource(self, source=None, profile=None):
        path = self.directory / 'source.pt'
        torch.save(self.source if source is None else source, path)
        profile = copy.deepcopy(self.profile if profile is None else profile)
        profile['ptq_calibration'] = {
            'parameter_updates': False, 'gradients_used': False, 'test_used': False,
            'source_checkpoint_sha256': fileSHA256(path),
            'source_sha256': {name: fileSHA256(Path(__file__).parent / name)
                              for name in ['model.py', 'quantize.py']},
        }
        profilePath = self.directory / 'calibration.json'
        profilePath.write_text(json.dumps(profile))
        return path, profilePath

    def makeBundle(self):
        sourcePath, profilePath = self.saveSource()
        source, network, profile, integers = loadCheckpoint(sourcePath, profilePath)
        bundle = copy.deepcopy(source)
        bundle.update({'mode': 'ptq', 'training_mode': 'fp32',
                       'quantization_method': 'PTQ', 'ptq_format_version': 1,
                       'ptq_profile': profile,
                       'ptq_profile_sha256': profileDigest(profile),
                       'int8_parameters': integers})
        path = self.directory / 'checkpoint.pt'
        torch.save(bundle, path)
        return path, bundle, network, profile

    def test_raw_and_bundle_outputs_and_integer_exports_match(self):
        path, bundle, network, profile = self.makeBundle()
        _, restored, restoredProfile, restoredIntegers = loadCheckpoint(path)
        expected = quantizedForward(network, self.features, profile)
        actual = quantizedForward(restored, self.features, restoredProfile)
        self.assertTrue(torch.equal(expected, actual))
        manifest = exportQuantizedModel(restored, restoredProfile, self.directory / 'export')
        self.assertEqual(manifest['piecewiseApproximation']['expKnots'], self.config['exp_knots'])
        for entry in manifest['tensors']:
            array = np.fromfile(self.directory / 'export' / entry['binary'], dtype=np.int8)
            self.assertTrue(np.array_equal(array.reshape(entry['shape']),
                                           restoredIntegers[entry['parameterName']].numpy()))
        self.assertEqual(restoredIntegers.keys(), integerParameters(network, profile).keys())
        self.assertEqual(bundle['training_mode'], 'fp32')

    def test_integer_parameter_tampering_is_rejected(self):
        path, bundle, _, _ = self.makeBundle()
        value = next(iter(bundle['int8_parameters'].values()))
        value.view(-1)[0] = -128 if value.view(-1)[0] != -128 else 127
        torch.save(bundle, path)
        with self.assertRaisesRegex(ValueError, 'deployment parameters differ'):
            loadCheckpoint(path)

    def test_profile_digest_and_external_profile_override_are_rejected(self):
        path, bundle, _, _ = self.makeBundle()
        with self.assertRaisesRegex(ValueError, 'already contains'):
            loadCheckpoint(path, self.directory / 'calibration.json')
        bundle['ptq_profile']['nodes']['input']['exponent'] += 1
        torch.save(bundle, path)
        with self.assertRaisesRegex(ValueError, 'profile digest mismatch'):
            loadCheckpoint(path)

    def test_qat_provenance_is_rejected_at_all_supported_locations(self):
        variants = [{'mode': 'qat'}, {'training_mode': 'qat'},
                    {'training': {'mode': 'qat'}}, {'quantization_profile': {}}]
        for metadata in variants:
            with self.subTest(metadata=metadata):
                source = copy.deepcopy(self.source)
                source.update(metadata)
                path, profilePath = self.saveSource(source)
                with self.assertRaisesRegex(ValueError, 'QAT'):
                    loadCheckpoint(path, profilePath)

    def test_checkpoint_selection_and_source_identity_are_enforced(self):
        source = copy.deepcopy(self.source)
        source['epoch'] = 3
        path, profilePath = self.saveSource(source)
        with self.assertRaisesRegex(ValueError, 'validation-selected'):
            loadCheckpoint(path, profilePath)
        path, profilePath = self.saveSource()
        profile = json.loads(profilePath.read_text())
        profile['ptq_calibration']['source_checkpoint_sha256'] = 'wrong-source'
        profilePath.write_text(json.dumps(profile))
        with self.assertRaisesRegex(ValueError, 'different FP32 checkpoint'):
            loadCheckpoint(path, profilePath)

    def test_inference_source_and_model_configuration_are_enforced(self):
        path, profilePath = self.saveSource()
        original = json.loads(profilePath.read_text())
        for name in ['model.py', 'quantize.py']:
            profile = copy.deepcopy(original)
            profile['ptq_calibration']['source_sha256'][name] = 'different-source'
            profilePath.write_text(json.dumps(profile))
            with self.subTest(source=name), self.assertRaisesRegex(ValueError, 'inference source changed'):
                loadCheckpoint(path, profilePath)
        profile = copy.deepcopy(original)
        profile['modelConfig']['norm_epsilon'] *= 2
        profilePath.write_text(json.dumps(profile))
        with self.assertRaisesRegex(ValueError, 'configuration mismatch'):
            loadCheckpoint(path, profilePath)

    def test_inference_exp_knots_cannot_disagree_with_export_configuration(self):
        profile = copy.deepcopy(self.profile)
        profile['pwlKnots']['exp_knots'] = list(EXP_KNOTS)
        path, profilePath = self.saveSource(profile=profile)
        with self.assertRaisesRegex(ValueError, 'PWL|knot'):
            loadCheckpoint(path, profilePath)

    def test_inconsistent_integer_formats_are_rejected(self):
        for name, field, value in [
                ('input', 'scale', 0.123), ('input', 'zeroPoint', 1),
                ('input', 'bits', 16), ('blocks.0.Abar', 'exponent', -6),
                ('blocks.0.currentState', 'exponent', 0)]:
            profile = copy.deepcopy(self.profile)
            profile['nodes'][name][field] = value
            if field == 'exponent':
                profile['nodes'][name]['scale'] = 2.0 ** value
            path, profilePath = self.saveSource(profile=profile)
            with self.subTest(node=name, field=field), self.assertRaises(ValueError):
                loadCheckpoint(path, profilePath)


if __name__ == '__main__':
    unittest.main()
