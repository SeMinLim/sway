#!/usr/bin/env python3
"""Refit the FP32 output head on training activations, with validation fallback.

The backbone, exact nonlinearities, and architecture stay fixed. No observer
quantizes values, and no quantized predictions or test arrays are used.
"""
import argparse
import copy
from pathlib import Path
import platform
import sys
import time

import numpy as np
import torch

from finetune import checkSource, saveCheckpoint
from model import createModel, forwardModel
from train import evaluate, fileSHA256, loadArrays, saveJSON


class CaptureHead:
    def __init__(self):
        self.activation = None

    def __call__(self, name, value):
        if name == 'headActivation':
            self.activation = value
        return value


def hiddenActivations(model, features, batchSize, device):
    model.eval()
    activations = []
    with torch.no_grad():
        for start in range(0, len(features), batchSize):
            capture = CaptureHead()
            forwardModel(model, features[start:start + batchSize].to(device),
                observer=capture, usePWL=False)
            if capture.activation is None or not torch.isfinite(capture.activation).all():
                raise RuntimeError('Missing or nonfinite hidden activation')
            activations.append(capture.activation.cpu().numpy())
    return np.concatenate(activations).astype(np.float64)


def fitLinearHead(activations, labels, ridge):
    activations = np.asarray(activations, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.float64)
    if (activations.ndim != 2 or labels.ndim != 2 or len(activations) != len(labels)
            or not len(activations) or not np.isfinite(activations).all()
            or not np.isfinite(labels).all() or not np.isfinite(ridge) or ridge < 0):
        raise ValueError('Invalid training activations, labels, or ridge')
    hiddenMean = activations.mean(axis=0)
    labelMean = labels.mean(axis=0)
    centeredHidden = activations - hiddenMean
    centeredLabels = labels - labelMean
    if ridge == 0:
        coefficients = np.linalg.lstsq(centeredHidden, centeredLabels, rcond=None)[0]
    else:
        gram = centeredHidden.T @ centeredHidden / len(activations)
        rhs = centeredHidden.T @ centeredLabels / len(activations)
        coefficients = np.linalg.solve(gram + ridge * np.eye(gram.shape[0]), rhs)
    bias = labelMean - hiddenMean @ coefficients
    return coefficients.T.astype(np.float32), bias.astype(np.float32)


def refit(args):
    if args.batch_size < 1 or args.threads < 1:
        raise ValueError('batch-size and threads must be positive')
    if not args.ridges or any(not np.isfinite(ridge) or ridge < 0 for ridge in args.ridges):
        raise ValueError('Ridges must be finite and nonnegative')
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be new or empty')
    sourcePath = Path(args.checkpoint).resolve()
    if sourcePath.parent == output.resolve():
        raise ValueError('Output must differ from the source checkpoint directory')
    torch.set_num_threads(args.threads)
    device = torch.device(args.device)
    datasetHashes = {}
    for split in ['train', 'validate']:
        for prefix in ['featuremap_', 'labels_']:
            path = Path(args.data) / (prefix + split + '.npy')
            datasetHashes[path.name] = fileSHA256(path)
    sourceHash = fileSHA256(sourcePath)
    source = torch.load(sourcePath, map_location='cpu', weights_only=False)
    if fileSHA256(sourcePath) != sourceHash:
        raise ValueError('Source checkpoint changed while loading')
    checkSource(source, datasetHashes)
    if (source.get('mode') in ['qat', 'pwl']
            or source.get('training', {}).get('mode') in ['qat', 'pwl']
            or source.get('quantization_profile') is not None):
        raise ValueError('Head refit requires a non-QAT exact FP32 source')
    model = createModel(source['config']).to(device)
    model.load_state_dict(source['model'])
    xTrain, yTrain = loadArrays(args.data, 'train')
    xValidation, yValidation = loadArrays(args.data, 'validate')
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    baseline = evaluate(model, xValidation, yValidation, args.batch_size, device)
    bestScore = baseline['rmse_coordinate_mean_cm']
    bestModel = copy.deepcopy(model.state_dict())
    bestMetrics = baseline
    bestIndex = 0
    candidates = [{'index': 0, 'method': 'source_fallback', 'ridge': None,
        'validation': baseline}]
    print('Source validation RMSE %.6f cm' % bestScore, flush=True)
    hidden = hiddenActivations(model, xTrain, args.batch_size, device)
    for index, ridge in enumerate(args.ridges, start=1):
        weight, bias = fitLinearHead(hidden, yTrain.numpy(), ridge)
        with torch.no_grad():
            model.headOutput.weight.copy_(torch.from_numpy(weight).to(device))
            model.headOutput.bias.copy_(torch.from_numpy(bias).to(device))
        metrics = evaluate(model, xValidation, yValidation, args.batch_size, device)
        score = metrics['rmse_coordinate_mean_cm']
        candidates.append({'index': index, 'method': 'training_head_refit',
            'ridge': ridge, 'validation': metrics})
        if score < bestScore:
            bestScore, bestIndex, bestMetrics = score, index, metrics
            bestModel = copy.deepcopy(model.state_dict())
        print('Ridge %g | validation RMSE %.6f cm' % (ridge, score), flush=True)
    sourceHashes = {name: fileSHA256(Path(__file__).parent / name)
        for name in ['refit_head.py', 'model.py', 'train.py', 'finetune.py']}
    checkpoint = {'format_version': 2, 'mode': 'fp32_head_refit',
        'model': bestModel, 'config': source['config'],
        'epoch': bestIndex, 'best_epoch': bestIndex,
        'best_validation_rmse_cm': bestScore, 'validation': bestMetrics,
        'training': vars(args), 'dataset_sha256': datasetHashes,
        'source_checkpoint_sha256': sourceHash,
        'source_checkpoint_epoch': source['epoch'], 'source_mode': source.get('mode', 'fp32_mse'),
        'source_sha256': sourceHashes, 'candidates': candidates,
        'selected_ridge': candidates[bestIndex]['ridge'],
        'training_objective': 'mean squared residual + ridge * squared output weights; bias unpenalized',
        'validation_forward': 'forwardModel(usePWL=False)',
        'test_used_for_selection': False, 'quantized_feedback_used': False}
    saveCheckpoint(output / 'best.pt', checkpoint)
    run = {key: value for key, value in checkpoint.items() if key not in ['model']}
    run.update({'status': 'completed', 'source_checkpoint': str(sourcePath),
        'train_frames': len(xTrain), 'validation_frames': len(xValidation),
        'best_checkpoint_sha256': fileSHA256(output / 'best.pt'),
        'elapsed_seconds': time.perf_counter() - started,
        'environment': {'python': sys.version, 'torch': str(torch.__version__),
            'numpy': np.__version__, 'platform': platform.platform(), 'device': str(device)},
        'selection_metric': 'minimum full-validation mean coordinatewise RMSE, including source fallback'})
    saveJSON(output / 'run.json', run)
    print('Selected index %d | validation RMSE %.6f cm' % (bestIndex, bestScore), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--ridges', type=float, nargs='+', default=[0.0, 0.0001, 0.001])
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--device', default='cpu')
    refit(parser.parse_args())


if __name__ == '__main__':
    main()
