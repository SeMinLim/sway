#!/usr/bin/env python3
"""Train the documented eMamba-MARS reimplementation on official MARS arrays."""
import argparse
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

from model import createModel


def seedEverything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def fileSHA256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def loadArrays(directory, split):
    # Official MARS files retain the published train/validate/test membership.
    directory = Path(directory)
    features = np.load(directory / ('featuremap_' + split + '.npy'), allow_pickle=False)
    labels = np.load(directory / ('labels_' + split + '.npy'), allow_pickle=False)
    features = np.asarray(features, dtype=np.float32)
    labels = np.asarray(labels, dtype=np.float32)
    if features.ndim != 4 or features.shape[1:] != (8, 8, 5):
        raise ValueError('Expected features [frames,8,8,5], got ' + str(features.shape))
    if labels.shape != (len(features), 57):
        raise ValueError('Expected axis-major labels [frames,57], got ' + str(labels.shape))
    if not np.isfinite(features).all() or not np.isfinite(labels).all():
        raise ValueError('Nonfinite dataset value')
    return torch.from_numpy(features), torch.from_numpy(labels)


def computeMetrics(prediction, labels):
    # Coordinates are metres; repository label order is x19, y19, z19.
    prediction = np.asarray(prediction, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.float64)
    if prediction.ndim != 2 or prediction.shape[1] != 57 or prediction.shape != labels.shape or len(labels) == 0:
        raise ValueError('Metrics require matching nonempty [frames,57] arrays')
    if not np.isfinite(prediction).all() or not np.isfinite(labels).all():
        raise ValueError('Nonfinite metric input')
    error = (prediction - labels) * 100.0
    error = error.reshape(-1, 3, 19)
    mae = np.mean(np.abs(error), axis=(0, 2))
    rmse = np.mean(np.sqrt(np.mean(error * error, axis=0)), axis=1)
    # Official MARS computes RMSE per joint/axis over frames, then averages.
    return {
        'frames': len(error),
        'mae_cm': float(np.mean(mae)),
        'rmse_coordinate_mean_cm': float(np.mean(rmse)),
        'rmse_global_cm': float(np.sqrt(np.mean(error * error))),
        'axis_mae_cm': dict(zip(['x', 'y', 'z'], mae.tolist())),
        'axis_rmse_cm': dict(zip(['x', 'y', 'z'], rmse.tolist()))
    }


def predict(model, features, batchSize, device, forward=None):
    model.eval()
    predictions = []
    with torch.no_grad():
        for start in range(0, len(features), batchSize):
            batch = features[start:start + batchSize].to(device)
            result = model(batch) if forward is None else forward(model, batch)
            if not torch.isfinite(result).all():
                raise RuntimeError('Nonfinite inference output')
            predictions.append(result.detach().cpu().numpy())
    return np.concatenate(predictions, axis=0)


def evaluate(model, features, labels, batchSize, device, forward=None):
    prediction = predict(model, features, batchSize, device, forward)
    return computeMetrics(prediction, labels.numpy())


def saveJSON(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def loadResume(args, output, config, datasetHashes, device):
    # Resume only the latest state of this run, preserving its selected best model.
    if Path(args.resume).resolve() != (output / 'last.pt').resolve():
        raise ValueError('--resume must name last.pt in the same --output directory')
    if not (output / 'best.pt').is_file():
        raise ValueError('Resume requires the original best.pt beside last.pt')
    checkpoint = torch.load(args.resume, map_location=device, weights_only=False)
    if checkpoint['config'] != config:
        raise ValueError('Resume configuration differs')
    if checkpoint['dataset_sha256'] != datasetHashes:
        raise ValueError('Resume dataset hashes differ from the original run')
    bestHash = fileSHA256(output / 'best.pt')
    if checkpoint.get('format_version', 1) == 1:
        # Version 1 predates the stored digest. Match its best-model metadata;
        # the next saved checkpoint also binds the exact file bytes.
        best = torch.load(output / 'best.pt', map_location='cpu', weights_only=False)
        if (best['config'] != config or best['dataset_sha256'] != datasetHashes
                or best['epoch'] != checkpoint['best_epoch']
                or best['best_validation_rmse_cm'] != checkpoint['best_validation_rmse_cm']
                or best['history'] != checkpoint['history'][:best['epoch']]
                or best['training'] != checkpoint['training']):
            raise ValueError('Version 1 best.pt provenance does not match last.pt')
    elif checkpoint.get('best_checkpoint_sha256') != bestHash:
        raise ValueError('best.pt does not match the selected checkpoint recorded in last.pt')
    if args.epochs <= checkpoint['epoch']:
        raise ValueError('--epochs must exceed the completed epoch when resuming')
    for key in ['batch_size', 'learning_rate', 'weight_decay', 'patience',
                'lr_patience', 'min_delta', 'clip_grad', 'seed', 'device']:
        if vars(args)[key] != checkpoint['training'][key]:
            raise ValueError('Resume training setting differs: ' + key)
    return checkpoint, bestHash


def train(args):
    if args.epochs < 1 or args.batch_size < 1 or args.threads < 1:
        raise ValueError('epochs, batch-size and threads must be positive')
    if args.patience < 1 or args.lr_patience < 0 or args.clip_grad <= 0 or args.learning_rate <= 0:
        raise ValueError('Invalid patience, gradient clipping or learning rate')
    if args.min_delta < 0 or args.weight_decay < 0:
        raise ValueError('min-delta and weight-decay must be nonnegative')
    torch.set_num_threads(args.threads)
    seedEverything(args.seed)
    device = torch.device(args.device)
    config = json.loads(Path(args.config).read_text())
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ['best.pt', 'last.pt']) and not args.resume:
        raise ValueError('Output contains a checkpoint; use a new directory or --resume')
    print('---------------------------------------------------------------------', flush=True)
    print('[STEP 1] Loading official MARS train and validation arrays', flush=True)
    xTrain, yTrain = loadArrays(args.data, 'train')
    xValidation, yValidation = loadArrays(args.data, 'validate')
    datasetHashes = {}
    for split in ['train', 'validate']:
        for prefix in ['featuremap_', 'labels_']:
            path = Path(args.data) / (prefix + split + '.npy')
            datasetHashes[path.name] = fileSHA256(path)
    # Test arrays are never opened during training or checkpoint selection.
    model = createModel(config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=args.lr_patience, min_lr=1e-6)
    startEpoch = 0
    bestScore = float('inf')
    bestEpoch = 0
    bestCheckpointHash = None
    history = []
    if args.resume:
        checkpoint, bestCheckpointHash = loadResume(args, output, config, datasetHashes, device)
        model.load_state_dict(checkpoint['model'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        scheduler.load_state_dict(checkpoint['scheduler'])
        startEpoch = checkpoint['epoch']
        bestScore = checkpoint['best_validation_rmse_cm']
        bestEpoch = checkpoint['best_epoch']
        history = checkpoint['history']
        torch.set_rng_state(checkpoint['torch_rng'].cpu())
        if 'python_rng' in checkpoint:
            random.setstate(checkpoint['python_rng'])
        if 'numpy_rng' in checkpoint:
            np.random.set_state(checkpoint['numpy_rng'])
        if device.type == 'cuda' and checkpoint.get('cuda_rng') is not None:
            torch.cuda.set_rng_state_all([state.cpu() for state in checkpoint['cuda_rng']])
    run = {
        'status': 'running', 'model_identity': 'our_reimplementation_not_author_checkpoint',
        'config': config, 'training': vars(args), 'dataset_sha256': datasetHashes,
        'train_frames': len(xTrain), 'validation_frames': len(xValidation),
        'parameters': sum(p.numel() for p in model.parameters()),
        'environment': {'python': sys.version, 'torch': torch.__version__, 'numpy': np.__version__,
                        'platform': platform.platform(), 'device': str(device), 'threads': args.threads},
        'selection_metric': 'validation mean of 57 coordinatewise RMSE values in cm',
        'test_used_for_selection': False
    }
    saveJSON(output / 'run.json', run)
    print('[STEP 2] Training %d parameters on %d frames' % (run['parameters'], len(xTrain)), flush=True)
    started = time.perf_counter()
    csvPath = output / 'history.csv'
    for epoch in range(startEpoch + 1, args.epochs + 1):
        epochStarted = time.perf_counter()
        model.train()
        order = torch.randperm(len(xTrain))
        lossSum = 0.0
        for start in range(0, len(order), args.batch_size):
            indices = order[start:start + args.batch_size]
            features = xTrain[indices].to(device)
            labels = yTrain[indices].to(device)
            optimizer.zero_grad(set_to_none=True)
            prediction = model(features)
            loss = torch.mean((prediction - labels) ** 2)
            if not torch.isfinite(loss):
                raise RuntimeError('Nonfinite training loss at epoch %d' % epoch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip_grad, error_if_nonfinite=True)
            optimizer.step()
            lossSum += float(loss.detach()) * len(indices)
        metrics = evaluate(model, xValidation, yValidation, args.batch_size, device)
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
        with open(csvPath, 'w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row), lineterminator='\n')
            writer.writeheader()
            writer.writerows(history)
        checkpoint = {
            'format_version': 2, 'model': model.state_dict(), 'config': config,
            'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
            'epoch': epoch, 'best_epoch': bestEpoch, 'best_validation_rmse_cm': bestScore,
            'validation': metrics, 'history': history, 'training': vars(args),
            'dataset_sha256': datasetHashes, 'torch_rng': torch.get_rng_state(),
            'python_rng': random.getstate(), 'numpy_rng': np.random.get_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if device.type == 'cuda' else None
        }
        if improved:
            torch.save(checkpoint, output / 'best.pt')
            bestCheckpointHash = fileSHA256(output / 'best.pt')
        checkpoint['best_checkpoint_sha256'] = bestCheckpointHash
        torch.save(checkpoint, output / 'last.pt')
        print('Epoch %3d | train MSE %.6f m2 | val MAE %.4f cm | val RMSE %.4f cm | %.2f s%s' %
              (epoch, row['train_mse_m2'], metrics['mae_cm'], score, row['elapsed_seconds'], ' *' if improved else ''), flush=True)
        if epoch - bestEpoch >= args.patience:
            break
    run.update({'status': 'completed', 'epochs_completed': history[-1]['epoch'],
                'best_epoch': bestEpoch, 'best_validation_rmse_cm': bestScore,
                'elapsed_seconds_this_invocation': time.perf_counter() - started,
                'stop_reason': 'configured_epoch_limit' if history[-1]['epoch'] == args.epochs else 'validation_patience',
                'convergence_claimed': False,
                'best_checkpoint_sha256': fileSHA256(output / 'best.pt')})
    saveJSON(output / 'run.json', run)
    print('[STEP 3] Best checkpoint saved: %s, validation RMSE %.4f cm' % (output / 'best.pt', bestScore), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--config', default=str(Path(__file__).parent / 'config' / 'model.json'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--epochs', type=int, default=150)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--learning-rate', type=float, default=0.001)
    parser.add_argument('--weight-decay', type=float, default=0.0001)
    parser.add_argument('--patience', type=int, default=25)
    parser.add_argument('--lr-patience', type=int, default=8)
    parser.add_argument('--min-delta', type=float, default=0.0001)
    parser.add_argument('--clip-grad', type=float, default=1.0)
    parser.add_argument('--seed', type=int, default=20260917)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--resume')
    train(parser.parse_args())


if __name__ == '__main__':
    main()
