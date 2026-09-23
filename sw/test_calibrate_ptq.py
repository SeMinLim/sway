"""Regression checks for PTQ warm-start provenance rejection."""
import copy
import unittest

from calibrate_ptq import validateInitialProvenance


class InitialProfileProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.hashes = {'model.py': 'model-current', 'quantize.py': 'quantize-current'}
        self.profile = {'calibrationSplit': 'train', 'modelConfig': {'D': 20},
                        'source_checkpoint_sha256': 'checkpoint-current',
                        'inference_source_sha256': dict(self.hashes),
                        'ptq_calibration': {'source_checkpoint_sha256': 'checkpoint-current',
                                            'source_sha256': dict(self.hashes)}}

    def validate(self, profile):
        validateInitialProvenance(profile, {'D': 20}, 'checkpoint-current', self.hashes)

    def test_matching_current_and_legacy_metadata(self):
        self.validate(self.profile)
        legacy = copy.deepcopy(self.profile)
        del legacy['inference_source_sha256']
        self.validate(legacy)

    def test_legacy_inference_hash_is_checked(self):
        for topPresent in [False, True]:
            profile = copy.deepcopy(self.profile)
            if not topPresent:
                del profile['inference_source_sha256']
            profile['ptq_calibration']['source_sha256']['quantize.py'] = 'old-numerics'
            with self.assertRaisesRegex(ValueError, 'inference source differs: quantize.py'):
                self.validate(profile)

    def test_conflicting_nested_checkpoint_hash_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile['ptq_calibration']['source_checkpoint_sha256'] = 'other-checkpoint'
        with self.assertRaisesRegex(ValueError, 'different checkpoint'):
            self.validate(profile)


if __name__ == '__main__':
    unittest.main()
