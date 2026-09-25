"""Check activation placement with signed, hand-computable branch values."""
import copy
import unittest

import torch
from torch.nn import functional as F

from calibrate_ptq import scaleGroups, validateInitialProvenance
from model import EXP_KNOTS, SILU_KNOTS, createModel, forwardModel, piecewise
from ptq_pwl import ActivationHistogram, calibrateKnots
from quantize import (QATObserver, QuantizationObserver, calibrateModel,
                      qatForward, quantizedForward)


class ArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.config = {'D': 2, 'E': 1, 'P': 1,
                      'M': 1, 'N': 1, 'L': 4, 'input_height': 2,
                      'input_width': 2, 'input_channels': 2, 'outputs': 3,
                      'dt_rank': 2, 'conv_kernel': 1, 'head_hidden': 2,
                      'norm_epsilon': 1e-5}
        torch.manual_seed(20260925)
        cls.model = createModel(cls.config)
        block = cls.model.blocks[0]
        with torch.no_grad():
            block.convWeight.zero_()
            block.convBias.copy_(torch.tensor([-2.0, 3.0]))
            block.xWeight.copy_(torch.tensor([[1., 0.], [0., 1.],
                                              [1., 1.], [1., -1.]]))
            block.dtWeight.copy_(torch.tensor([[-.125, 0.], [0., .125]]))
            block.dtBias.fill_(-.125)
            block.A_log.zero_()
        cls.features = torch.arange(16, dtype=torch.float32).reshape(2, 2, 2, 2) / 8

    def assertCurrentBranches(self, values):
        prefix = 'blocks.0.'
        self.assertTrue(torch.equal(values[prefix + 'conv'], values[prefix + 'x']))
        self.assertTrue((values[prefix + 'x'][..., 0] < 0).all())
        expectedInput = torch.tensor([0., 3.]).expand_as(values[prefix + 'deltaInput'])
        self.assertTrue(torch.equal(values[prefix + 'deltaInput'], expectedInput))
        expectedDelta = torch.tensor([-.125, .25]).expand_as(values[prefix + 'delta'])
        self.assertTrue(torch.equal(values[prefix + 'delta'], expectedDelta))
        self.assertTrue(torch.equal(values[prefix + 'deltaProjection'], expectedDelta))
        self.assertTrue((values[prefix + 'B'] == 1).all())
        self.assertTrue((values[prefix + 'C'] == -5).all())

    def test_exact_and_pwl_activation_placement(self):
        for usePWL in [False, True]:
            values = {}
            def record(name, value):
                values[name] = value.detach().clone()
                return value
            output = forwardModel(self.model, self.features, observer=record, usePWL=usePWL)
            self.assertTrue(torch.isfinite(output).all())
            self.assertCurrentBranches(values)
            gateInput = values['blocks.0.gateInput']
            gate = piecewise(gateInput, SILU_KNOTS, 'silu') if usePWL else F.silu(gateInput)
            self.assertTrue(torch.equal(values['blocks.0.gate'], gate))
            self.assertTrue((values['blocks.0.expInput'][..., 0, :] > 0).all())
            self.assertTrue((values['blocks.0.Abar'][..., 0, :] > 1).all())

    def test_quantized_and_qat_signed_branches(self):
        for usePWL in [False, True]:
            profile = calibrateModel(self.model, self.features, maxSamples=2,
                                     percentile=100, usePWL=usePWL)
            records = []
            for observerClass in [QuantizationObserver, QATObserver]:
                class Recorder(observerClass):
                    def __call__(self, name, value):
                        result = super().__call__(name, value)
                        self.values[name] = result.detach().clone()
                        return result

                    def linear(self, name, *arguments):
                        result = super().linear(name, *arguments)
                        self.values[name] = result.detach().clone()
                        return result

                    def conv1d(self, name, *arguments):
                        result = super().conv1d(name, *arguments)
                        self.values[name] = result.detach().clone()
                        return result
                observer = Recorder(profile)
                observer.values = {}
                result = forwardModel(self.model, self.features, observer, usePWL)
                self.assertCurrentBranches(observer.values)
                records.append(result.detach())
            self.assertTrue(torch.equal(records[0], records[1]))
            self.assertTrue(torch.equal(quantizedForward(self.model, self.features, profile),
                                        qatForward(self.model, self.features, profile)))

    def test_profile_configuration_and_alias_guards(self):
        profile = calibrateModel(self.model, self.features, maxSamples=2)
        groups = scaleGroups(profile)
        self.assertIn(['blocks.0.conv', 'blocks.0.x'], groups)
        self.assertIn(['blocks.0.deltaProjection', 'blocks.0.delta'], groups)
        reordered = copy.deepcopy(profile)
        reordered['nodes'] = dict(reversed(list(reordered['nodes'].items())))
        reorderedGroups = scaleGroups(reordered)
        for alias in ['blocks.0.x', 'blocks.0.delta']:
            self.assertEqual(sum(group.count(alias) for group in reorderedGroups), 1)
        self.assertIn(['blocks.0.conv', 'blocks.0.x'], reorderedGroups)
        self.assertIn(['blocks.0.deltaProjection', 'blocks.0.delta'], reorderedGroups)
        missing = copy.deepcopy(profile)
        del missing['modelConfig']
        with self.assertRaisesRegex(ValueError, 'requires model configuration'):
            quantizedForward(self.model, self.features, missing)
        with self.assertRaisesRegex(ValueError, 'requires model configuration'):
            validateInitialProvenance(missing, self.config, 'unused', {})
        mismatched = copy.deepcopy(profile)
        mismatched['modelConfig']['norm_epsilon'] *= 2
        with self.assertRaisesRegex(ValueError, 'configuration differ'):
            quantizedForward(self.model, self.features, mismatched)
        with self.assertRaisesRegex(ValueError, 'configuration differs'):
            validateInitialProvenance(mismatched, self.config, 'unused', {})

    def test_pwl_histogram_follows_only_existing_activations(self):
        values = torch.tensor([-.5, 0., .5, 1.5])
        histogram = ActivationHistogram(bins=100)
        for name in ['blocks.0.conv', 'blocks.0.gateInput', 'blocks.0.expInput']:
            histogram(name, values)
        self.assertEqual(histogram.elements['silu'], 4)
        self.assertEqual(histogram.outside['exp'], 1)
        self.assertEqual(histogram.edges['exp'][0], -4.)
        self.assertEqual(histogram.edges['exp'][-1], 1.)
        fitted = calibrateKnots(self.model, self.features, maxSamples=2, batchSize=2)
        self.assertEqual(len(fitted['expKnots']), len(EXP_KNOTS))
        self.assertEqual(fitted['expKnots'][0], -4.)
        self.assertEqual(fitted['expKnots'][-1], 1.)
        self.assertIn(0., fitted['expKnots'])
        self.assertEqual(fitted['histogram']['elements']['silu'], 16)


if __name__ == '__main__':
    unittest.main()
