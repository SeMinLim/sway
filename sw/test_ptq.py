"""Check PTQ preparation preserves the FP32 contract and calibration identity."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch

from model import createModel, forwardModel
from ptq_equalize import equalizeModel, prepareCheckpoint
from quantize import calibrateModel, quantizedForward


class PTQTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        torch.manual_seed(20260923)
        cls.config = json.loads((Path(__file__).parent / 'config' / 'model.json').read_text())
        cls.model = createModel(cls.config).eval()
        cls.features = torch.randn(5, 8, 8, 5)

    def test_equalized_outputs_and_checkpoint_roundtrip(self):
        original = {name: value.detach().clone() for name, value in self.model.state_dict().items()}
        headDivisors = [0.5, 1, 2, 4, 0.25] * 4
        changed, _, metadata = equalizeModel(self.model, [1, 1, 1, 1, 4], headDivisors)
        for usePWL in [False, True]:
            with torch.no_grad():
                expected = forwardModel(self.model, self.features, usePWL=usePWL)
                actual = forwardModel(changed, self.features, usePWL=usePWL)
            self.assertTrue(torch.equal(expected, actual))
        stream = io.BytesIO()
        torch.save({'config': changed.config, 'model': changed.state_dict()}, stream)
        stream.seek(0)
        saved = torch.load(stream, weights_only=False)
        loaded = createModel(saved['config'])
        loaded.load_state_dict(saved['model'])
        self.assertTrue(torch.equal(loaded(self.features), self.model(self.features)))
        self.assertFalse(metadata['usesGradients'])
        self.assertFalse(metadata['retrainingPerformed'])
        for name, value in self.model.state_dict().items():
            self.assertTrue(torch.equal(value, original[name]))
        self.assertNotIn('input_channel_divisors', self.model.config)

    def test_preprocessing_precedes_quantization_observation(self):
        changed, _, _ = equalizeModel(self.model, [1, 1, 1, 1, 4])
        observed = {}
        def record(name, value):
            if name == 'input':
                observed[name] = value.detach().clone()
            return value
        forwardModel(changed, self.features, observer=record)
        self.assertTrue(torch.equal(observed['input'][..., :4], self.features[..., :4]))
        self.assertTrue(torch.equal(observed['input'][..., 4] * 4, self.features[..., 4]))
        profile = calibrateModel(changed, self.features, maxSamples=5, batchSize=3,
                                  percentile=100)
        self.assertEqual(profile['modelConfig'], changed.config)
        self.assertTrue(torch.isfinite(quantizedForward(changed, self.features, profile)).all())
        with self.assertRaisesRegex(ValueError, 'configuration differ'):
            quantizedForward(self.model, self.features, profile)

    def test_custom_pwl_configuration_and_profile_identity(self):
        config = copy.deepcopy(self.config)
        config['silu_knots'] = np.linspace(-7, 7, 18).tolist()
        config['exp_knots'] = np.linspace(-4, 1, 12).tolist()
        model = createModel(config)
        model.load_state_dict(self.model.state_dict())
        self.assertTrue(torch.equal(model(self.features), self.model(self.features)))
        self.assertFalse(torch.equal(forwardModel(model, self.features, usePWL=True),
                                     forwardModel(self.model, self.features, usePWL=True)))
        profile = calibrateModel(model, self.features, maxSamples=5, usePWL=True)
        self.assertEqual(profile['pwlKnots']['exp_knots'], config['exp_knots'])
        self.assertEqual(profile['pwlKnots']['silu_knots'], config['silu_knots'])
        with self.assertRaisesRegex(ValueError, 'configuration differ'):
            quantizedForward(self.model, self.features, profile)
        invalid = copy.deepcopy(config)
        invalid['exp_knots'][1] = invalid['exp_knots'][0]
        with self.assertRaisesRegex(ValueError, 'PWL configuration'):
            createModel(invalid)

    def test_invalid_and_repeated_equalization_rejected(self):
        for divisors in [[1, 1], [1, 1, 1, 1, 0], [1, 1, 1, 1, 3],
                         [1, 1, 1, 1, float('inf')]]:
            with self.subTest(divisors=divisors), self.assertRaises(ValueError):
                equalizeModel(self.model, divisors)
        changed, _, _ = equalizeModel(self.model, [1, 1, 1, 1, 4])
        with self.assertRaisesRegex(ValueError, 'already applied'):
            equalizeModel(changed, [1, 1, 1, 1, 4])

    def test_prepared_checkpoint_provenance_and_data_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            hashes = {}
            for split in ['train', 'validate']:
                for prefix, array in [('featuremap_', self.features.numpy()),
                                       ('labels_', np.zeros((5, 57), dtype=np.float32))]:
                    path = directory / (prefix + split + '.npy')
                    np.save(path, array)
                    hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            source = {'model': self.model.state_dict(), 'config': self.config,
                      'epoch': 7, 'best_epoch': 7, 'mode': 'fp32',
                      'best_validation_rmse_cm': 12.0, 'dataset_sha256': hashes,
                      'source_checkpoint_sha256': 'earlier-source',
                      'source_checkpoint_epoch': 3, 'optimizer': {'step': 7},
                      'scheduler': {'step': 7}, 'torch_rng': torch.get_rng_state()}
            path = directory / 'source.pt'
            torch.save(source, path)
            prepared = prepareCheckpoint(path, directory, [1, 1, 1, 1, 4], True)
            self.assertTrue(prepared['inference_only'])
            self.assertEqual(prepared['source_checkpoint_sha256'], 'earlier-source')
            self.assertEqual(prepared['source_checkpoint_epoch'], 3)
            self.assertEqual(prepared['epoch'], prepared['best_epoch'])
            self.assertEqual(prepared['ptq_equalization']['verification']['frames'], 5)
            self.assertEqual(prepared['ptq_equalization']['sourceCheckpointSHA256'],
                             hashlib.sha256(path.read_bytes()).hexdigest())
            for key in ['optimizer', 'scheduler', 'torch_rng']:
                self.assertNotIn(key, prepared)
            self.assertFalse((directory / 'featuremap_test.npy').exists())
            (directory / 'labels_validate.npy').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Data differs'):
                prepareCheckpoint(path, directory, [1, 1, 1, 1, 4])
            source['mode'] = 'qat'
            torch.save(source, path)
            with self.assertRaisesRegex(ValueError, 'not QAT'):
                prepareCheckpoint(path, directory, [1, 1, 1, 1, 4])
            source['mode'] = 'fp32'
            source['epoch'] = 8
            torch.save(source, path)
            with self.assertRaisesRegex(ValueError, 'best checkpoint'):
                prepareCheckpoint(path, directory, [1, 1, 1, 1, 4])


if __name__ == '__main__':
    unittest.main()
