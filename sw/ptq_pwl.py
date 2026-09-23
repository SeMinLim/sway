#!/usr/bin/env python3
"""Fit inference-only PWL secant knots to training activation histograms.

No parameter updates or quantization-aware training are performed. The exact
FP32 model is only observed. Optional validation compares three predetermined
PWL choices and never opens the test arrays.
"""
import argparse
import copy
import hashlib
from pathlib import Path

import numpy as np
import torch

import model as modelModule
from model import createModel, forwardModel
from train import evaluate, fileSHA256, loadArrays, saveJSON


def functionValues(value, kind):
    if kind == 'exp':
        return np.exp(value)
    return value / (1.0 + np.exp(-value))


class ActivationHistogram:
    def __init__(self, bins=14000):
        self.edges = {'silu': np.linspace(-7.0, 7.0, bins + 1),
                      'exp': np.linspace(-4.0, 0.0, bins + 1)}
        self.counts = {kind: np.zeros(bins, dtype=np.int64) for kind in self.edges}
        self.elements = {kind: 0 for kind in self.edges}
        self.outside = {kind: 0 for kind in self.edges}

    def __call__(self, name, value):
        kind = None
        if name.endswith('.conv') or name.endswith('.gateInput'):
            kind = 'silu'
        elif name.endswith('.expInput'):
            kind = 'exp'
        if kind is not None:
            array = value.detach().cpu().numpy().ravel()
            lower, upper = self.edges[kind][0], self.edges[kind][-1]
            # Zero-valued ReLU branches are exact with the required exp knot 0.
            active = array[(array >= lower) & (array <= upper)]
            if kind == 'exp':
                active = active[active != 0.0]
            self.counts[kind] += np.histogram(active, self.edges[kind])[0]
            self.elements[kind] += int(array.size)
            self.outside[kind] += int(((array < lower) | (array > upper)).sum())
        return value


def fitSecants(edges, counts, kind, segments, gridStep):
    """Exact dynamic program on a fixed endpoint grid, weighted by train counts."""
    lower, upper = float(edges[0]), float(edges[-1])
    grid = np.linspace(lower, upper, round((upper - lower) / gridStep) + 1)
    x = (edges[:-1] + edges[1:]) * 0.5
    y = functionValues(x, kind)
    w = counts.astype(np.float64) / max(1, counts.sum())
    terms = np.stack([w, w * x, w * x * x, w * y, w * x * y, w * y * y], axis=1)
    prefix = np.vstack([np.zeros((1, 6)), np.cumsum(terms, axis=0)])
    pos = np.searchsorted(x, grid, side='left')
    boundaryY = functionValues(grid, kind)
    size = len(grid)
    costs = np.full((size, size), np.inf)
    for start in range(size - 1):
        ends = np.arange(start + 1, size)
        slope = (boundaryY[ends] - boundaryY[start]) / (grid[ends] - grid[start])
        intercept = boundaryY[start] - slope * grid[start]
        mass, sx, sxx, sy, sxy, syy = (prefix[pos[ends]] - prefix[pos[start]]).T
        cost = syy + slope * slope * sxx + intercept * intercept * mass
        cost += 2 * slope * intercept * sx - 2 * slope * sxy - 2 * intercept * sy
        costs[start, ends] = np.maximum(cost, 0.0)
    # Keep SiLU(0)=0 exact as well as the domain endpoints.
    if lower < 0.0 < upper:
        zero = int(np.argmin(np.abs(grid)))
        costs[:zero, zero + 1:] = np.inf
    scores = np.full((segments + 1, size), np.inf)
    previous = np.full((segments + 1, size), -1, dtype=np.int64)
    scores[0, 0] = 0.0
    for count in range(1, segments + 1):
        for end in range(count, size):
            candidate = scores[count - 1, :end] + costs[:end, end]
            selected = int(np.argmin(candidate))
            scores[count, end] = candidate[selected]
            previous[count, end] = selected
    indices = [size - 1]
    for count in range(segments, 0, -1):
        indices.append(int(previous[count, indices[-1]]))
    indices.reverse()
    knots = np.round(grid[indices], 10).tolist()
    if len(knots) != segments + 1 or knots[0] != lower or knots[-1] != upper:
        raise RuntimeError('Invalid fitted PWL knots')
    return knots, float(scores[segments, -1])


def histogramMSE(edges, counts, knots, kind):
    x = (edges[:-1] + edges[1:]) * 0.5
    exact = functionValues(x, kind)
    approximate = np.interp(x, knots, functionValues(np.asarray(knots), kind))
    return float(np.sum(counts * np.square(exact - approximate)) / max(1, counts.sum()))


def calibrateKnots(network, trainFeatures, maxSamples=2048, seed=0, batchSize=256):
    generator = np.random.default_rng(seed)
    indices = generator.permutation(len(trainFeatures))[:min(maxSamples, len(trainFeatures))]
    histogram = ActivationHistogram()
    network.eval()
    with torch.no_grad():
        for offset in range(0, len(indices), batchSize):
            features = trainFeatures[indices[offset:offset + batchSize]]
            forwardModel(network, features, observer=histogram, usePWL=False)
    silu, siluObjective = fitSecants(histogram.edges['silu'], histogram.counts['silu'],
                                   'silu', 17, 0.05)
    exp, expObjective = fitSecants(histogram.edges['exp'], histogram.counts['exp'],
                                 'exp', 10, 0.02)
    exp.append(1.0)
    result = {'siluKnots': silu, 'expKnots': exp,
              'method': 'train-activation histogram minimum-MSE secant grid dynamic program',
              'calibrationSplit': 'train', 'calibrationSamples': len(indices),
              'calibrationSeed': seed,
              'calibrationIndexSHA256': hashlib.sha256(indices.astype('<i8').tobytes()).hexdigest(),
              'modelParametersUpdated': False, 'testUsed': False,
              'histogram': {'bins': 14000, 'elements': histogram.elements,
                            'outsideDomain': histogram.outside,
                            'expZeroValuesExcludedFromObjective': True},
              'endpointGridStep': {'silu': 0.05, 'exp': 0.02},
              'trainHistogramMSE': {'fitted': {'silu': siluObjective, 'exp': expObjective},
                                    'baseline': {}}}
    for kind, knots in [('silu', modelModule.SILU_KNOTS), ('exp', modelModule.EXP_KNOTS)]:
        result['trainHistogramMSE']['baseline'][kind] = histogramMSE(
            histogram.edges[kind], histogram.counts[kind], knots, kind)
    return result


def parameterSHA256(parameters):
    digest = hashlib.sha256()
    for name in sorted(parameters):
        tensor = parameters[name].detach().contiguous().cpu()
        digest.update(name.encode())
        digest.update(str(tensor.dtype).encode())
        digest.update(str(tuple(tensor.shape)).encode())
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def applyKnots(checkpoint, profile):
    """An inference configuration change; model and optimizer tensors are intact."""
    fitted = copy.copy(checkpoint)
    fitted['config'] = dict(checkpoint['config'])
    fitted['config']['silu_knots'] = list(profile['siluKnots'])
    fitted['config']['exp_knots'] = list(profile['expKnots'])
    fitted['ptq_pwl_calibration'] = copy.deepcopy(profile)
    fitted['ptq_pwl_calibration']['sourceParameterSHA256'] = parameterSHA256(checkpoint['model'])
    fitted['ptq_pwl_calibration']['fittedParameterSHA256'] = parameterSHA256(fitted['model'])
    return fitted


def validateSource(checkpoint, dataDirectory, validate=False):
    """Check FP32 selection and the identity of every split this stage reads."""
    modes = [checkpoint.get('mode'), checkpoint.get('training_mode'),
             checkpoint.get('training', {}).get('mode')]
    if (checkpoint.get('quantization_profile') is not None
            or any(mode in ['pwl', 'qat'] for mode in modes)):
        raise ValueError('PWL PTQ calibration requires an exact-trained FP32 checkpoint')
    if 'epoch' not in checkpoint or checkpoint['epoch'] != checkpoint.get('best_epoch'):
        raise ValueError('PWL PTQ calibration requires a validation-selected best checkpoint')
    expected = checkpoint.get('dataset_sha256', {})
    checked = {}
    for split in ['train', 'validate'] if validate else ['train']:
        for prefix in ['featuremap_', 'labels_']:
            name = prefix + split + '.npy'
            if name not in expected:
                raise ValueError('Source checkpoint has no dataset hash: ' + name)
            digest = fileSHA256(Path(dataDirectory) / name)
            if digest != expected[name]:
                raise ValueError('Data differs from FP32 source training: ' + name)
            checked[name] = digest
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--output-checkpoint', help='Save unchanged FP32 weights with fitted inference PWL config')
    parser.add_argument('--samples', type=int, default=2048)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    if min(args.samples, args.batch_size, args.threads) < 1:
        raise ValueError('samples, batch-size, and threads must be positive')
    torch.set_num_threads(args.threads)
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
    datasetHashes = validateSource(checkpoint, args.data, args.validate)
    network = createModel(checkpoint['config'])
    network.load_state_dict(checkpoint['model'])
    trainFeatures, _ = loadArrays(args.data, 'train')
    profile = calibrateKnots(network, trainFeatures, args.samples, args.seed, args.batch_size)
    profile['checkpointSHA256'] = fileSHA256(args.checkpoint)
    profile['sourceCheckpoint'] = args.checkpoint
    profile['sourceSHA256'] = fileSHA256(__file__)
    profile['datasetSHA256'] = datasetHashes
    profile['trainFeaturesSHA256'] = fileSHA256(Path(args.data) / 'featuremap_train.npy')
    print(profile, flush=True)
    if args.validate:
        validationFeatures, validationLabels = loadArrays(args.data, 'validate')
        originalConfig = dict(network.config)
        originalSilu = originalConfig.get('silu_knots', modelModule.SILU_KNOTS)
        originalExp = originalConfig.get('exp_knots', modelModule.EXP_KNOTS)
        candidates = [('baseline', originalSilu, originalExp),
                      ('exp_only', originalSilu, profile['expKnots']),
                      ('both', profile['siluKnots'], profile['expKnots'])]
        validation = {}
        validation['exact'] = evaluate(network, validationFeatures, validationLabels,
                                       args.batch_size, torch.device('cpu'))
        print('exact', validation['exact'], flush=True)
        try:
            for name, silu, exp in candidates:
                network.config['silu_knots'], network.config['exp_knots'] = silu, exp
                metrics = evaluate(network, validationFeatures, validationLabels, args.batch_size,
                                   torch.device('cpu'), lambda net, x: forwardModel(net, x, usePWL=True))
                validation[name] = metrics
                print(name, metrics, flush=True)
        finally:
            network.config = originalConfig
        profile['validation'] = validation
        profile['validationLabelsSHA256'] = fileSHA256(Path(args.data) / 'labels_validate.npy')
        profile['validationFeaturesSHA256'] = fileSHA256(Path(args.data) / 'featuremap_validate.npy')
    if args.output_checkpoint:
        fitted = applyKnots(checkpoint, profile)
        fittedNetwork = createModel(fitted['config'])
        fittedNetwork.load_state_dict(fitted['model'])
        with torch.no_grad():
            originalPrediction = forwardModel(network, trainFeatures[:16], usePWL=False)
            fittedPrediction = forwardModel(fittedNetwork, trainFeatures[:16], usePWL=False)
        if not torch.equal(originalPrediction, fittedPrediction):
            raise RuntimeError('Inference PWL configuration changed exact FP32 output')
        destination = Path(args.output_checkpoint)
        destination.parent.mkdir(parents=True, exist_ok=True)
        torch.save(fitted, destination)
        profile['outputCheckpointSHA256'] = fileSHA256(destination)
        profile['parameterSHA256'] = parameterSHA256(fitted['model'])
        profile['exactOutputBitIdenticalOnTrainProbe'] = True
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    saveJSON(args.output, profile)


if __name__ == '__main__':
    main()
