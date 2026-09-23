"""Verify actual FP32 refinement resume, source fallback, and split isolation."""
import argparse
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch

from model import createModel
import refine_fp32
from train import fileSHA256, loadArrays


class RefineFP32Tests(unittest.TestCase):
    def assertNestedEqual(self, left, right):
        if torch.is_tensor(left):
            self.assertTrue(torch.equal(left, right))
        elif isinstance(left, np.ndarray):
            np.testing.assert_array_equal(left, right)
        elif isinstance(left, dict):
            self.assertEqual(set(left), set(right))
            for key in left:
                self.assertNestedEqual(left[key], right[key])
        elif isinstance(left, (tuple, list)):
            self.assertEqual(len(left), len(right))
            for first, second in zip(left, right):
                self.assertNestedEqual(first, second)
        else:
            self.assertEqual(left, right)

    def test_resume_fallback_and_train_validation_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            data.mkdir()
            generator = np.random.default_rng(51)
            for split, count in [('train', 4), ('validate', 2)]:
                np.save(data / ('featuremap_' + split + '.npy'),
                    generator.normal(0, 0.2, (count, 8, 8, 5)).astype(np.float32))
                np.save(data / ('labels_' + split + '.npy'),
                    generator.normal(0, 0.08, (count, 57)).astype(np.float32))
            torch.set_num_threads(1)
            torch.manual_seed(59)
            config = {'D': 4, 'E': 2, 'P': 2, 'M': 1, 'N': 2, 'L': 16,
                'input_height': 8, 'input_width': 8, 'input_channels': 5,
                'outputs': 57, 'dt_rank': 1, 'conv_kernel': 2, 'head_hidden': 4,
                'norm_epsilon': 1e-5}
            source = {'config': config, 'model': createModel(config).state_dict(),
                'epoch': 1, 'best_epoch': 1, 'best_validation_rmse_cm': 10.,
                'dataset_sha256': {path.name: fileSHA256(path) for path in data.glob('*.npy')}}
            sourcePath = root / 'source.pt'
            torch.save(source, sourcePath)
            arguments = {'checkpoint': str(sourcePath), 'data': str(data),
                'output': str(root / 'continuous'), 'epochs': 2, 'batch_size': 2,
                'threads': 1, 'seed': 63, 'learning_rate': 0.001, 'weight_decay': 0.0001,
                'clip_grad': 1., 'patience': 15, 'lr_patience': 0, 'min_delta': 0.0001,
                'device': 'cpu', 'resume': None}
            opened = []

            def restrictedLoad(directory, split):
                self.assertIn(split, ['train', 'validate'])
                opened.append(split)
                return loadArrays(directory, split)

            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(refine_fp32, 'loadArrays', side_effect=restrictedLoad):
                refine_fp32.refine(argparse.Namespace(**arguments))
                partial = dict(arguments, output=str(root / 'resumed'), epochs=1)
                refine_fp32.refine(argparse.Namespace(**partial))
                partial.update(epochs=2, resume=str(root / 'resumed' / 'last.pt'))
                refine_fp32.refine(argparse.Namespace(**partial))
                fallback = dict(arguments, output=str(root / 'fallback'), epochs=1, min_delta=1e6)
                refine_fp32.refine(argparse.Namespace(**fallback))
            continuous = torch.load(root / 'continuous' / 'last.pt', weights_only=False)
            resumed = torch.load(root / 'resumed' / 'last.pt', weights_only=False)
            for key in ['model', 'optimizer', 'scheduler', 'torch_rng', 'python_rng',
                        'numpy_rng', 'cuda_rng', 'best_epoch', 'best_validation_rmse_cm', 'validation']:
                with self.subTest(state=key):
                    self.assertNestedEqual(continuous[key], resumed[key])
            self.assertTrue(any(not torch.equal(value, source['model'][name])
                for name, value in continuous['model'].items()))
            self.assertTrue(continuous['optimizer']['state'])
            for state in continuous['optimizer']['state'].values():
                self.assertEqual(int(state['step']), 4)
            self.assertEqual(set(opened), {'train', 'validate'})
            self.assertFalse((data / 'featuremap_test.npy').exists())
            fallbackBest = torch.load(root / 'fallback' / 'best.pt', weights_only=False)
            self.assertEqual(fallbackBest['epoch'], 0)
            self.assertNestedEqual(fallbackBest['model'], source['model'])
            changed = copy.deepcopy(resumed)
            changed['source_sha256']['model.py'] = 'changed'
            torch.save(changed, root / 'resumed' / 'last.pt')
            partial['epochs'] = 3
            with self.assertRaisesRegex(ValueError, 'source code differs'):
                refine_fp32.loadResume(argparse.Namespace(**partial), root / 'resumed', config,
                    source['dataset_sha256'], fileSHA256(sourcePath), resumed['source_sha256'], 'cpu')


if __name__ == '__main__':
    unittest.main()
