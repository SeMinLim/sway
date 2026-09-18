#!/usr/bin/env python3
"""Evaluate a validation-selected checkpoint without training on test data."""
import argparse
from pathlib import Path
import time

import numpy as np
import torch

from model import createModel, forwardModel
from train import computeMetrics, evaluate, fileSHA256, loadArrays, predict, saveJSON


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--calibration-samples', type=int, default=2048)
    parser.add_argument('--quantize', action='store_true')
    parser.add_argument('--calibration-percentiles', nargs='+', type=float, default=[99.9, 100.0])
    args = parser.parse_args()
    if args.calibration_samples < 1 or args.batch_size < 1 or args.threads < 1:
        raise ValueError('calibration-samples and batch-size must be positive')
    torch.set_num_threads(args.threads)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
    if checkpoint['epoch'] != checkpoint['best_epoch']:
        raise ValueError('Evaluate the validation-selected best.pt, not a later checkpoint')
    model = createModel(checkpoint['config'])
    model.load_state_dict(checkpoint['model'])
    model.eval()
    result = {'checkpoint_sha256': fileSHA256(args.checkpoint),
              'checkpoint_epoch': checkpoint['epoch'],
              'selection': 'best validation mean coordinatewise RMSE; no test tuning',
              'config': checkpoint['config'], 'metrics': {}}
    profile = None
    if args.quantize:
        from quantize import calibrateModel, quantizedForward, exportQuantizedModel
        for name in checkpoint['dataset_sha256']:
            if fileSHA256(Path(args.data) / name) != checkpoint['dataset_sha256'][name]:
                raise ValueError('Calibration/validation data differs from training: ' + name)
        xTrain, _ = loadArrays(args.data, 'train')
        xValidation, yValidation = loadArrays(args.data, 'validate')
        candidates = []
        bestScore = float('inf')
        # Predeclared, small clipping comparison; fit scales on train only and
        # select the policy on validation before loading any test values.
        for percentile in args.calibration_percentiles:
            candidate = calibrateModel(model, xTrain, batchSize=args.batch_size,
                                       maxSamples=args.calibration_samples, percentile=percentile)
            def forwardCandidate(model, features):
                return quantizedForward(model, features, candidate)
            metrics = evaluate(model, xValidation, yValidation, args.batch_size,
                               torch.device('cpu'), forwardCandidate)
            candidates.append({'percentile': percentile, 'validation_metrics': metrics})
            if metrics['rmse_coordinate_mean_cm'] < bestScore:
                bestScore = metrics['rmse_coordinate_mean_cm']
                profile = candidate
            print('[STEP 1] PTQ percentile %.3f validation RMSE %.4f cm' %
                  (percentile, metrics['rmse_coordinate_mean_cm']), flush=True)
        result['quantization_selection'] = {'candidates': candidates,
                                            'selected_percentile': profile['percentile'],
                                            'criterion': 'validation mean coordinatewise RMSE',
                                            'test_used': False}
        exportQuantizedModel(model, profile, output / 'export')
        saveJSON(output / 'calibration.json', profile)
        del xTrain, xValidation, yValidation
    xTest, yTest = loadArrays(args.data, 'test')
    for mode in ['fp32', 'fp32_pwl']:
        started = time.perf_counter()
        forward = None
        if mode == 'fp32_pwl':
            def forward(model, features):
                return forwardModel(model, features, usePWL=True)
        prediction = predict(model, xTest, args.batch_size, torch.device('cpu'), forward)
        result['metrics'][mode] = computeMetrics(prediction, yTest.numpy())
        result['metrics'][mode]['evaluation_seconds'] = time.perf_counter() - started
        np.save(output / ('predictions_' + mode + '.npy'), prediction, allow_pickle=False)
        print('[STEP 2] %s: %s' % (mode, result['metrics'][mode]), flush=True)
    if args.quantize:
        result['quantization_scope'] = profile['simulationScope']
        statistics = {}
        started = time.perf_counter()
        def forwardQuantized(model, features):
            prediction, batchStatistics = quantizedForward(model, features, profile, returnStatistics=True)
            for blockName, values in batchStatistics.items():
                block = statistics.setdefault(blockName, {})
                for name, value in values.items():
                    if name.startswith('max'):
                        block[name] = max(block.get(name, 0), value)
                    else:
                        block[name] = block.get(name, 0) + value
            return prediction
        prediction = predict(model, xTest, args.batch_size, torch.device('cpu'), forwardQuantized)
        result['metrics']['int8_ptq'] = computeMetrics(prediction, yTest.numpy())
        result['metrics']['int8_ptq']['evaluation_seconds'] = time.perf_counter() - started
        result['quantization_statistics'] = statistics
        np.save(output / 'predictions_int8_ptq.npy', prediction, allow_pickle=False)
        print('[STEP 3] INT8 PTQ: %s' % result['metrics']['int8_ptq'], flush=True)
    result['dataset_test_sha256'] = {
        name: fileSHA256(Path(args.data) / name)
        for name in ['featuremap_test.npy', 'labels_test.npy']
    }
    saveJSON(output / 'metrics.json', result)
    print('[STEP 4] Evaluation saved: %s' % (output / 'metrics.json'), flush=True)


if __name__ == '__main__':
    main()
