"""Exercise fine-tuning and exact resume on four synthetic training frames."""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch

import finetune
from model import createModel
from quantize import quantizedForward
from train import evaluate, fileSHA256, loadArrays


class FineTuneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.data = cls.root / 'data'
        cls.data.mkdir()
        generator = np.random.default_rng(771)
        for split, count in [('train', 4), ('validate', 2)]:
            features = generator.normal(0, 0.2, (count, 8, 8, 5)).astype(np.float32)
            labels = generator.normal(0, 0.08, (count, 57)).astype(np.float32)
            np.save(cls.data / ('featuremap_' + split + '.npy'), features)
            np.save(cls.data / ('labels_' + split + '.npy'), labels)
        torch.set_num_threads(1)
        torch.manual_seed(882)
        config = {'D': 4, 'E': 2, 'P': 2, 'M': 1, 'N': 2, 'L': 16,
            'input_height': 8, 'input_width': 8, 'input_channels': 5,
            'outputs': 57, 'dt_rank': 1, 'conv_kernel': 2, 'head_hidden': 4,
            'norm_epsilon': 1e-5}
        cls.source = {'config': config, 'model': createModel(config).state_dict(),
            'epoch': 1, 'best_epoch': 1, 'best_validation_rmse_cm': 10.,
            'dataset_sha256': {path.name: fileSHA256(path) for path in cls.data.glob('*.npy')}}
        cls.sourcePath = cls.root / 'source.pt'
        torch.save(cls.source, cls.sourcePath)
        cls.continuous = cls.root / 'continuous'
        cls.resumed = cls.root / 'resumed'
        cls.fallback = cls.root / 'fallback'
        cls.observedSplits = []
        cls.calibrationCalls = []
        import quantize
        originalCalibrate = quantize.calibrateModel

        def loadOnlyTrainValidation(directory, split):
            cls.observedSplits.append(split)
            if split not in ['train', 'validate']:
                raise AssertionError('Fine-tuning attempted to read a test split')
            return loadArrays(directory, split)

        def recordCalibration(*args, **kwargs):
            cls.calibrationCalls.append(kwargs['percentile'])
            return originalCalibrate(*args, **kwargs)

        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(finetune, 'loadArrays', side_effect=loadOnlyTrainValidation), \
                mock.patch.object(quantize, 'calibrateModel', side_effect=recordCalibration):
            finetune.finetune(cls.arguments(cls.continuous, epochs=2))
            finetune.finetune(cls.arguments(cls.resumed, epochs=1))
            cls.beforeResume = len(cls.calibrationCalls)
            finetune.finetune(cls.arguments(cls.resumed, epochs=2,
                resume=str(cls.resumed / 'last.pt')))
            cls.afterResume = len(cls.calibrationCalls)
            # The large threshold deliberately makes epoch zero remain selected.
            finetune.finetune(cls.arguments(cls.fallback, mode='pwl', epochs=1, min_delta=1e6))
        cls.continuousLast = torch.load(cls.continuous / 'last.pt', weights_only=False)
        cls.resumedLast = torch.load(cls.resumed / 'last.pt', weights_only=False)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def arguments(cls, output, **changes):
        values = {'mode': 'qat', 'checkpoint': str(cls.sourcePath), 'data': str(cls.data),
            'output': str(output), 'epochs': 2, 'batch_size': 2, 'threads': 1,
            'seed': 901, 'learning_rate': 0.002, 'weight_decay': 0.0001,
            'clip_grad': 1., 'patience': 8, 'lr_patience': 0, 'min_delta': 0.0001,
            'device': 'cpu', 'calibration': None, 'calibration_samples': 4,
            'calibration_percentiles': [99.9, 100.], 'resume': None}
        values.update(changes)
        return argparse.Namespace(**values)

    def assertStateEqual(self, left, right):
        if torch.is_tensor(left):
            self.assertTrue(torch.equal(left, right))
        elif isinstance(left, np.ndarray):
            np.testing.assert_array_equal(left, right)
        elif isinstance(left, dict):
            self.assertEqual(set(left), set(right))
            for key in left:
                with self.subTest(key=key):
                    self.assertStateEqual(left[key], right[key])
        elif isinstance(left, (tuple, list)):
            self.assertEqual(len(left), len(right))
            for leftItem, rightItem in zip(left, right):
                self.assertStateEqual(leftItem, rightItem)
        else:
            self.assertEqual(left, right)

    def test_actual_qat_resume_matches_two_uninterrupted_epochs(self):
        self.assertEqual(self.continuousLast['epoch'], 2)
        self.assertEqual(self.resumedLast['epoch'], 2)
        self.assertStateEqual(self.continuousLast['model'], self.resumedLast['model'])
        self.assertTrue(any(not torch.equal(value, self.source['model'][name])
            for name, value in self.continuousLast['model'].items()))
        for key in ['optimizer', 'scheduler', 'torch_rng', 'python_rng', 'numpy_rng', 'cuda_rng']:
            with self.subTest(state=key):
                self.assertStateEqual(self.continuousLast[key], self.resumedLast[key])
        self.assertTrue(self.continuousLast['optimizer']['state'])
        for state in self.continuousLast['optimizer']['state'].values():
            self.assertEqual(int(state['step']), 4)
        for key in ['best_epoch', 'best_validation_rmse_cm', 'validation']:
            self.assertStateEqual(self.continuousLast[key], self.resumedLast[key])

    def test_profile_is_frozen_and_resume_does_not_recalibrate(self):
        self.assertEqual(self.beforeResume, self.afterResume)
        self.assertEqual(self.calibrationCalls, [99.9, 100., 99.9, 100.])
        left = self.continuousLast['quantization_profile']
        self.assertEqual(left, self.resumedLast['quantization_profile'])
        self.assertEqual(finetune.profileSHA256(left),
            self.continuousLast['quantization_profile_sha256'])
        self.assertEqual(left, json.loads((self.continuous / 'calibration.json').read_text()))
        best = torch.load(self.continuous / 'best.pt', weights_only=False)
        self.assertEqual(left, best['quantization_profile'])
        self.assertEqual(len(best['quantization_selection']['candidates']), 3)

    def test_checkpoint_score_uses_exact_quantized_validation(self):
        best = torch.load(self.continuous / 'best.pt', weights_only=False)
        model = createModel(best['config'])
        model.load_state_dict(best['model'])
        features, labels = loadArrays(self.data, 'validate')
        profile = best['quantization_profile']
        metrics = evaluate(model, features, labels, 2, 'cpu',
            lambda network, values: quantizedForward(network, values, profile))
        self.assertEqual(best['validation'], metrics)
        self.assertEqual(best['best_validation_rmse_cm'], metrics['rmse_coordinate_mean_cm'])
        self.assertEqual(best['epoch'], best['best_epoch'])

    def test_no_test_arrays_are_required_or_loaded(self):
        self.assertEqual(set(self.observedSplits), {'train', 'validate'})
        self.assertFalse((self.data / 'featuremap_test.npy').exists())
        self.assertFalse((self.data / 'labels_test.npy').exists())
        run = json.loads((self.continuous / 'run.json').read_text())
        self.assertFalse(run['test_used_for_selection'])
        self.assertEqual(set(run['dataset_sha256']), set(self.source['dataset_sha256']))

    def test_epoch_zero_fallback_retains_source_weights(self):
        best = torch.load(self.fallback / 'best.pt', weights_only=False)
        last = torch.load(self.fallback / 'last.pt', weights_only=False)
        self.assertEqual(best['epoch'], 0)
        self.assertEqual(best['best_epoch'], 0)
        self.assertEqual(last['epoch'], 1)
        self.assertEqual(last['best_epoch'], 0)
        self.assertStateEqual(best['model'], self.source['model'])
        self.assertTrue(any(not torch.equal(value, self.source['model'][name])
            for name, value in last['model'].items()))
        self.assertEqual(last['best_checkpoint_sha256'], fileSHA256(self.fallback / 'best.pt'))

    def test_overwrite_guard_preserves_existing_artifacts(self):
        before = {path.name: fileSHA256(path) for path in self.continuous.iterdir()}
        with self.assertRaisesRegex(ValueError, 'Output contains run artifacts'):
            finetune.finetune(self.arguments(self.continuous))
        after = {path.name: fileSHA256(path) for path in self.continuous.iterdir()}
        self.assertEqual(before, after)

    def test_resume_rejects_changed_profile_and_source_metadata(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            output = Path(directory)
            (output / 'best.pt').write_bytes((self.continuous / 'best.pt').read_bytes())
            for kind in ['profile', 'source_checkpoint', 'source_code', 'dataset', 'best']:
                with self.subTest(change=kind):
                    changed = copy.deepcopy(self.continuousLast)
                    if kind == 'profile':
                        changed['quantization_profile']['nodes']['input']['exponent'] += 1
                    elif kind == 'source_checkpoint':
                        changed['source_checkpoint_sha256'] = 'modified'
                    elif kind == 'source_code':
                        changed['source_sha256']['model.py'] = 'modified'
                    elif kind == 'dataset':
                        changed['dataset_sha256']['labels_train.npy'] = 'modified'
                    else:
                        changed['best_checkpoint_sha256'] = 'modified'
                    torch.save(changed, output / 'last.pt')
                    args = self.arguments(output, epochs=3, resume=str(output / 'last.pt'))
                    with self.assertRaises(ValueError):
                        finetune.loadResume(args, output, self.source['config'],
                            self.source['dataset_sha256'], fileSHA256(self.sourcePath),
                            self.continuousLast['source_sha256'], 'cpu')

    def test_source_rejects_nonselected_epoch_and_changed_dataset(self):
        for kind in ['epoch', 'dataset']:
            with self.subTest(change=kind):
                source = copy.deepcopy(self.source)
                if kind == 'epoch':
                    source['epoch'] += 1
                else:
                    source['dataset_sha256']['labels_train.npy'] = 'modified'
                with self.assertRaises(ValueError):
                    finetune.checkSource(source, self.source['dataset_sha256'])


if __name__ == '__main__':
    unittest.main()
