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
    parser.add_argument('--source-checkpoint', help='QAT source FP32 checkpoint for a fixed-profile before/after comparison')
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
    sourceHashes = {name: fileSHA256(Path(__file__).parent / name)
        for name in ['evaluate.py', 'train.py', 'model.py', 'quantize.py', 'finetune.py']}
    savedProfile = checkpoint.get('quantization_profile')
    if savedProfile is not None:
        for name in ['model.py', 'quantize.py']:
            if checkpoint.get('source_sha256', {}).get(name) != sourceHashes[name]:
                raise ValueError('QAT inference source differs from training: ' + name)
    model = createModel(checkpoint['config'])
    model.load_state_dict(checkpoint['model'])
    model.eval()
    result = {'checkpoint_sha256': fileSHA256(args.checkpoint),
              'checkpoint_epoch': checkpoint['epoch'],
              'evaluation_source_sha256': sourceHashes,
              'selection': 'best validation mean coordinatewise RMSE; no test tuning',
              'config': checkpoint['config'], 'metrics': {}}
    profile = None
    sourceModel = None
    if args.source_checkpoint:
        if savedProfile is None:
            raise ValueError('--source-checkpoint requires a QAT checkpoint')
        sourceHash = fileSHA256(args.source_checkpoint)
        if sourceHash != checkpoint['source_checkpoint_sha256']:
            raise ValueError('Source checkpoint differs from the QAT initialization')
        source = torch.load(args.source_checkpoint, map_location='cpu', weights_only=False)
        if source['config'] != checkpoint['config'] or source['dataset_sha256'] != checkpoint['dataset_sha256']:
            raise ValueError('Source model configuration or data differs from QAT')
        sourceModel = createModel(source['config'])
        sourceModel.load_state_dict(source['model'])
        result['source_checkpoint_sha256'] = sourceHash
        result['source_checkpoint_epoch'] = source['epoch']
    if savedProfile is not None:
        args.quantize = True
        result['training_mode'] = 'qat'
    if args.quantize:
        from quantize import calibrateModel, quantizedForward, exportQuantizedModel
        for name in checkpoint['dataset_sha256']:
            if fileSHA256(Path(args.data) / name) != checkpoint['dataset_sha256'][name]:
                raise ValueError('Calibration/validation data differs from training: ' + name)
        if savedProfile is not None:
            # QAT optimized this exact frozen numerical graph. Recalibrating
            # here would silently evaluate a different model after training.
            profile = savedProfile
            from finetune import profileSHA256
            if profileSHA256(profile) != checkpoint['quantization_profile_sha256']:
                raise ValueError('Stored QAT profile digest does not match')
            if profile['calibrationSplit'] != 'train':
                raise ValueError('QAT checkpoint has a non-training calibration')
            result['quantization_selection'] = {
                'source': 'frozen profile stored in the validation-selected QAT checkpoint',
                'selected_percentile': profile['percentile'],
                'selected_policy': profile.get('calibration_policy'),
                'training_selection': checkpoint['quantization_selection'], 'test_used': False}
        else:
            xTrain, _ = loadArrays(args.data, 'train')
            xValidation, yValidation = loadArrays(args.data, 'validate')
            candidates = []
            bestScore = float('inf')
            # Fit scales on train, select on validation, then open test data.
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
            del xTrain, xValidation, yValidation
        manifest = exportQuantizedModel(model, profile, output / 'export')
        if savedProfile is not None:
            headerPath = output / 'export' / manifest['cArrays']
            headerPath.write_text(headerPath.read_text().replace('PTQ parameters.', 'QAT parameters.'))
        # Preserve the established binary layout while identifying how these
        # particular weights and scales were produced.
        manifest['trainingProvenance'] = {
            'mode': 'qat' if savedProfile is not None else 'ptq',
            'checkpointSHA256': result['checkpoint_sha256'],
            'checkpointEpoch': checkpoint['epoch'],
            'sourceCheckpointSHA256': checkpoint.get('source_checkpoint_sha256'),
            'quantizationProfileSHA256': checkpoint.get('quantization_profile_sha256'),
        }
        manifest['evaluationSourceSHA256'] = sourceHashes
        saveJSON(output / 'export' / 'manifest.json', manifest)
        saveJSON(output / 'calibration.json', profile)
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
        mode = 'int8_qat' if savedProfile is not None else 'int8_ptq'
        result['metrics'][mode] = computeMetrics(prediction, yTest.numpy())
        result['metrics'][mode]['evaluation_seconds'] = time.perf_counter() - started
        result['quantization_statistics'] = statistics
        np.save(output / ('predictions_' + mode + '.npy'), prediction, allow_pickle=False)
        print('[STEP 3] %s: %s' % (mode, result['metrics'][mode]), flush=True)
    if sourceModel is not None:
        # Both versions use exactly the same test frames and frozen scales.
        # These measurements cannot influence the already selected checkpoint.
        for mode in ['fp32_source', 'int8_before_qat']:
            started = time.perf_counter()
            forward = None
            if mode == 'int8_before_qat':
                forward = lambda network, features: quantizedForward(network, features, profile)
            prediction = predict(sourceModel, xTest, args.batch_size, torch.device('cpu'), forward)
            result['metrics'][mode] = computeMetrics(prediction, yTest.numpy())
            result['metrics'][mode]['evaluation_seconds'] = time.perf_counter() - started
            np.save(output / ('predictions_' + mode + '.npy'), prediction, allow_pickle=False)
            print('[STEP 3] %s: %s' % (mode, result['metrics'][mode]), flush=True)
    result['dataset_test_sha256'] = {
        name: fileSHA256(Path(args.data) / name)
        for name in ['featuremap_test.npy', 'labels_test.npy']
    }
    saveJSON(output / 'metrics.json', result)
    print('[STEP 4] Evaluation saved: %s' % (output / 'metrics.json'), flush=True)


if __name__ == '__main__':
    main()
