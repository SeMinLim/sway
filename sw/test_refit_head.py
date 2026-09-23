"""Verify train-only FP32 head fitting and fixed-backbone provenance."""
import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch

from model import createModel
from refit_head import fitLinearHead, hiddenActivations, refit
from train import fileSHA256


class HeadRefitTests(unittest.TestCase):
    def test_affine_recovery_and_unpenalized_intercept(self):
        generator = np.random.default_rng(291)
        features = generator.normal(size=(1000, 6))
        weight = generator.normal(size=(3, 6))
        bias = generator.normal(size=3)
        fittedWeight, fittedBias = fitLinearHead(features, features @ weight.T + bias, 0.)
        np.testing.assert_allclose(fittedWeight, weight, rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(fittedBias, bias, rtol=1e-6, atol=1e-6)
        for ridge in [0., 0.0001, 0.001]:
            constant = np.ones((20, 4))
            labels = np.full((20, 3), [1., 2., 3.])
            fittedWeight, fittedBias = fitLinearHead(constant, labels, ridge)
            np.testing.assert_allclose(constant @ fittedWeight.T + fittedBias, labels)

    def test_real_refit_preserves_backbone_and_never_needs_test_arrays(self):
        torch.set_num_threads(1)
        torch.manual_seed(8)
        config = {'D': 4, 'E': 2, 'P': 2, 'M': 1, 'N': 2, 'L': 16,
            'input_height': 8, 'input_width': 8, 'input_channels': 5,
            'outputs': 57, 'dt_rank': 1, 'conv_kernel': 2, 'head_hidden': 4,
            'norm_epsilon': 1e-5}
        model = createModel(config)
        generator = np.random.default_rng(37)
        targetWeight = generator.normal(size=(57, 4)).astype(np.float32)
        targetBias = generator.normal(size=57).astype(np.float32)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            data.mkdir()
            for split, count in [('train', 64), ('validate', 16)]:
                features = generator.normal(size=(count, 8, 8, 5)).astype(np.float32)
                hidden = hiddenActivations(model, torch.from_numpy(features), 16, 'cpu')
                labels = hidden @ targetWeight.T + targetBias
                np.save(data / ('featuremap_' + split + '.npy'), features)
                np.save(data / ('labels_' + split + '.npy'), labels.astype(np.float32))
            source = {'model': model.state_dict(), 'config': config,
                'epoch': 1, 'best_epoch': 1, 'best_validation_rmse_cm': 100.,
                'dataset_sha256': {path.name: fileSHA256(path) for path in data.glob('*.npy')}}
            sourcePath = root / 'source.pt'
            torch.save(source, sourcePath)
            args = argparse.Namespace(checkpoint=str(sourcePath), data=str(data),
                output=str(root / 'fitted'), batch_size=16, threads=1, device='cpu',
                ridges=[0., 0.0001, 0.001])
            with contextlib.redirect_stdout(io.StringIO()):
                refit(args)
            result = torch.load(root / 'fitted' / 'best.pt', weights_only=False)
            self.assertLess(result['validation']['rmse_coordinate_mean_cm'], 0.001)
            for name, value in source['model'].items():
                if not name.startswith('headOutput.'):
                    self.assertTrue(torch.equal(value, result['model'][name]), name)
            self.assertFalse(result['quantized_feedback_used'])
            self.assertFalse(result['test_used_for_selection'])
            source['mode'] = 'qat'
            torch.save(source, sourcePath)
            args.output = str(root / 'rejected')
            with self.assertRaisesRegex(ValueError, 'non-QAT exact FP32'):
                refit(args)


if __name__ == '__main__':
    unittest.main()
