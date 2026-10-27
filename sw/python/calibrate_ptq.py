#!/usr/bin/env python3
"""Calibrate frozen FP32 weights with train-only power-of-two scale search.

This is PTQ: it never creates an optimizer, calculates gradients, or updates a
parameter. It minimizes output reconstruction error against the floating-point
teacher on training frames. Full validation selects a completed calibration;
the test split is never opened here.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch

from model import createModel, forwardModel
from quantize import calibrateModel, quantizedForward
from train import evaluate, fileSHA256, loadArrays, predict, saveJSON


def setExponent(profile, name, exponent):
    entry = profile['nodes'][name]
    entry['exponent'] = int(exponent)
    entry['scale'] = 2.0 ** int(exponent)


def scaleGroups(profile):
    """Couple true aliases and the prescribed INT24/INT17 state formats."""
    names = list(profile['nodes'])
    groups = []
    consumed = set()
    for name in names:
        if name in consumed or name.endswith('.Abar'):
            continue
        if name.endswith('.currentState'):
            continue
        if name.endswith('.x') or name.endswith('.delta'):
            # Alias grouping must not depend on JSON object key order.
            continue
        group = [name]
        if name == 'input' and 'patches' in names:
            group.append('patches')
        if name.endswith('.conv'):
            group.append(name[:-len('.conv')] + '.x')
        if name.endswith('.deltaProjection'):
            group.append(name[:-len('.deltaProjection')] + '.delta')
        if name.endswith('.state'):
            current = name[:-len('.state')] + '.currentState'
            if current in names:
                group.append(current)
        if name.endswith('.residual'):
            block = int(name.split('.')[1])
            if 'blocks.%d.residual' % (block + 1) not in names and 'headInput' in names:
                group.append('headInput')
        consumed.update(group)
        groups.append(group)
    return groups


def shiftGroup(profile, group, shift):
    for name in group:
        setExponent(profile, name, profile['nodes'][name]['exponent'] + shift)


def validateInitialProvenance(profile, config, checkpointHash, inferenceHashes):
    """Check both top-level and nested provenance without precedence."""
    if profile.get('calibrationSplit') != 'train':
        raise ValueError('Initial PTQ profile must be calibrated on training data')
    suppliedConfig = profile.get('modelConfig')
    if suppliedConfig is None:
        raise ValueError('Initial profile requires model configuration')
    if suppliedConfig != config:
        raise ValueError('Initial profile model configuration differs from checkpoint')
    nested = profile.get('ptq_calibration', {})
    for suppliedHash in [profile.get('source_checkpoint_sha256'),
                         nested.get('source_checkpoint_sha256')]:
        if suppliedHash is not None and suppliedHash != checkpointHash:
            raise ValueError('Initial profile was calibrated for a different checkpoint')
    for hashes in [profile.get('inference_source_sha256', {}), nested.get('source_sha256', {})]:
        for name in ['model.py', 'quantize.py']:
            if name in hashes and hashes[name] != inferenceHashes[name]:
                raise ValueError('Initial profile inference source differs: ' + name)


def reconstructionError(model, features, teacher, profile, batchSize):
    prediction = predict(model, features, batchSize, torch.device('cpu'),
                         lambda network, batch: quantizedForward(network, batch, profile))
    error = prediction.astype(np.float64) - teacher.astype(np.float64)
    return float(np.mean(error * error))


def optimizeProfile(model, features, teacher, profile, batchSize, rounds, callback=None):
    """Greedy integer exponent search; only quantizer metadata is changed."""
    selected = copy.deepcopy(profile)
    score = reconstructionError(model, features, teacher, selected, batchSize)
    history = []
    for passIdx in range(rounds):
        changes = []
        for group in scaleGroups(selected):
            bestShift = 0
            bestScore = score
            for shift in [-1, 1]:
                candidate = copy.deepcopy(selected)
                shiftGroup(candidate, group, shift)
                candidateScore = reconstructionError(model, features, teacher, candidate, batchSize)
                if candidateScore < bestScore - 1e-12:
                    bestScore, bestShift = candidateScore, shift
            if bestShift:
                shiftGroup(selected, group, bestShift)
                changes.append({'nodes': group, 'shift': bestShift,
                                'reconstruction_mse_before': score,
                                'reconstruction_mse_after': bestScore})
                score = bestScore
        record = {'round': passIdx + 1, 'reconstruction_mse': score, 'changes': changes}
        history.append(record)
        if callback is not None:
            callback(selected, record)
        if not changes:
            break
    return selected, history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--initial-profile')
    parser.add_argument('--calibration-samples', type=int, default=2048)
    parser.add_argument('--reconstruction-samples', type=int, default=512)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--teacher-pwl', action='store_true')
    args = parser.parse_args()
    if min(args.calibration_samples, args.reconstruction_samples, args.batch_size,
           args.rounds, args.threads) < 1:
        raise ValueError('Sample, batch, round and thread counts must be positive')
    torch.set_num_threads(args.threads)
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
    if (checkpoint.get('quantization_profile') is not None
            or checkpoint.get('mode') == 'qat'
            or checkpoint.get('training_mode') == 'qat'
            or checkpoint.get('training', {}).get('mode') == 'qat'):
        raise ValueError('PTQ must start from an FP32 checkpoint, not QAT weights')
    if checkpoint.get('epoch') != checkpoint.get('best_epoch'):
        raise ValueError('PTQ requires the validation-selected best checkpoint')
    model = createModel(checkpoint['config'])
    model.load_state_dict(checkpoint['model'])
    model.eval()
    frozen = {name: value.detach().clone() for name, value in model.state_dict().items()}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'selection.json').exists():
        raise ValueError('Use a new output directory for each calibration run')
    xTrain, _ = loadArrays(args.data, 'train')
    xValidation, yValidation = loadArrays(args.data, 'validate')
    datasetHashes = {name: fileSHA256(Path(args.data) / name)
                     for name in ['featuremap_train.npy', 'labels_train.npy',
                                  'featuremap_validate.npy', 'labels_validate.npy']}
    for name, digest in checkpoint.get('dataset_sha256', {}).items():
        if name in datasetHashes and digest != datasetHashes[name]:
            raise ValueError('Calibration/validation data differs from training: ' + name)
    indices = np.random.default_rng(args.seed).permutation(len(xTrain))[:args.reconstruction_samples]
    features = xTrain[indices]
    teacher = predict(model, features, args.batch_size, torch.device('cpu'),
                      lambda network, batch: forwardModel(network, batch, usePWL=args.teacher_pwl))
    provenance = {'method': 'PTQ train-output reconstruction by discrete exponent coordinate search',
                  'parameter_updates': False, 'gradients_used': False, 'test_used': False,
                  'teacher': 'fp32_pwl' if args.teacher_pwl else 'fp32_exact',
                  'source_checkpoint_sha256': fileSHA256(args.checkpoint),
                  'dataset_sha256': datasetHashes,
                  'reconstruction_samples': len(indices), 'reconstruction_seed': args.seed,
                  'reconstruction_index_sha256': hashlib.sha256(indices.astype('<i8').tobytes()).hexdigest(),
                  'arguments': vars(args),
                  'source_sha256': {name: fileSHA256(Path(__file__).parent / name)
                                    for name in ['model.py', 'quantize.py', 'calibrate_ptq.py']}}
    candidates = []
    bestProfile = None
    bestScore = float('inf')

    def assess(profile, name, reconstruction):
        nonlocal bestProfile, bestScore
        profile = copy.deepcopy(profile)
        profile['ptq_calibration'] = provenance
        profile['modelConfig'] = checkpoint['config']
        profile['source_checkpoint_sha256'] = provenance['source_checkpoint_sha256']
        profile['inference_source_sha256'] = {name: provenance['source_sha256'][name]
                                             for name in ['model.py', 'quantize.py']}
        metrics = evaluate(model, xValidation, yValidation, args.batch_size, torch.device('cpu'),
                           lambda network, batch: quantizedForward(network, batch, profile))
        score = metrics['rmse_coordinate_mean_cm']
        saveJSON(output / (name + '.json'), profile)
        candidates.append({'name': name, 'reconstruction_mse': reconstruction,
                           'validation_metrics': metrics})
        if score < bestScore:
            bestScore = score
            bestProfile = profile
            saveJSON(output / 'calibration.json', profile)
        saveJSON(output / 'selection.json', {'provenance': provenance, 'candidates': candidates,
                 'selection': 'minimum full-validation mean coordinatewise RMSE',
                 'best_validation_rmse_cm': bestScore,
                 'selected_candidate': min(candidates, key=lambda entry: entry['validation_metrics']['rmse_coordinate_mean_cm'])['name']})
        print('%s reconstruction MSE %.8g validation RMSE %.6f cm' % (name, reconstruction, score), flush=True)

    profiles = []
    if args.initial_profile:
        supplied = json.loads(Path(args.initial_profile).read_text())
        validateInitialProvenance(supplied, checkpoint['config'],
                                  provenance['source_checkpoint_sha256'], provenance['source_sha256'])
        expected = calibrateModel(model, xTrain, batchSize=args.batch_size,
                                  maxSamples=min(args.calibration_samples, 32), seed=args.seed)
        if set(supplied['nodes']) != set(expected['nodes']):
            raise ValueError('Initial profile observation nodes differ from model')
        for name, entry in supplied['nodes'].items():
            if entry['bits'] != expected['nodes'][name]['bits']:
                raise ValueError('Initial profile bit width differs: ' + name)
            if entry['scale'] != 2.0 ** entry['exponent'] or entry.get('zeroPoint') != 0:
                raise ValueError('Initial profile must use symmetric power-of-two scales')
            if name.endswith('.Abar') and entry['exponent'] != -7:
                raise ValueError('Initial profile must retain Abar scale 2^-7')
            if name.endswith('.currentState'):
                stateName = name[:-len('.currentState')] + '.state'
                if entry['exponent'] != supplied['nodes'][stateName]['exponent'] - 7:
                    raise ValueError('Initial profile has inconsistent INT24/INT17 state scales')
        profiles.append(('supplied', supplied))
    else:
        for percentile in [99.9, 100.0]:
            profile = calibrateModel(model, xTrain, batchSize=args.batch_size,
                                     maxSamples=args.calibration_samples, percentile=percentile, seed=args.seed)
            profiles.append(('percentile_%g' % percentile, profile))
        hybrid = copy.deepcopy(profiles[-1][1])
        for name in ['input', 'patches']:
            hybrid['nodes'][name] = copy.deepcopy(profiles[0][1]['nodes'][name])
        hybrid['percentileOverrides'] = {'input': 99.9, 'patches': 99.9}
        profiles.append(('hybrid', hybrid))
    # exp has a bounded useful PWL domain; calibrate its input independently of
    # rare very negative arguments and the large mass of exactly zero deltas.
    expanded = []
    for name, profile in profiles:
        expanded.append((name, profile))
        candidate = copy.deepcopy(profile)
        for node in candidate['nodes']:
            if node.endswith('.expInput'):
                setExponent(candidate, node, -5)
        expanded.append((name + '_exp_neg5', candidate))
    initial = []
    for name, profile in expanded:
        score = reconstructionError(model, features, teacher, profile, args.batch_size)
        assess(profile, name, score)
        initial.append((score, name, profile))
    _, name, initialProfile = min(initial, key=lambda entry: entry[0])

    def completedRound(profile, record):
        roundName = 'reconstruction_round_%d' % record['round']
        saveJSON(output / (roundName + '_changes.json'), record)
        assess(profile, roundName, record['reconstruction_mse'])

    started = time.perf_counter()
    _, history = optimizeProfile(model, features, teacher, initialProfile, args.batch_size,
                                 args.rounds, completedRound)
    for parameter, value in model.state_dict().items():
        if not torch.equal(value, frozen[parameter]):
            raise RuntimeError('PTQ unexpectedly modified model parameter: ' + parameter)
    saveJSON(output / 'reconstruction_history.json', {'initial_candidate': name, 'rounds': history,
             'seconds': time.perf_counter() - started, 'parameters_unchanged': True})


if __name__ == '__main__':
    main()
