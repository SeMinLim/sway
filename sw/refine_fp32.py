#!/usr/bin/env python3
"""Refine FP32 with minibatch mean coordinatewise RMSE on training data.

The minibatch objective is an estimator of the full-split metric. Selection
always uses full validation mean coordinatewise RMSE; test arrays are not read.
The model architecture and exact nonlinearities are unchanged.
"""
import argparse
from pathlib import Path
import platform
import random
import sys
import time

import numpy as np
import torch

from finetune import checkSource, saveCheckpoint, saveHistory
from model import createModel, forwardModel
from train import evaluate, fileSHA256, loadArrays, saveJSON, seedEverything


def loadResume(args, output, config, datasetHashes, sourceHash, sourceHashes, device):
    if Path(args.resume).resolve() != (output / 'last.pt').resolve():
        raise ValueError('--resume must name last.pt in the same output directory')
    if not (output / 'best.pt').is_file():
        raise ValueError('Resume requires the original best.pt beside last.pt')
    checkpoint = torch.load(args.resume, map_location=device, weights_only=False)
    if checkpoint.get('refine_fp32_format_version') != 1:
        raise ValueError('Unsupported FP32 refinement checkpoint')
    if checkpoint['config'] != config or checkpoint['dataset_sha256'] != datasetHashes:
        raise ValueError('Resume configuration or train/validation hashes differ')
    if checkpoint['source_checkpoint_sha256'] != sourceHash:
        raise ValueError('Resume source checkpoint bytes differ')
    if checkpoint['source_sha256'] != sourceHashes:
        raise ValueError('Resume source code differs')
    for key in ['batch_size', 'learning_rate', 'weight_decay', 'patience',
                'lr_patience', 'min_delta', 'clip_grad', 'seed', 'device', 'threads']:
        if vars(args)[key] != checkpoint['training'][key]:
            raise ValueError('Resume training setting differs: ' + key)
    bestHash = fileSHA256(output / 'best.pt')
    if bestHash != checkpoint['best_checkpoint_sha256']:
        raise ValueError('best.pt differs from the selected checkpoint recorded in last.pt')
    best = torch.load(output / 'best.pt', map_location='cpu', weights_only=False)
    if (best['epoch'] != checkpoint['best_epoch'] or best['epoch'] != best['best_epoch']
            or best['best_validation_rmse_cm'] != checkpoint['best_validation_rmse_cm']
            or best['source_checkpoint_sha256'] != sourceHash
            or best['source_sha256'] != sourceHashes
            or best['dataset_sha256'] != datasetHashes or best['config'] != config):
        raise ValueError('Selected best checkpoint metadata differs from last.pt')
    if args.epochs <= checkpoint['epoch']:
        raise ValueError('--epochs must exceed the completed epoch when resuming')
    return checkpoint, bestHash


def refine(args):
    if args.epochs < 1 or args.batch_size < 1 or args.threads < 1:
        raise ValueError('epochs, batch-size and threads must be positive')
    if args.learning_rate <= 0 or args.clip_grad <= 0 or args.patience < 1 or args.lr_patience < 0:
        raise ValueError('Invalid learning rate, clipping or patience')
    if args.weight_decay < 0 or args.min_delta < 0:
        raise ValueError('Weight decay and min-delta must be nonnegative')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if not args.resume and any((output / name).exists() for name in
            ['best.pt', 'last.pt', 'history.csv', 'run.json']):
        raise ValueError('Output contains run artifacts; use a new directory or --resume')
    sourcePath = Path(args.checkpoint).resolve()
    if sourcePath.parent == output.resolve():
        raise ValueError('Refinement output must differ from the source checkpoint directory')
    torch.set_num_threads(args.threads)
    seedEverything(args.seed)
    device = torch.device(args.device)
    print('[STEP 1] Verifying source and train/validation arrays', flush=True)
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
        for name in ['refine_fp32.py', 'finetune.py', 'train.py', 'model.py']}
    xTrain, yTrain = loadArrays(args.data, 'train')
    xValidation, yValidation = loadArrays(args.data, 'validate')
    model = createModel(config).to(device)
    model.load_state_dict(source['model'])
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min',
        factor=0.5, patience=args.lr_patience, min_lr=1e-6)
    exactForward = lambda network, features: forwardModel(network, features, usePWL=False)
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
        initialMetrics = resumed['initial_validation']
        torch.set_rng_state(resumed['torch_rng'].cpu())
        random.setstate(resumed['python_rng'])
        np.random.set_state(resumed['numpy_rng'])
        if device.type == 'cuda' and resumed['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all([state.cpu() for state in resumed['cuda_rng']])
    else:
        initialMetrics = evaluate(model, xValidation, yValidation, args.batch_size, device, exactForward)
        bestScore = initialMetrics['rmse_coordinate_mean_cm']
        history.append({'epoch': 0, 'train_minibatch_rmse_m': None,
            'validation_mae_cm': initialMetrics['mae_cm'], 'validation_rmse_cm': bestScore,
            'learning_rate': optimizer.param_groups[0]['lr'],
            'elapsed_seconds': time.perf_counter() - started})
    run = {'status': 'running', 'mode': 'fp32_coordinate_rmse',
        'model_identity': 'our_reimplementation_not_author_checkpoint',
        'config': config, 'training': vars(args), 'dataset_sha256': datasetHashes,
        'source_checkpoint': str(sourcePath), 'source_checkpoint_sha256': sourceHash,
        'source_checkpoint_epoch': source['epoch'], 'source_sha256': sourceHashes,
        'train_frames': len(xTrain), 'validation_frames': len(xValidation),
        'parameters': sum(parameter.numel() for parameter in model.parameters()),
        'environment': {'python': sys.version, 'torch': str(torch.__version__), 'numpy': np.__version__,
            'platform': platform.platform(), 'device': str(device), 'threads': torch.get_num_threads()},
        'training_objective': 'mean(sqrt(mean((prediction-label)^2, batch)+1e-8), coordinates)',
        'training_objective_scope': 'minibatch estimate, not full-split coordinate RMSE',
        'selection_metric': 'full validation mean of 57 coordinatewise RMSE values in cm',
        'validation_forward': 'forwardModel(usePWL=False)', 'initial_validation': initialMetrics,
        'test_used_for_selection': False}
    saveJSON(output / 'run.json', run)

    def checkpointAt(epoch, metrics):
        return {'format_version': 2, 'refine_fp32_format_version': 1,
            'mode': 'fp32_coordinate_rmse', 'model': model.state_dict(), 'config': config,
            'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
            'epoch': epoch, 'best_epoch': bestEpoch, 'best_validation_rmse_cm': bestScore,
            'validation': metrics, 'initial_validation': initialMetrics,
            'history': history, 'training': vars(args), 'dataset_sha256': datasetHashes,
            'source_checkpoint_sha256': sourceHash, 'source_checkpoint_epoch': source['epoch'],
            'source_sha256': sourceHashes, 'torch_rng': torch.get_rng_state(),
            'python_rng': random.getstate(), 'numpy_rng': np.random.get_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if device.type == 'cuda' else None}

    if not args.resume:
        initial = checkpointAt(0, initialMetrics)
        saveCheckpoint(output / 'best.pt', initial)
        bestCheckpointHash = fileSHA256(output / 'best.pt')
        initial['best_checkpoint_sha256'] = bestCheckpointHash
        saveCheckpoint(output / 'last.pt', initial)
        saveHistory(output, history)
    print('[STEP 2] FP32 RMSE refinement | source epoch %d | epoch-0 validation RMSE %.4f cm' %
        (source['epoch'], initialMetrics['rmse_coordinate_mean_cm']), flush=True)
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
            prediction = exactForward(model, features)
            loss = torch.sqrt(torch.mean((prediction - labels) ** 2, dim=0) + 1e-8).mean()
            if not torch.isfinite(loss):
                raise RuntimeError('Nonfinite training loss at epoch %d' % epoch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip_grad, error_if_nonfinite=True)
            optimizer.step()
            lossSum += float(loss.detach()) * len(indices)
        metrics = evaluate(model, xValidation, yValidation, args.batch_size, device, exactForward)
        score = metrics['rmse_coordinate_mean_cm']
        scheduler.step(score)
        improved = score < bestScore - args.min_delta
        if improved:
            bestScore = score
            bestEpoch = epoch
        row = {'epoch': epoch, 'train_minibatch_rmse_m': lossSum / len(xTrain),
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
        print('Epoch %3d | train minibatch RMSE %.6f m | val MAE %.4f cm | val RMSE %.4f cm | %.2f s%s' %
            (epoch, row['train_minibatch_rmse_m'], metrics['mae_cm'], score, row['elapsed_seconds'],
             ' *' if improved else ''), flush=True)
    run.update({'status': 'completed', 'epochs_completed': history[-1]['epoch'],
        'best_epoch': bestEpoch, 'best_validation_rmse_cm': bestScore,
        'elapsed_seconds_this_invocation': time.perf_counter() - started,
        'stop_reason': 'configured_epoch_limit' if history[-1]['epoch'] == args.epochs else 'validation_patience',
        'convergence_claimed': False, 'best_checkpoint_sha256': fileSHA256(output / 'best.pt')})
    saveJSON(output / 'run.json', run)
    print('[STEP 3] Selected %s, epoch %d, validation RMSE %.4f cm' %
        (output / 'best.pt', bestEpoch, bestScore), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--epochs', type=int, default=60)
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--seed', type=int, default=20260922)
    parser.add_argument('--learning-rate', type=float, default=0.0001)
    parser.add_argument('--weight-decay', type=float, default=0.0001)
    parser.add_argument('--clip-grad', type=float, default=1.0)
    parser.add_argument('--patience', type=int, default=15)
    parser.add_argument('--lr-patience', type=int, default=5)
    parser.add_argument('--min-delta', type=float, default=0.0001)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--resume', help='Resume last.pt in the same output directory')
    refine(parser.parse_args())


if __name__ == '__main__':
    main()
