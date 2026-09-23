"""Optional, function-preserving equalization before post-training quantization.

No optimizer, targets, gradients, or quantized training are used. Input channel
divisors are stored in model.config['input_channel_divisors']; forwardModel
applies them before calibration and every inference. They are part of the
model's input contract, not a modification to the saved MARS data.
For example, [1, 1, 1, 1, 4] divides the fifth input channel by four and
multiplies embedding columns 4, 9, 14, and 19 by four for a 2x2 patch.

Positive power-of-two scaling of the two regression-head layers preserves
ReLU: W1' = W1 / d, b1' = b1 / d, W2' = W2 * d. This optional weight-only
cross-layer equalization needs no runtime head preprocessing. Floating-point
rounding can still cause tiny differences when head divisors are nonuniform.
"""

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import torch

try:
    from .model import createModel, forwardModel
except ImportError:
    from model import createModel, forwardModel


def _divisorTensor(divisors, width, reference):
    if divisors is None:
        result = reference.new_ones(width)
    else:
        result = torch.as_tensor(divisors, dtype=reference.dtype,
                                 device=reference.device).detach().clone()
    if result.ndim != 1 or result.numel() != width:
        raise ValueError('One divisor is required for each channel')
    if not torch.isfinite(result).all() or not torch.all(result > 0):
        raise ValueError('Equalization divisors must be finite and positive')
    logarithm = torch.log2(result)
    if not torch.equal(logarithm, logarithm.round()):
        raise ValueError('Equalization divisors must be powers of two')
    return result


def preprocessInput(value, inputDivisors):
    """Scale the last dimension without mutating an input batch or dataset."""
    if isinstance(value, torch.Tensor):
        divisors = torch.as_tensor(inputDivisors, dtype=value.dtype,
                                   device=value.device)
    else:
        value = np.asarray(value)
        divisors = np.asarray(inputDivisors, dtype=value.dtype)
    if value.ndim != 4 or divisors.ndim != 1 or value.shape[-1] != len(divisors):
        raise ValueError('Expected [batch,height,width,channels] and channel divisors')
    return value / divisors


def equalizeModel(model, inputDivisors=None, headDivisors=None):
    """Return (copied model, input divisor tensor, JSON-serializable metadata).

    Call this once on an unequalized FP32 checkpoint. Save the returned model's
    config, state, and metadata together. Pass unmodified raw input batches to
    calibration and inference; forwardModel applies the stored divisors.
    """
    if any(value != 1.0 for value in model.config.get('input_channel_divisors', [])):
        raise ValueError('Input equalization was already applied to this model')
    transformed = copy.deepcopy(model)
    inputScale = _divisorTensor(inputDivisors, model.config['input_channels'],
                                transformed.embedding.weight)
    headScale = _divisorTensor(headDivisors, transformed.headHidden.out_features,
                               transformed.headHidden.weight)
    patchScale = inputScale.repeat(model.config['P'] ** 2)
    if patchScale.numel() != transformed.embedding.in_features:
        raise ValueError('Embedding shape does not match patch/channel layout')
    with torch.no_grad():
        transformed.embedding.weight.mul_(patchScale[None, :])
        transformed.headHidden.weight.div_(headScale[:, None])
        if transformed.headHidden.bias is not None:
            transformed.headHidden.bias.div_(headScale)
        transformed.headOutput.weight.mul_(headScale[None, :])
    transformed.config['input_channel_divisors'] = inputScale.cpu().tolist()
    if any(not torch.isfinite(parameter).all() for parameter in transformed.parameters()):
        raise ValueError('Equalization produced nonfinite model parameters')
    metadata = {
        'schemaVersion': 1,
        'method': 'function_preserving_power_of_two_equalization',
        'inputChannelDivisors': inputScale.cpu().tolist(),
        'headHiddenDivisors': headScale.cpu().tolist(),
        'inputPreprocessing': 'raw_input / inputChannelDivisors before input quantization',
        'embeddingTransform': 'weight columns multiplied by repeated inputChannelDivisors',
        'headTransform': 'headHidden rows and bias divided; headOutput columns multiplied',
        'parametersAlreadyTransformed': True,
        'usesGradients': False,
        'usesTargets': False,
        'trainingMode': 'ptq',
        'weightsChangedOnlyAlgebraically': True,
        'retrainingPerformed': False,
    }
    return transformed, inputScale, metadata


def forwardEqualized(model, value, inputDivisors, observer=None, usePWL=False):
    """Forward an already-transformed model from unmodified raw inputs."""
    configured = model.config.get('input_channel_divisors')
    supplied = torch.as_tensor(inputDivisors).detach().cpu().tolist()
    if configured != supplied:
        raise ValueError('Input divisors differ from the transformed model config')
    return forwardModel(model, value, observer=observer, usePWL=usePWL)


def chooseInputDivisors(trainX, maxSamples=2048, percentile=99.9, seed=0,
                        maximumExponent=4):
    """Fit bounded powers of two using only training-input channel ranges.

    This is an unsupervised heuristic, not a claim that quantized RMSE improves.
    Return its metadata so calibration provenance can accompany a checkpoint.
    """
    if len(trainX) < 1 or maxSamples < 1 or not 0 < percentile <= 100:
        raise ValueError('Invalid training-input equalization configuration')
    if maximumExponent < 0:
        raise ValueError('maximumExponent must be nonnegative')
    indices = np.random.default_rng(seed).permutation(len(trainX))[:maxSamples]
    values = torch.as_tensor(trainX[indices], dtype=torch.float32).detach().cpu()
    if values.ndim != 4 or not torch.isfinite(values).all():
        raise ValueError('Expected finite training [batch,height,width,channels]')
    channelRange = torch.quantile(values.abs().reshape(-1, values.shape[-1]),
                                  percentile / 100.0, dim=0)
    nonzero = channelRange[channelRange > 0]
    reference = nonzero.median() if nonzero.numel() else channelRange.new_tensor(1.0)
    ratios = torch.where(channelRange > 0, channelRange / reference,
                          torch.ones_like(channelRange))
    exponents = torch.log2(ratios).round().clamp(-maximumExponent, maximumExponent)
    divisors = torch.pow(2.0, exponents)
    return divisors, {
        'fitSplit': 'train',
        'fitSamples': len(indices),
        'fitSeed': int(seed),
        'fitIndexSHA256': hashlib.sha256(indices.astype('<i8').tobytes()).hexdigest(),
        'fitPercentile': float(percentile),
        'channelAbsPercentiles': channelRange.tolist(),
        'referenceAbsPercentile': float(reference),
        'maximumExponent': int(maximumExponent),
        'usesTargets': False,
    }


def chooseHeadDivisors(model, maximumExponent=4):
    """Weight-only cross-layer equalization of the positive-homogeneous ReLU.

    d = sqrt(max(abs(W1 row), abs(b1)) / max(abs(W2 column))), rounded to a
    bounded power of two. An all-zero incoming or outgoing channel stays fixed.
    """
    if maximumExponent < 0:
        raise ValueError('maximumExponent must be nonnegative')
    with torch.no_grad():
        incoming = model.headHidden.weight.detach().abs().amax(dim=1)
        if model.headHidden.bias is not None:
            incoming = torch.maximum(incoming, model.headHidden.bias.detach().abs())
        outgoing = model.headOutput.weight.detach().abs().amax(dim=0)
        valid = (incoming > 0) & (outgoing > 0)
        ratio = torch.ones_like(incoming)
        ratio[valid] = incoming[valid] / outgoing[valid]
        if not torch.isfinite(ratio).all():
            raise ValueError('Head equalization requires finite weight ranges')
        exponents = (0.5 * torch.log2(ratio)).round().clamp(-maximumExponent, maximumExponent)
        return torch.pow(2.0, exponents)


def _fileSHA256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepareCheckpoint(checkpointPath, dataDirectory, inputDivisors, equalizeHead=False):
    """Prepare an inference-only FP32 checkpoint for a subsequent PTQ run.

    The checkpoint retains source training history and selection. Optimizer,
    scheduler, and RNG state are removed: those states are not transformed and
    must not be used to resume training from the changed parameterization.
    Train/validation file hashes are checked; only train features are loaded.
    """
    payload = Path(checkpointPath).read_bytes()
    sourceHash = hashlib.sha256(payload).hexdigest()
    source = torch.load(io.BytesIO(payload), map_location='cpu', weights_only=False)
    if source.get('mode') == 'qat' or source.get('quantization_profile') is not None:
        raise ValueError('PTQ equalization requires an FP32 source, not QAT weights')
    if source.get('training', {}).get('mode') == 'qat':
        raise ValueError('PTQ equalization cannot use QAT training provenance')
    if source.get('ptq_equalization') is not None:
        raise ValueError('Checkpoint equalization was already applied')
    if 'epoch' not in source or source['epoch'] != source.get('best_epoch'):
        raise ValueError('Use a validation-selected best checkpoint')
    expectedHashes = source.get('dataset_sha256', {})
    checkedHashes = {}
    dataDirectory = Path(dataDirectory)
    for split in ['train', 'validate']:
        for prefix in ['featuremap_', 'labels_']:
            name = prefix + split + '.npy'
            if name not in expectedHashes:
                raise ValueError('Source checkpoint has no dataset hash: ' + name)
            digest = _fileSHA256(dataDirectory / name)
            if digest != expectedHashes[name]:
                raise ValueError('Data differs from FP32 source training: ' + name)
            checkedHashes[name] = digest

    original = createModel(source['config'])
    original.load_state_dict(source['model'])
    original.eval()
    headDivisors = chooseHeadDivisors(original) if equalizeHead else None
    transformed, _, metadata = equalizeModel(original, inputDivisors, headDivisors)
    trainFeatures = np.load(dataDirectory / 'featuremap_train.npy', mmap_mode='r',
                             allow_pickle=False)
    if len(trainFeatures) < 1:
        raise ValueError('Training features are empty')
    indices = np.random.default_rng(0).permutation(len(trainFeatures))[:128]
    batch = torch.as_tensor(np.asarray(trainFeatures[indices], dtype=np.float32))
    differences = {}
    with torch.no_grad():
        for usePWL in [False, True]:
            reference = forwardModel(original, batch, usePWL=usePWL)
            changed = forwardModel(transformed, batch, usePWL=usePWL)
            mode = 'fp32_pwl' if usePWL else 'fp32'
            differences[mode] = float((reference - changed).abs().max())
            if not torch.equal(reference, changed):
                raise ValueError('Equalization changed training-input predictions: ' + mode)
    metadata.update({
        'sourceCheckpointSHA256': sourceHash,
        'sourceCheckpointEpoch': source['epoch'],
        'sourceMode': source.get('mode', 'fp32'),
        'sourceBestValidationRMSECm': source.get('best_validation_rmse_cm'),
        'sourceDatasetSHA256': checkedHashes,
        'equalizationSourceSHA256': {
            name: _fileSHA256(Path(__file__).parent / name)
            for name in ['model.py', 'ptq_equalize.py']
        },
        'verification': {
            'split': 'train',
            'frames': len(indices),
            'seed': 0,
            'indexSHA256': hashlib.sha256(indices.astype('<i8').tobytes()).hexdigest(),
            'maxAbsoluteOutputDifference': differences,
            'bitIdentical': True,
            'testUsed': False,
        },
        'checkpointUse': 'inference and PTQ calibration only; do not resume training',
    })
    prepared = copy.deepcopy(source)
    removed = []
    for name in list(prepared):
        if name in ['optimizer', 'scheduler'] or name.endswith('_rng'):
            removed.append(name)
            del prepared[name]
    metadata['removedTrainingState'] = removed
    prepared['model'] = transformed.state_dict()
    prepared['config'] = transformed.config
    prepared['mode'] = 'fp32_equalized_for_ptq'
    prepared['inference_only'] = True
    prepared['ptq_equalization'] = metadata
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--output-checkpoint', required=True)
    parser.add_argument('--input-divisors', nargs='+', type=float, required=True)
    parser.add_argument('--equalize-head', action='store_true')
    parser.add_argument('--threads', type=int, default=1)
    args = parser.parse_args()
    if args.threads < 1:
        raise ValueError('threads must be positive')
    if Path(args.checkpoint).resolve() == Path(args.output_checkpoint).resolve():
        raise ValueError('Output must preserve the original FP32 checkpoint')
    torch.set_num_threads(args.threads)
    checkpoint = prepareCheckpoint(args.checkpoint, args.data, args.input_divisors,
                                    args.equalize_head)
    output = Path(args.output_checkpoint)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    torch.save(checkpoint, temporary)
    temporary.replace(output)
    print(json.dumps({
        'checkpoint': str(output),
        'checkpointSHA256': _fileSHA256(output),
        'ptq_equalization': checkpoint['ptq_equalization'],
    }, indent=2))


if __name__ == '__main__':
    main()
