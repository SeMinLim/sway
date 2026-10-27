"""Verify inference-only PWL calibration and recurrent profile propagation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch

from model import EXP_KNOTS, SILU_KNOTS, createModel, forwardModel
from ptq_pwl import applyKnots, parameterSHA256, validateSource
from quantize import QuantizationObserver, integerSSM
from train import fileSHA256


class PTQPWLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_fitted_checkpoint_preserves_exact_model(self):
        torch.manual_seed(2048)
        config = json.loads((Path(__file__).parent / 'config' / 'model.json').read_text())
        network = createModel(config)
        source = {'config': config, 'model': copy.deepcopy(network.state_dict()), 'epoch': 1}
        profile = {'siluKnots': list(SILU_KNOTS),
                   'expKnots': [-4., -3., -2., -1.5, -1., -.75, -.5, -.25, -.125, -.0625, 0., 1.]}
        fitted = applyKnots(source, profile)
        self.assertNotIn('exp_knots', source['config'])
        self.assertEqual(parameterSHA256(source['model']), parameterSHA256(fitted['model']))
        newNetwork = createModel(fitted['config'])
        newNetwork.load_state_dict(fitted['model'])
        features = torch.randn(3, 8, 8, 5)
        with torch.no_grad():
            self.assertTrue(torch.equal(forwardModel(network, features), forwardModel(newNetwork, features)))

    def test_ssm_uses_custom_exp_knots_and_default(self):
        values = {'x': 0, 'Bbar': -2, 'C': -2, 'D': 0, 'state': -8,
                  'ssmY': -8, 'expInput': -4, 'Abar': -7}
        nodes = {'block.' + name: {'exponent': exponent, 'scale': 2.0 ** exponent,
                                  'bits': 17 if name == 'state' else 8}
                 for name, exponent in values.items()}
        profile = {'nodes': nodes, 'usePWL': True}
        x = torch.ones(1, 3, 1)
        delta = torch.full((1, 3, 1), .25)
        A = -torch.ones(1, 1)
        B = torch.ones(1, 3, 1)
        C = torch.full((1, 3, 1), .25)
        D = torch.zeros(1)
        for custom, aBarInteger in [(False, 103), (True, 100)]:
            candidate = copy.deepcopy(profile)
            if custom:
                candidate['pwlKnots'] = {'exp_knots': [-4., -3., -2., -1.5, -1., -.75,
                                                       -.5, -.25, -.125, -.0625, 0., 1.]}
            actual = QuantizationObserver(candidate).ssm('block', x, delta, A, B, C, D)
            expected, _ = integerSSM(
                torch.ones(1, 3, 1, dtype=torch.int64),
                torch.full((1, 3, 1, 1), aBarInteger, dtype=torch.int64),
                torch.ones(1, 3, 1, 1, dtype=torch.int64),
                torch.ones(1, 3, 1, dtype=torch.int64),
                torch.zeros(1, dtype=torch.int64), 0, -2, -2, 0, -8, -8)
            self.assertTrue(torch.equal(actual, expected.float() * 2.0 ** -8))

    def test_fitting_checks_only_requested_data_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = {'mode': 'fp32', 'epoch': 7, 'best_epoch': 7, 'dataset_sha256': {}}
            for split in ['train', 'validate']:
                for prefix in ['featuremap_', 'labels_']:
                    path = directory / (prefix + split + '.npy')
                    np.save(path, np.zeros((2, 2), dtype=np.float32))
                    source['dataset_sha256'][path.name] = fileSHA256(path)
            checked = validateSource(source, directory)
            self.assertEqual(set(checked), {'featuremap_train.npy', 'labels_train.npy'})
            self.assertEqual(validateSource(source, directory, True), source['dataset_sha256'])
            (directory / 'labels_validate.npy').write_bytes(b'changed')
            self.assertEqual(validateSource(source, directory), checked)
            with self.assertRaisesRegex(ValueError, 'Data differs.*labels_validate'):
                validateSource(source, directory, True)
            (directory / 'featuremap_train.npy').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Data differs.*featuremap_train'):
                validateSource(source, directory)
            self.assertFalse((directory / 'featuremap_test.npy').exists())

    def test_fitting_rejects_nonexact_training_and_nonselected_checkpoint(self):
        source = {'mode': 'fp32', 'epoch': 7, 'best_epoch': 7}
        for metadata in [{'mode': 'qat'}, {'mode': 'pwl'}, {'training_mode': 'qat'},
                         {'training_mode': 'pwl'}, {'training': {'mode': 'qat'}},
                         {'training': {'mode': 'pwl'}}, {'quantization_profile': {}}]:
            with self.subTest(metadata=metadata), self.assertRaisesRegex(ValueError, 'exact-trained'):
                validateSource(dict(source, **metadata), '/unused')
        with self.assertRaisesRegex(ValueError, 'validation-selected'):
            validateSource(dict(source, epoch=8), '/unused')
        with self.assertRaisesRegex(ValueError, 'no dataset hash'):
            validateSource(source, '/unused')


if __name__ == '__main__':
    unittest.main()
