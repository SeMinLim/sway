#!/usr/bin/env python3
"""Check numerical semantics and checkpoint continuity using independent oracles."""

import argparse
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
import tempfile

import numpy as np
import torch

from model import createModel, forwardModel, rangeNorm
from train import computeMetrics, fileSHA256, loadResume, train


class Capture:
    def __init__(self):
        self.values = {}
        self.ssmInputs = {}

    def __call__(self, name, value):
        self.values[name] = value.detach().clone()
        return value

    def ssm(self, prefix, x, delta, A, B, C, D):
        self.ssmInputs[prefix] = [v.detach().cpu().numpy() for v in [x, delta, A, B, C, D]]
        return None


def checkRecurrence(capture):
    # Expand the recurrence into a weighted sum of all previous inputs. This
    # oracle has no mutable recurrent state and catches state-order mistakes.
    largestError = 0.0
    for prefix, values in capture.ssmInputs.items():
        x, delta, A, B, C, D = values
        expected = np.zeros_like(x)
        for sample in range(x.shape[0]):
            for token in range(x.shape[1]):
                for channel in range(x.shape[2]):
                    total = float(D[channel] * x[sample, token, channel])
                    for state in range(A.shape[1]):
                        stateValue = 0.0
                        for source in range(token + 1):
                            elapsed = float(delta[sample, source + 1:token + 1, channel].sum())
                            decay = math.exp(float(A[channel, state]) * elapsed)
                            stateValue += float(delta[sample, source, channel] * B[sample, source, state]
                                                * x[sample, source, channel]) * decay
                        total += float(C[sample, token, state]) * stateValue
                    expected[sample, token, channel] = total
        actual = capture.values[prefix + '.ssmY'].cpu().numpy()
        np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-13)
        largestError = max(largestError, float(np.max(np.abs(actual - expected))))
    return largestError


def checkMetrics():
    # Axis-constant errors 1, 2, 3 cm imply mean coordinate RMSE 2 cm,
    # whereas pooled RMSE is sqrt(14/3) cm. The two must not be confused.
    labels = np.zeros((2, 57), dtype=np.float64)
    prediction = np.tile(np.repeat([0.01, 0.02, 0.03], 19), (2, 1))
    result = computeMetrics(prediction, labels)
    np.testing.assert_allclose(result['mae_cm'], 2.0, atol=1e-14)
    np.testing.assert_allclose(result['rmse_coordinate_mean_cm'], 2.0, atol=1e-14)
    np.testing.assert_allclose(result['rmse_global_cm'], math.sqrt(14.0 / 3.0), atol=1e-14)
    for axis, value in zip(['x', 'y', 'z'], [1.0, 2.0, 3.0]):
        np.testing.assert_allclose(result['axis_rmse_cm'][axis], value, atol=1e-14)
    # Unequal errors over frames distinguish per-frame averaging from the
    # required per-coordinate square root taken after the frame average.
    prediction[0] = 0.0
    result = computeMetrics(prediction, labels)
    np.testing.assert_allclose(result['rmse_coordinate_mean_cm'], math.sqrt(2.0), atol=1e-14)
    return True


def checkGradients(model, features):
    target = torch.randn(features.shape[0], 57, dtype=torch.float64)
    loss = (model(features) - target).square().mean()
    loss.backward()
    for name, parameter in model.named_parameters():
        if parameter.grad is None or not torch.isfinite(parameter.grad).all():
            raise AssertionError('Missing or nonfinite gradient: ' + name)
        if parameter.grad.abs().max().item() == 0:
            raise AssertionError('All-zero gradient: ' + name)
    names = ['embedding.weight', 'blocks.0.A_log', 'blocks.0.dtWeight',
             'blocks.0.xWeight', 'blocks.0.outWeight', 'headOutput.weight']
    parameters = dict(model.named_parameters())
    checked = {}
    for name in names:
        parameter = parameters[name]
        index = int(parameter.grad.abs().reshape(-1).argmax())
        analytical = parameter.grad.reshape(-1)[index].item()
        original = parameter.detach().reshape(-1)[index].item()
        step = 1e-5
        with torch.no_grad():
            parameter.reshape(-1)[index] = original + step
            plus = (model(features) - target).square().mean().item()
            parameter.reshape(-1)[index] = original - step
            minus = (model(features) - target).square().mean().item()
            parameter.reshape(-1)[index] = original
        numerical = (plus - minus) / (2.0 * step)
        np.testing.assert_allclose(analytical, numerical, rtol=0.002, atol=2e-10,
                                   err_msg='Finite difference: ' + name)
        checked[name] = {'autograd': analytical, 'finite_difference': numerical}
    return checked


def checkResume(config, configPath):
    # Real optimizer/scheduler steps on a tiny synthetic fixture establish
    # exact continuation, rather than only serialization compatibility.
    with tempfile.TemporaryDirectory(prefix='sway-check-') as temporary:
        root = Path(temporary)
        data = root / 'data'
        data.mkdir()
        rng = np.random.default_rng(914)
        for split, count in [('train', 8), ('validate', 4)]:
            np.save(data / ('featuremap_' + split + '.npy'), rng.normal(size=(count, 8, 8, 5)))
            np.save(data / ('labels_' + split + '.npy'), rng.normal(size=(count, 57)))
        common = dict(data=str(data), config=str(configPath), epochs=2, batch_size=4,
                      learning_rate=0.001, weight_decay=0.0001, patience=10,
                      lr_patience=3, min_delta=0.0001, clip_grad=1.0,
                      seed=71, threads=1, device='cpu', resume=None)
        continuous = argparse.Namespace(**common, output=str(root / 'continuous'))
        splitRun = argparse.Namespace(**common, output=str(root / 'resumed'))
        with redirect_stdout(io.StringIO()):
            train(continuous)
            splitRun.epochs = 1
            train(splitRun)
            splitRun.epochs = 2
            splitRun.resume = str(root / 'resumed' / 'last.pt')
            train(splitRun)
        whole = torch.load(root / 'continuous' / 'last.pt', weights_only=False)
        resumed = torch.load(root / 'resumed' / 'last.pt', weights_only=False)
        for name in whole['model']:
            torch.testing.assert_close(whole['model'][name], resumed['model'][name], rtol=0, atol=0)
        assert whole['best_epoch'] == resumed['best_epoch']
        assert whole['best_validation_rmse_cm'] == resumed['best_validation_rmse_cm']
        assert resumed['best_checkpoint_sha256'] == fileSHA256(root / 'resumed' / 'best.pt')
        splitRun.epochs = 3
        hashes = resumed['dataset_sha256']
        changed = dict(hashes)
        changed['labels_train.npy'] = 'changed'
        try:
            loadResume(splitRun, root / 'resumed', config, changed, 'cpu')
        except ValueError as error:
            assert 'dataset hashes' in str(error)
        else:
            raise AssertionError('Changed resume dataset was accepted')
        with (root / 'resumed' / 'best.pt').open('ab') as stream:
            stream.write(b'changed')
        try:
            loadResume(splitRun, root / 'resumed', config, hashes, 'cpu')
        except ValueError as error:
            assert 'best.pt' in str(error)
        else:
            raise AssertionError('Changed best checkpoint was accepted')
    return True


def checkQuantization(config, features):
    from quantize import calibrateModel, exportQuantizedModel, quantizedForward, verifyExport
    model = createModel(config).eval()
    features = features.float()
    profile = calibrateModel(model, features, batchSize=2, maxSamples=2)
    expected = quantizedForward(model, features, profile)
    assert torch.isfinite(expected).all()
    separate = torch.cat([quantizedForward(model, features[i:i + 1], profile) for i in range(2)])
    torch.testing.assert_close(expected, separate, rtol=0, atol=0)
    with tempfile.TemporaryDirectory(prefix='sway-export-check-') as temporary:
        root = Path(temporary)
        manifest = exportQuantizedModel(model, profile, root)
        assert verifyExport(root)
        parameters = dict(model.named_parameters())
        for entry in manifest['tensors']:
            value = parameters[entry['trainingParameterName']].detach().numpy()
            if entry['trainingParameterName'].endswith('.A_log'):
                value = -np.exp(value)
            expectedIntegers = np.clip(np.rint(value / entry['scale']), -128, 127).astype(np.int8)
            actual = np.fromfile(root / entry['binary'], dtype=np.int8).reshape(entry['shape'])
            np.testing.assert_array_equal(actual, expectedIntegers)
        # A fresh model reloaded from the exported FP32 arrays and serialized
        # scales must reproduce the same quantized reference outputs.
        restored = createModel(config).eval()
        with np.load(root / manifest['fp32Parameters'], allow_pickle=False) as arrays:
            restored.load_state_dict({name: torch.from_numpy(arrays[name]) for name in arrays.files})
        restoredProfile = json.loads((root / manifest['quantization']).read_text())
        torch.testing.assert_close(expected, quantizedForward(restored, features, restoredProfile), rtol=0, atol=0)
    return {'finite_output': True, 'batch_independence': True,
            'binary_hex_and_parameter_values': True, 'serialized_model_scale_roundtrip': True}



def checkIntegerState():
    from quantize import integerSSM, shiftInteger
    values = torch.tensor([-7, -5, -3, -1, 1, 3, 5, 7], dtype=torch.int64)
    expected = torch.tensor([-4, -2, -2, 0, 0, 2, 2, 4], dtype=torch.int64)
    torch.testing.assert_close(shiftInteger(values, 0, 1), expected, rtol=0, atol=0)
    x = torch.ones((1, 1, 1), dtype=torch.int64)
    a = torch.zeros((1, 1, 1, 1), dtype=torch.int64)
    b = torch.full_like(a, 64)
    c = torch.full((1, 1, 1), 127, dtype=torch.int64)
    d = torch.zeros(1, dtype=torch.int64)
    # Current=64 and stored=0: computing the output after truncation would
    # incorrectly produce zero instead of the exact expected code 127.
    output, trace = integerSSM(x, a, b, c, d, 0, 0, 0, 0, 7, 6, trace=True)
    assert output.item() == 127 and trace['currentState'].item() == 64
    assert trace['storedState'].item() == 0
    _, trace = integerSSM(x, a, torch.full_like(a, -1), c, d,
                          0, 0, 0, 0, 7, 0, trace=True)
    assert trace['currentState'].item() == -1 and trace['storedState'].item() == -1
    _, trace = integerSSM(torch.full_like(x, 127), a, torch.full_like(a, 127),
                          torch.zeros_like(c), d, 0, 14, 0, 0, 7, 0, trace=True)
    assert trace['currentStateClipped'] == 1
    assert trace['currentState'].item() == 8388607 and trace['storedState'].item() == 65535
    return {'nearest_even_rescaling': True, 'output_before_state_truncation': True,
            'negative_arithmetic_shift': True, 'int24_saturation': True}

def runChecks(configPath):
    torch.set_num_threads(1)
    torch.manual_seed(2309)
    config = json.loads(Path(configPath).read_text())
    model = createModel(config).double().eval()
    features = torch.randn(2, 8, 8, 5, dtype=torch.float64)
    capture = Capture()
    with torch.no_grad():
        output = forwardModel(model, features, capture)
    recurrenceError = checkRecurrence(capture)
    expectedPatches = []
    for row in range(0, 8, 2):
        for column in range(0, 8, 2):
            expectedPatches.append(features[:, row:row + 2, column:column + 2, :].reshape(2, 20))
    torch.testing.assert_close(capture.values['patches'], torch.stack(expectedPatches, 1), rtol=0, atol=0)
    with torch.no_grad():
        separately = torch.cat([model(features[i:i + 1]) for i in range(2)])
        torch.testing.assert_close(output, separately, rtol=1e-12, atol=1e-13)
        model(torch.randn_like(features))
        torch.testing.assert_close(output, model(features), rtol=0, atol=0)
        torch.testing.assert_close(output.flip(0), model(features.flip(0)), rtol=1e-12, atol=1e-13)
        constant = torch.full((2, 3, 20), 4.0, dtype=torch.float64)
        weight = torch.ones(20, dtype=torch.float64)
        bias = torch.full((20,), 0.75, dtype=torch.float64)
        normalized = rangeNorm(None, 'check', constant, weight, bias, 1e-5)
        torch.testing.assert_close(normalized, torch.full_like(constant, 0.75), rtol=0, atol=0)
    gradients = checkGradients(model, features)
    return {'status': 'passed', 'patch_order': 'row-major 2x2 HWC verified',
            'recurrence_closed_form_max_abs_error': recurrenceError,
            'frame_reset_and_batch_independence': True, 'constant_range_normalization': True,
            'coordinatewise_metrics': checkMetrics(), 'gradient_finite_differences': gradients,
            'resume_continuity_and_provenance': checkResume(config, configPath),
            'quantization_export': checkQuantization(config, features),
            'integer_state': checkIntegerState(),
            'test_data_used': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).parent / 'config' / 'model.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    results = runChecks(args.config)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, allow_nan=False) + '\n')
    print(json.dumps(results, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
