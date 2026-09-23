#!/usr/bin/env python3
"""Fine-tune a validation-selected MARS checkpoint with PWL or fixed-scale QAT.

Only train and validation arrays are opened. QAT checkpoint selection uses the
integer-reference forward pass, with scales frozen before the first update.
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import platform
import random
import sys
import time

import numpy as np
import torch

from model import createModel, forwardModel
from train import evaluate, fileSHA256, loadArrays, saveJSON, seedEverything


def profileSHA256(profile):
    if profile is None:
        return None
    payload = json.dumps(profile, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def saveCheckpoint(path, checkpoint):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.part')
    torch.save(checkpoint, temporary)
    temporary.replace(path)


def saveHistory(output, history):
    path = output / 'history.csv'
    temporary = path.with_suffix('.csv.part')
    with temporary.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(history[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(history)
    temporary.replace(path)


def checkSource(checkpoint, datasetHashes):
    if checkpoint['epoch'] != checkpoint['best_epoch']:
        raise ValueError('--checkpoint must be the validation-selected best checkpoint')
    if checkpoint['dataset_sha256'] != datasetHashes:
        raise ValueError('Source checkpoint train/validation hashes differ from the supplied arrays')
    if not np.isfinite(checkpoint['best_validation_rmse_cm']):
        raise ValueError('Source checkpoint has no finite validation score')
    if not checkpoint.get('config') or not checkpoint.get('model'):
        raise ValueError('Source checkpoint has no model configuration or weights')


def chooseProfile(args, model, xTrain, xValidation, yValidation, device):
    from quantize import calibrateModel, quantizedForward
    candidates = []
    bestScore = float('inf')
    bestProfile = None
    bestMetrics = None
    if args.calibration:
        supplied = json.loads(Path(args.calibration).read_text())
        profiles = [supplied]
    else:
        profiles = []
        for percentile in args.calibration_percentiles:
            profile = calibrateModel(model, xTrain, batchSize=args.batch_size,
                maxSamples=args.calibration_samples, percentile=percentile,
                seed=args.seed, usePWL=True)
            profile['calibration_policy'] = 'uniform_percentile_%g' % percentile
            profiles.append(profile)
        byPercentile = {profile['percentile']: profile for profile in profiles}
        if 99.9 in byPercentile and 100.0 in byPercentile:
            # Isolate input precision from clipping in later model stages.
            hybrid = copy.deepcopy(byPercentile[100.0])
            for name in ['input', 'patches']:
                hybrid['nodes'][name] = copy.deepcopy(byPercentile[99.9]['nodes'][name])
            hybrid['calibration_policy'] = 'input_99.9_other_nodes_100'
            hybrid['percentileOverrides'] = {'input': 99.9, 'patches': 99.9}
            profiles.append(hybrid)
    for profile in profiles:
        if profile.get('calibrationSplit') != 'train' or profile.get('usePWL') is not True:
            raise ValueError('QAT requires a training-calibrated PWL quantization profile')
        profileSHA256(profile)
        metrics = evaluate(model, xValidation, yValidation, args.batch_size, device,
            lambda network, features: quantizedForward(network, features, profile))
        score = metrics['rmse_coordinate_mean_cm']
        policy = profile.get('calibration_policy', 'supplied_profile')
        candidates.append({'percentile': profile.get('percentile'), 'calibration_policy': policy,
            'profile_sha256': profileSHA256(profile), 'validation_metrics': metrics})
        if score < bestScore:
            bestScore = score
            bestProfile = profile
            bestMetrics = metrics
        print('[STEP 2] QAT initial profile %s | validation RMSE %.4f cm' %
            (policy, score), flush=True)
    selection = {'source': 'supplied_profile' if args.calibration else 'training_calibration',
        'calibration_file_sha256': fileSHA256(args.calibration) if args.calibration else None,
        'candidates': candidates, 'selected_percentile': bestProfile.get('percentile'),
        'selected_policy': bestProfile.get('calibration_policy', 'supplied_profile'),
        'criterion': 'validation mean coordinatewise RMSE from quantizedForward',
        'frozen_during_training': True, 'test_used': False}
    return bestProfile, selection, bestMetrics


def loadResume(args, output, config, datasetHashes, sourceHash, sourceHashes, device):
    if Path(args.resume).resolve() != (output / 'last.pt').resolve():
        raise ValueError('--resume must name last.pt in the same --output directory')
    if not (output / 'best.pt').is_file():
        raise ValueError('Resume requires the original best.pt beside last.pt')
    checkpoint = torch.load(args.resume, map_location=device, weights_only=False)
    if checkpoint.get('finetune_format_version') != 1:
        raise ValueError('Resume checkpoint is not a supported fine-tuning run')
    if checkpoint['config'] != config or checkpoint['dataset_sha256'] != datasetHashes:
        raise ValueError('Resume configuration or train/validation hashes differ')
    if checkpoint['source_checkpoint_sha256'] != sourceHash:
        raise ValueError('Resume source checkpoint bytes differ')
    if checkpoint['source_sha256'] != sourceHashes:
        raise ValueError('Resume model/training/quantization code differs')
    for key in ['mode', 'batch_size', 'learning_rate', 'weight_decay', 'patience',
                'lr_patience', 'min_delta', 'clip_grad', 'seed', 'device', 'threads',
                'calibration_samples', 'calibration_percentiles']:
        if vars(args)[key] != checkpoint['training'][key]:
            raise ValueError('Resume training setting differs: ' + key)
    calibrationHash = fileSHA256(args.calibration) if args.calibration else None
    if calibrationHash != checkpoint['calibration_file_sha256']:
        raise ValueError('Resume calibration file differs')
    profile = checkpoint['quantization_profile']
    if profileSHA256(profile) != checkpoint['quantization_profile_sha256']:
        raise ValueError('Resume frozen calibration profile does not match its digest')
    bestHash = fileSHA256(output / 'best.pt')
    if bestHash != checkpoint['best_checkpoint_sha256']:
        raise ValueError('best.pt differs from the selected checkpoint recorded in last.pt')
    best = torch.load(output / 'best.pt', map_location='cpu', weights_only=False)
    if (best['epoch'] != checkpoint['best_epoch'] or best['epoch'] != best['best_epoch']
            or best['best_validation_rmse_cm'] != checkpoint['best_validation_rmse_cm']
            or best['source_checkpoint_sha256'] != sourceHash
            or best['dataset_sha256'] != datasetHashes or best['config'] != config
            or best['quantization_profile_sha256'] != checkpoint['quantization_profile_sha256']):
        raise ValueError('Selected best checkpoint metadata differs from last.pt')
    if args.epochs <= checkpoint['epoch']:
        raise ValueError('--epochs must exceed the completed epoch when resuming')
    return checkpoint, bestHash


def finetune(args):
    if args.epochs < 1 or args.batch_size < 1 or args.threads < 1:
        raise ValueError('epochs, batch-size and threads must be positive')
    if args.learning_rate <= 0 or args.clip_grad <= 0 or args.patience < 1 or args.lr_patience < 0:
        raise ValueError('Invalid learning rate, clipping or patience')
    if args.weight_decay < 0 or args.min_delta < 0 or args.calibration_samples < 1:
        raise ValueError('Invalid weight decay, min-delta or calibration sample count')
    if not args.calibration_percentiles or any(not 0 < p <= 100 for p in args.calibration_percentiles):
        raise ValueError('Calibration percentiles must be in (0, 100]')
    if args.mode == 'pwl' and args.calibration:
        raise ValueError('--calibration applies only to QAT')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if not args.resume and any((output / name).exists() for name in
            ['best.pt', 'last.pt', 'history.csv', 'run.json', 'calibration.json']):
        raise ValueError('Output contains run artifacts; use a new directory or --resume')
    sourcePath = Path(args.checkpoint).resolve()
    if sourcePath.parent == output.resolve():
        raise ValueError('Fine-tuning output must differ from the source checkpoint directory')
    torch.set_num_threads(args.threads)
    seedEverything(args.seed)
    device = torch.device(args.device)
    print('[STEP 1] Verifying source checkpoint and loading train/validation arrays', flush=True)
    datasetHashes = {}
    for split in ['train', 'validate']:
        for prefix in ['featuremap_', 'labels_']:
            path = Path(args.data) / (prefix + split + '.npy')
            datasetHashes[path.name] = fileSHA256(path)
    sourceHash = fileSHA256(sourcePath)
    source = torch.load(sourcePath, map_location='cpu', weights_only=False)
    checkSource(source, datasetHashes)
    config = source['config']
    sourceHashes = {name: fileSHA256(Path(__file__).parent / name)
        for name in ['finetune.py', 'train.py', 'model.py', 'quantize.py']}
    xTrain, yTrain = loadArrays(args.data, 'train')
    xValidation, yValidation = loadArrays(args.data, 'validate')
    model = createModel(config).to(device)
    model.load_state_dict(source['model'])
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min',
        factor=0.5, patience=args.lr_patience, min_lr=1e-6)
    profile = None
    selection = None
    initialMetrics = None
    calibrationHash = fileSHA256(args.calibration) if args.calibration else None
    startEpoch = 0
    bestEpoch = 0
    bestCheckpointHash = None
    history = []
    started = time.perf_counter()
    if args.resume:
        resumed, bestCheckpointHash = loadResume(args, output, config, datasetHashes,
            sourceHash, sourceHashes, device)
        model.load_state_dict(resumed['model'])
        optimizer.load_state_dict(resumed['optimizer'])
        scheduler.load_state_dict(resumed['scheduler'])
        startEpoch = resumed['epoch']
        bestEpoch = resumed['best_epoch']
        bestScore = resumed['best_validation_rmse_cm']
        history = resumed['history']
        profile = resumed['quantization_profile']
        selection = resumed['quantization_selection']
        initialMetrics = resumed['initial_validation']
        torch.set_rng_state(resumed['torch_rng'].cpu())
        random.setstate(resumed['python_rng'])
        np.random.set_state(resumed['numpy_rng'])
        if device.type == 'cuda' and resumed['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all([state.cpu() for state in resumed['cuda_rng']])
    elif args.mode == 'qat':
        profile, selection, initialMetrics = chooseProfile(args, model,
            xTrain, xValidation, yValidation, device)
    if args.mode == 'qat':
        from quantize import qatForward, quantizedForward
        trainForward = lambda network, features: qatForward(network, features, profile)
        validationForward = lambda network, features: quantizedForward(network, features, profile)
        saveJSON(output / 'calibration.json', profile)
    else:
        trainForward = lambda network, features: forwardModel(network, features, usePWL=True)
        validationForward = trainForward
    profileHash = profileSHA256(profile)
    if initialMetrics is None:
        initialMetrics = evaluate(model, xValidation, yValidation, args.batch_size, device, validationForward)
    if not args.resume:
        bestScore = initialMetrics['rmse_coordinate_mean_cm']
        history.append({'epoch': 0, 'train_mse_m2': None,
            'validation_mae_cm': initialMetrics['mae_cm'], 'validation_rmse_cm': bestScore,
            'learning_rate': optimizer.param_groups[0]['lr'],
            'elapsed_seconds': time.perf_counter() - started})
    run = {'status': 'running', 'mode': args.mode,
        'model_identity': 'our_reimplementation_not_author_checkpoint',
        'config': config, 'training': vars(args), 'dataset_sha256': datasetHashes,
        'source_checkpoint': str(sourcePath), 'source_checkpoint_sha256': sourceHash,
        'source_checkpoint_epoch': source['epoch'], 'source_sha256': sourceHashes,
        'train_frames': len(xTrain), 'validation_frames': len(xValidation),
        'parameters': sum(parameter.numel() for parameter in model.parameters()),
        'environment': {'python': sys.version, 'torch': str(torch.__version__), 'numpy': np.__version__,
            'platform': platform.platform(), 'device': str(device), 'threads': torch.get_num_threads(),
            'interop_threads': torch.get_num_interop_threads(), 'cuda_version': torch.version.cuda,
            'device_name': torch.cuda.get_device_name(device) if device.type == 'cuda' else platform.processor(),
            'deterministic_algorithms': torch.are_deterministic_algorithms_enabled()},
        'selection_metric': 'validation mean of 57 coordinatewise RMSE values in cm',
        'validation_forward': 'quantizedForward' if args.mode == 'qat' else 'forwardModel(usePWL=True)',
        'initial_validation': initialMetrics, 'quantization_selection': selection,
        'quantization_profile_sha256': profileHash, 'calibration_file_sha256': calibrationHash,
        'test_used_for_selection': False}
    saveJSON(output / 'run.json', run)

    def checkpointAt(epoch, metrics):
        if profileSHA256(profile) != profileHash:
            raise RuntimeError('Frozen quantization profile changed during training')
        return {'format_version': 2, 'finetune_format_version': 1,
            'mode': args.mode, 'model': model.state_dict(), 'config': config,
            'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
            'epoch': epoch, 'best_epoch': bestEpoch, 'best_validation_rmse_cm': bestScore,
            'validation': metrics, 'initial_validation': initialMetrics,
            'history': history, 'training': vars(args), 'dataset_sha256': datasetHashes,
            'source_checkpoint_sha256': sourceHash, 'source_checkpoint_epoch': source['epoch'],
            'source_sha256': sourceHashes, 'quantization_profile': profile,
            'quantization_profile_sha256': profileHash, 'quantization_selection': selection,
            'calibration_file_sha256': calibrationHash,
            'torch_rng': torch.get_rng_state(), 'python_rng': random.getstate(),
            'numpy_rng': np.random.get_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if device.type == 'cuda' else None}

    if not args.resume:
        initial = checkpointAt(0, initialMetrics)
        saveCheckpoint(output / 'best.pt', initial)
        bestCheckpointHash = fileSHA256(output / 'best.pt')
        initial['best_checkpoint_sha256'] = bestCheckpointHash
        saveCheckpoint(output / 'last.pt', initial)
        saveHistory(output, history)
    print('[STEP 3] %s fine-tuning | epoch-0 validation RMSE %.4f cm' %
        (args.mode.upper(), initialMetrics['rmse_coordinate_mean_cm']), flush=True)
    for epoch in range(startEpoch + 1, args.epochs + 1):
        if epoch - 1 - bestEpoch >= args.patience:
            break
        epochStarted = time.perf_counter()
        model.train()
        order = torch.randperm(len(xTrain))
        lossSum = 0.0
        for start in range(0, len(order), args.batch_size):
            indices = order[start:start + args.batch_size]
            features = xTrain[indices].to(device)
            labels = yTrain[indices].to(device)
            optimizer.zero_grad(set_to_none=True)
            prediction = trainForward(model, features)
            loss = torch.mean((prediction - labels) ** 2)
            if not torch.isfinite(loss):
                raise RuntimeError('Nonfinite training loss at epoch %d' % epoch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip_grad, error_if_nonfinite=True)
            optimizer.step()
            lossSum += float(loss.detach()) * len(indices)
        metrics = evaluate(model, xValidation, yValidation, args.batch_size, device, validationForward)
        score = metrics['rmse_coordinate_mean_cm']
        scheduler.step(score)
        improved = score < bestScore - args.min_delta
        if improved:
            bestScore = score
            bestEpoch = epoch
        row = {'epoch': epoch, 'train_mse_m2': lossSum / len(xTrain),
            'validation_mae_cm': metrics['mae_cm'], 'validation_rmse_cm': score,
            'learning_rate': optimizer.param_groups[0]['lr'],
            'elapsed_seconds': time.perf_counter() - epochStarted}
        history.append(row)
        checkpoint = checkpointAt(epoch, metrics)
        if improved:
            saveCheckpoint(output / 'best.pt', checkpoint)
            bestCheckpointHash = fileSHA256(output / 'best.pt')
        checkpoint['best_checkpoint_sha256'] = bestCheckpointHash
        saveCheckpoint(output / 'last.pt', checkpoint)
        saveHistory(output, history)
        print('Epoch %3d | train MSE %.6f m2 | val MAE %.4f cm | val RMSE %.4f cm | %.2f s%s' %
            (epoch, row['train_mse_m2'], metrics['mae_cm'], score, row['elapsed_seconds'],
             ' *' if improved else ''), flush=True)
    run.update({'status': 'completed', 'epochs_completed': history[-1]['epoch'],
        'best_epoch': bestEpoch, 'best_validation_rmse_cm': bestScore,
        'elapsed_seconds_this_invocation': time.perf_counter() - started,
        'stop_reason': 'configured_epoch_limit' if history[-1]['epoch'] == args.epochs else 'validation_patience',
        'convergence_claimed': False, 'best_checkpoint_sha256': fileSHA256(output / 'best.pt')})
    saveJSON(output / 'run.json', run)
    print('[STEP 4] Selected %s, epoch %d, validation RMSE %.4f cm' %
        (output / 'best.pt', bestEpoch, bestScore), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=['pwl', 'qat'])
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--epochs', type=int, default=60)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--seed', type=int, default=20260922)
    parser.add_argument('--learning-rate', type=float, default=0.0001)
    parser.add_argument('--weight-decay', type=float, default=0.0001)
    parser.add_argument('--clip-grad', type=float, default=1.0)
    parser.add_argument('--patience', type=int, default=15)
    parser.add_argument('--lr-patience', type=int, default=5)
    parser.add_argument('--min-delta', type=float, default=0.0001)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--calibration', help='Optional training-calibrated JSON profile for QAT')
    parser.add_argument('--calibration-samples', type=int, default=2048)
    parser.add_argument('--calibration-percentiles', type=float, nargs='+', default=[99.9, 100.0])
    parser.add_argument('--resume', help='Resume last.pt in the same output directory')
    finetune(parser.parse_args())


if __name__ == '__main__':
    main()
