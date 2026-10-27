#!/usr/bin/env python3
"""Freeze, export, and evaluate a validation-selected PTQ checkpoint.

Accept either an FP32 checkpoint paired with --profile, or the self-contained
checkpoint.pt emitted here. No fitting, gradients, or selection occurs here.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch

from model import createModel, forwardModel
from quantize import exportQuantizedModel, quantizedForward, quantizeTensor, validateModelProfile
from train import computeMetrics, fileSHA256, loadArrays, predict, saveJSON


def profileDigest(profile):
    content = json.dumps(profile, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


def integerParameters(model, profile):
    result = {}
    for name, parameter in model.named_parameters():
        value = parameter.detach().cpu()
        if name.endswith('.A_log'):
            name = name[:-len('.A_log')] + '.A'
            value = -torch.exp(value)
        entry = profile['nodes'][name]
        if entry['bits'] != 8:
            raise ValueError('PTQ parameter is not INT8: ' + name)
        result[name] = quantizeTensor(value, entry['exponent']).to(torch.int8)
    return result


def validateFormats(profile):
    for name, entry in profile['nodes'].items():
        exponent = entry['exponent']
        expectedBits = 24 if name.endswith('.currentState') else 17 if name.endswith('.state') else 8
        if (not isinstance(exponent, int) or isinstance(exponent, bool)
                or entry['scale'] != 2.0 ** exponent or entry['zeroPoint'] != 0
                or entry['bits'] != expectedBits):
            raise ValueError('Inconsistent symmetric power-of-two quantization format: ' + name)
        if name.endswith('.Abar') and exponent != -7:
            raise ValueError('Abar scale must be 2^-7')
        if name.endswith('.state'):
            current = name[:-len('.state')] + '.currentState'
            if profile['nodes'][current]['exponent'] != exponent - 7:
                raise ValueError('INT24/INT17 state scales must differ by seven bits')


def loadCheckpoint(checkpointPath, profilePath=None):
    checkpoint = torch.load(checkpointPath, map_location='cpu', weights_only=False)
    if (checkpoint.get('quantization_profile') is not None or checkpoint.get('mode') == 'qat'
            or checkpoint.get('training_mode') == 'qat'
            or checkpoint.get('training', {}).get('mode') == 'qat'):
        raise ValueError('QAT weights are not accepted by the PTQ evaluator')
    if checkpoint['epoch'] != checkpoint['best_epoch']:
        raise ValueError('Expected a validation-selected checkpoint')
    if checkpoint.get('ptq_format_version') == 1:
        if profilePath is not None:
            raise ValueError('A PTQ bundle already contains its frozen profile')
        profile = checkpoint['ptq_profile']
        if profileDigest(profile) != checkpoint['ptq_profile_sha256']:
            raise ValueError('PTQ profile digest mismatch')
    else:
        if profilePath is None:
            raise ValueError('An FP32 checkpoint requires --profile')
        profile = json.loads(Path(profilePath).read_text())
        provenance = profile.get('ptq_calibration', {})
        if provenance.get('source_checkpoint_sha256') != fileSHA256(checkpointPath):
            raise ValueError('Calibration belongs to a different FP32 checkpoint')
    if profile.get('calibrationSplit') != 'train' or not profile.get('usePWL'):
        raise ValueError('Expected train-calibrated PTQ with inference PWL')
    if profile.get('modelConfig') != checkpoint['config']:
        raise ValueError('PTQ profile/model configuration mismatch')
    validateFormats(profile)
    provenance = profile.get('ptq_calibration', {})
    if provenance.get('parameter_updates') is not False or provenance.get('gradients_used') is not False:
        raise ValueError('Missing frozen-weight PTQ provenance')
    for name in ['model.py', 'quantize.py']:
        if provenance.get('source_sha256', {}).get(name) != fileSHA256(Path(__file__).parent / name):
            raise ValueError('PTQ inference source changed: ' + name)
    model = createModel(checkpoint['config'])
    model.load_state_dict(checkpoint['model'])
    model.eval()
    validateModelProfile(model, profile)
    integers = integerParameters(model, profile)
    if checkpoint.get('ptq_format_version') == 1:
        saved = checkpoint['int8_parameters']
        if saved.keys() != integers.keys() or any(
                saved[name].dtype != torch.int8 or not torch.equal(saved[name], value)
                for name, value in integers.items()):
            raise ValueError('PTQ deployment parameters differ from reference quantization')
    return checkpoint, model, profile, integers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--profile')
    parser.add_argument('--output', required=True)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if min(args.batch_size, args.threads) < 1:
        raise ValueError('Batch size and threads must be positive')
    torch.set_num_threads(args.threads)
    checkpoint, model, profile, integers = loadCheckpoint(args.checkpoint, args.profile)
    for name, expected in checkpoint['dataset_sha256'].items():
        if fileSHA256(Path(args.data) / name) != expected:
            raise ValueError('Dataset differs from checkpoint: ' + name)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'metrics.json').exists():
        raise ValueError('Use a new output directory for each evaluation')
    inputHash = fileSHA256(args.checkpoint)
    bundle = {
        'ptq_format_version': 1, 'quantization_method': 'PTQ',
        'mode': 'ptq', 'model': model.state_dict(), 'config': model.config,
        'int8_parameters': integers, 'ptq_profile': profile,
        'ptq_profile_sha256': profileDigest(profile),
        'epoch': checkpoint['epoch'], 'best_epoch': checkpoint['best_epoch'],
        'dataset_sha256': checkpoint['dataset_sha256'],
        'source_checkpoint_sha256': inputHash,
        'training_mode': checkpoint.get('training_mode', checkpoint.get('mode', 'fp32')),
        'ptq_equalization': checkpoint.get('ptq_equalization'),
        'ptq_pwl_calibration': checkpoint.get('ptq_pwl_calibration'),
        'scope': 'FP32 reference weights plus their signed INT8 deployment tensors; frozen PTQ scales; no QAT',
    }
    # Freeze the bundle before opening test arrays.
    bundlePath = output / 'checkpoint.pt'
    torch.save(bundle, bundlePath)
    _, reloaded, reloadedProfile, _ = loadCheckpoint(bundlePath)
    manifest = exportQuantizedModel(model, profile, output / 'export')
    manifest['trainingProvenance'] = {
        'mode': 'ptq', 'checkpointSHA256': fileSHA256(bundlePath),
        'sourceCheckpointSHA256': inputHash, 'quantizationProfileSHA256': profileDigest(profile),
        'quantizedWeightTraining': False,
    }
    manifest['inputPreprocessing'] = {'channelDivisors': model.config.get(
        'input_channel_divisors', [1.0] * model.config['input_channels'])}
    saveJSON(output / 'export' / 'manifest.json', manifest)
    saveJSON(output / 'calibration.json', profile)
    features, labels = loadArrays(args.data, 'test')
    result = {
        'quantization_method': 'PTQ', 'checkpoint_sha256': fileSHA256(bundlePath),
        'source_checkpoint_sha256': inputHash, 'ptq_profile_sha256': profileDigest(profile),
        'selection': 'Frozen full-validation selection; test is used only for final reporting',
        'test_used_for_selection': False, 'test_frames': len(features),
        'parameter_count': sum(value.numel() for value in model.parameters()),
        'dataset_test_sha256': {name: fileSHA256(Path(args.data) / name)
            for name in ['featuremap_test.npy', 'labels_test.npy']},
        'evaluation_source_sha256': {name: fileSHA256(Path(__file__).parent / name)
            for name in ['evaluate_ptq.py', 'train.py', 'model.py', 'quantize.py']},
        'metrics': {}, 'quantization_scope': profile['simulationScope'],
    }
    for mode in ['fp32', 'fp32_pwl', 'int8_ptq']:
        started = time.perf_counter()
        if mode == 'int8_ptq':
            forward = lambda network, batch: quantizedForward(network, batch, profile)
        else:
            forward = lambda network, batch: forwardModel(network, batch, usePWL=(mode == 'fp32_pwl'))
        prediction = predict(model, features, args.batch_size, torch.device('cpu'), forward)
        np.save(output / ('predictions_' + mode + '.npy'), prediction, allow_pickle=False)
        metrics = computeMetrics(prediction, labels.numpy())
        metrics['evaluation_seconds'] = time.perf_counter() - started
        result['metrics'][mode] = metrics
        print('%s RMSE %.8f cm' % (mode, metrics['rmse_coordinate_mean_cm']), flush=True)
    # A fresh bundle load must reproduce the INT8 outputs, not merely its metadata.
    with torch.no_grad():
        reference = quantizedForward(model, features[:64], profile)
        restored = quantizedForward(reloaded, features[:64], reloadedProfile)
        if not torch.equal(reference, restored):
            raise RuntimeError('Saved PTQ bundle changed inference output')
    for entry in manifest['tensors']:
        binary = np.fromfile(output / 'export' / entry['binary'], dtype=np.int8).reshape(entry['shape'])
        if not np.array_equal(binary, integers[entry['parameterName']].numpy()):
            raise RuntimeError('Exported INT8 weights differ from checkpoint')
    result['verification'] = {'bundle_roundtrip_exact': True,
        'integer_exports_match_checkpoint': True, 'new_rtl_validation': False}
    saveJSON(output / 'metrics.json', result)


if __name__ == '__main__':
    main()
