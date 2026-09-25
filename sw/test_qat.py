"""Check QAT against the unchanged integer inference path and state semantics."""
import copy
import json
from pathlib import Path
import unittest

import torch

from model import createModel, forwardModel
from quantize import (QATObserver, QuantizationObserver, _qatCorrection, calibrateModel, integerSSM,
                      qatForward, quantizedForward)


class QATTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        torch.manual_seed(7321)
        cls.root = Path(__file__).parent
        cls.config = json.loads((cls.root / "config" / "model.json").read_text())
        cls.model = createModel(cls.config)
        cls.features = torch.randn(5, 8, 8, 5)

    def test_exact_forward_both_nonlinearity_modes(self):
        for usePWL in [False, True]:
            with self.subTest(usePWL=usePWL):
                profile = calibrateModel(self.model, self.features, batchSize=3,
                                         maxSamples=5, percentile=100, usePWL=usePWL)
                expected, expectedStats = quantizedForward(
                    self.model, self.features, profile, returnStatistics=True)
                actual, actualStats = qatForward(
                    self.model, self.features, profile, returnStatistics=True)
                self.assertTrue(torch.equal(actual, expected))
                self.assertEqual(actualStats, expectedStats)

    def test_intermediate_affine_conv_and_ssm_values(self):
        profile = calibrateModel(self.model, self.features, maxSamples=5, percentile=100)
        records = []
        for observerClass in [QuantizationObserver, QATObserver]:
            observer = observerClass(profile)
            outputs = {}
            for operation in ["linear", "conv1d", "ssm"]:
                method = getattr(observer, operation)
                def record(name, *arguments, method=method, operation=operation):
                    result = method(name, *arguments)
                    outputs[(operation, name)] = result.detach().clone()
                    return result
                setattr(observer, operation, record)
            forwardModel(self.model, self.features, observer=observer, usePWL=True)
            records.append(outputs)
        self.assertEqual(set(records[0]), set(records[1]))
        self.assertGreater(len(records[0]), 10)
        for name in records[0]:
            with self.subTest(operation=name):
                self.assertTrue(torch.equal(records[0][name], records[1][name]))

    def test_finite_nonzero_parameter_gradients(self):
        model = copy.deepcopy(self.model)
        profile = calibrateModel(model, self.features, maxSamples=5, percentile=100)
        actual = qatForward(model, self.features, profile)
        target = torch.linspace(-0.2, 0.3, 57).expand_as(actual)
        ((actual - target) ** 2).mean().backward()
        for name, parameter in model.named_parameters():
            with self.subTest(parameter=name):
                self.assertIsNotNone(parameter.grad)
                self.assertTrue(torch.isfinite(parameter.grad).all().item())
        for name in ["embedding.weight", "blocks.0.inWeight", "blocks.0.A_log",
                     "blocks.1.outWeight", "headHidden.weight", "headOutput.weight"]:
            self.assertGreater(float(dict(model.named_parameters())[name].grad.abs().sum()), 0)

    def test_saturation_gradient_and_exact_values(self):
        value = torch.tensor([-200.0, -1.25, 0.75, 200.0], requires_grad=True)
        exact = torch.tensor([-128.0, -1.0, 1.0, 127.0])
        actual = _qatCorrection(exact, value, 0, 8)
        self.assertTrue(torch.equal(actual, exact))
        actual.sum().backward()
        self.assertTrue(torch.equal(value.grad, torch.tensor([0.0, 1.0, 1.0, 0.0])))

    def test_negative_recurrent_truncation(self):
        # exp(-1) quantizes to 47/128. Negative arithmetic right shifts floor
        # -175/128 to -2, whereas truncation toward zero would incorrectly use -1.
        values = {"x": 0, "Bbar": 0, "C": -2, "D": 0, "state": 0,
                  "ssmY": -5, "expInput": 0, "Abar": -7}
        nodes = {"block." + name: {"exponent": exponent, "scale": 2.0 ** exponent,
                                  "bits": 17 if name == "state" else 8}
                 for name, exponent in values.items()}
        profile = {"nodes": nodes, "usePWL": False}
        x = -torch.ones(1, 3, 1)
        x.requires_grad_()
        delta = torch.ones(1, 3, 1)
        A = -torch.ones(1, 1)
        B = torch.ones(1, 3, 1)
        C = torch.full((1, 3, 1), 0.25)
        D = torch.zeros(1)
        expected, trace = integerSSM(
            -torch.ones(1, 3, 1, dtype=torch.int64),
            torch.full((1, 3, 1, 1), 47, dtype=torch.int64),
            torch.ones(1, 3, 1, 1, dtype=torch.int64),
            torch.ones(1, 3, 1, dtype=torch.int64),
            torch.zeros(1, dtype=torch.int64), 0, 0, -2, 0, 0, -5, trace=True)
        self.assertEqual(trace["currentState"].flatten().tolist(), [-128, -175, -222])
        self.assertEqual(trace["storedState"].flatten().tolist(), [-1, -2, -2])
        self.assertEqual(expected.flatten().tolist(), [-8, -11, -14])
        actual = QATObserver(profile).ssm("block", x, delta, A, B, C, D)
        self.assertTrue(torch.equal(actual, expected.float() * 2.0 ** -5))
        actual.sum().backward()
        self.assertTrue(torch.isfinite(x.grad).all().item())
        self.assertGreater(float(x.grad.abs().sum()), 0)

    def test_saved_checkpoint_and_fixtures(self):
        checkpointPath = self.root / "results" / "mars" / "final" / "checkpoint.pt"
        profilePath = checkpointPath.parent / "calibration.json"
        dataPath = self.root.parent / "hw" / "model" / "real_fixture_features.npy"
        for path in [checkpointPath, profilePath, dataPath]:
            self.assertTrue(path.is_file(), "Required checked-in file missing: " + str(path))
        import numpy as np
        checkpoint = torch.load(checkpointPath, map_location="cpu", weights_only=False)
        model = createModel(checkpoint["config"])
        model.load_state_dict(checkpoint["model"])
        profile = json.loads(profilePath.read_text())
        features = torch.from_numpy(np.load(dataPath, mmap_mode="r")[:3].astype(np.float32))
        expected = quantizedForward(model, features, profile)
        actual = qatForward(model, features, profile)
        self.assertTrue(torch.equal(actual, expected))
        actual.square().mean().backward()
        self.assertTrue(torch.isfinite(model.embedding.weight.grad).all().item())
        self.assertGreater(float(model.embedding.weight.grad.abs().sum()), 0)


if __name__ == "__main__":
    unittest.main()
