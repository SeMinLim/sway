"""Reject QAT evaluation with inference code differing from its training run."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import evaluate
from train import fileSHA256


class EvaluateSourceTests(unittest.TestCase):
    def test_changed_or_missing_qat_inference_digest_stops_before_loading_data(self):
        sources = {name: fileSHA256(Path(evaluate.__file__).parent / name)
            for name in ['model.py', 'quantize.py']}
        for name in sources:
            for change in ['mismatch', 'missing']:
                with self.subTest(name=name, change=change), tempfile.TemporaryDirectory() as directory:
                    recorded = dict(sources)
                    if change == 'mismatch':
                        recorded[name] = 'changed'
                    else:
                        del recorded[name]
                    checkpoint = {'epoch': 1, 'best_epoch': 1,
                        'quantization_profile': {}, 'source_sha256': recorded}
                    arguments = ['evaluate.py', '--data', directory,
                        '--checkpoint', str(Path(directory) / 'best.pt'), '--output', directory]
                    with mock.patch('sys.argv', arguments), \
                            mock.patch.object(evaluate.torch, 'load', return_value=checkpoint), \
                            mock.patch.object(evaluate, 'createModel') as createModel, \
                            mock.patch.object(evaluate, 'loadArrays') as loadArrays:
                        with self.assertRaisesRegex(ValueError,
                                'QAT inference source differs from training: ' + name):
                            evaluate.main()
                        createModel.assert_not_called()
                        loadArrays.assert_not_called()


if __name__ == '__main__':
    unittest.main()
