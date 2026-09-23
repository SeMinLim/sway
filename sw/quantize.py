"""Training-only PTQ, integer SSM reference, and portable RTL parameter files.

The scale/state rules follow eMamba section 4.6. The unpublished calibration,
rounding and clipping details are explicit choices of this reimplementation.
The surrounding graph uses quantize/dequantize simulation; this is not an RTL
equivalence or hardware-performance result.
"""

import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import torch

try:
    from .model import forwardModel
except ImportError:
    from model import forwardModel


def signedLimits(bits):
    return -(1 << (bits - 1)), (1 << (bits - 1)) - 1


def chooseExponent(values, bits=8, percentile=99.9):
    values = torch.as_tensor(values).detach().abs().flatten().float().cpu()
    if values.numel() == 0 or not torch.isfinite(values).all():
        raise ValueError("Calibration requires nonempty finite tensors")
    threshold = float(torch.quantile(values, percentile / 100.0))
    if threshold == 0:
        return 0
    _, upper = signedLimits(bits)
    return int(math.ceil(math.log2(threshold / upper)))


def quantizeTensor(value, exponent, bits=8):
    lower, upper = signedLimits(bits)
    return torch.round(value.detach() / (2.0 ** exponent)).clamp(lower, upper).to(torch.int64)


def shiftInteger(value, exponentFrom, exponentTo):
    """Exact power-of-two rescaling, nearest ties-to-even for right shifts."""
    shift = int(exponentFrom) - int(exponentTo)
    if shift >= 0:
        if shift >= 62:
            raise OverflowError("Requested integer left shift exceeds reference width")
        if value.numel() and int(value.abs().max()) > ((1 << 62) - 1) >> shift:
            raise OverflowError("Integer rescaling would overflow INT64")
        return value << shift
    shift = -shift
    if shift >= 62:
        raise OverflowError("Requested integer right shift exceeds reference width")
    absolute = value.abs()
    quotient = absolute >> shift
    remainder = absolute - (quotient << shift)
    half = 1 << (shift - 1)
    increment = (remainder > half) | ((remainder == half) & ((quotient & 1) != 0))
    rounded = quotient + increment.to(torch.int64)
    return torch.where(value < 0, -rounded, rounded)


def integerSSM(xInt, aInt, bInt, cInt, dInt, xExponent, bExponent,
               cExponent, dExponent, stateExponent, outputExponent, trace=False):
    """INT24 current state -> output -> arithmetic >>7 -> signed INT17.

    Inputs are B,L,H, B,L,H,N, B,L,H,N, B,L,N, and H respectively.
    Abar is signed INT8 with scale 2^-7, including saturation of 1 to 127/128.
    Bbar*x is aligned to the current-state scale before addition. The recurrent
    storage shift deliberately truncates, whereas ordinary rescaling rounds.
    """
    if xInt.ndim != 3 or aInt.shape != bInt.shape or aInt.shape[:3] != xInt.shape:
        raise ValueError("SSM shape mismatch")
    batchNum, tokenNum, hiddenDim = xInt.shape
    stateDim = aInt.shape[-1]
    if tuple(cInt.shape) != (batchNum, tokenNum, stateDim) or dInt.shape != (hiddenDim,):
        raise ValueError("SSM C/D shape mismatch")
    state = torch.zeros((batchNum, hiddenDim, stateDim), dtype=torch.int64, device=xInt.device)
    output = torch.empty_like(xInt)
    currentExponent = stateExponent - 7
    currentMin, currentMax = signedLimits(24)
    stateMin, stateMax = signedLimits(17)
    outputMin, outputMax = signedLimits(8)
    statistics = {"currentStateClipped": 0, "outputClipped": 0, "stateElements": 0,
                  "outputElements": 0, "maxAbsCurrentState": 0}
    currentTrace = []
    storedTrace = []
    for tokenIdx in range(tokenNum):
        inputTerm = bInt[:, tokenIdx] * xInt[:, tokenIdx, :, None]
        inputTerm = shiftInteger(inputTerm, bExponent + xExponent, currentExponent)
        current = aInt[:, tokenIdx] * state + inputTerm
        statistics["currentStateClipped"] += int(((current < currentMin) | (current > currentMax)).sum())
        statistics["stateElements"] += current.numel()
        current = current.clamp(currentMin, currentMax)
        statistics["maxAbsCurrentState"] = max(statistics["maxAbsCurrentState"], int(current.abs().max()))

        # Produce this token's output with INT24, before recurrent truncation.
        stateOutput = (current * cInt[:, tokenIdx, None, :]).sum(dim=-1)
        directOutput = xInt[:, tokenIdx] * dInt
        stateOutputExponent = currentExponent + cExponent
        directOutputExponent = xExponent + dExponent
        accumulatorExponent = min(stateOutputExponent, directOutputExponent)
        accumulator = shiftInteger(stateOutput, stateOutputExponent, accumulatorExponent)
        accumulator += shiftInteger(directOutput, directOutputExponent, accumulatorExponent)
        tokenOutput = shiftInteger(accumulator, accumulatorExponent, outputExponent)
        statistics["outputClipped"] += int(((tokenOutput < outputMin) | (tokenOutput > outputMax)).sum())
        statistics["outputElements"] += tokenOutput.numel()
        output[:, tokenIdx] = tokenOutput.clamp(outputMin, outputMax)
        state = (current >> 7).clamp(stateMin, stateMax)
        if trace:
            currentTrace.append(current.clone())
            storedTrace.append(state.clone())
    if trace:
        statistics["currentState"] = torch.stack(currentTrace, dim=1)
        statistics["storedState"] = torch.stack(storedTrace, dim=1)
    return output, statistics


class CalibrationObserver:
    """A callable required by the model's operation-boundary observer API."""

    def __init__(self, maximumValues=65536):
        self.maximumValues = int(maximumValues)
        self.samples = {}
        self.counts = {}
        self.maximum = {}

    def __call__(self, name, value):
        if not torch.isfinite(value).all():
            raise ValueError("Nonfinite calibration tensor: " + name)
        flat = value.detach().abs().flatten().float().cpu()
        self.counts[name] = self.counts.get(name, 0) + flat.numel()
        self.maximum[name] = max(self.maximum.get(name, 0.0), float(flat.max()))
        # Uniform subsampling inside each invocation bounds activation memory.
        stride = max(1, (flat.numel() + 4095) // 4096)
        sampled = flat[::stride]
        if name in self.samples:
            sampled = torch.cat((self.samples[name], sampled))
        if sampled.numel() > self.maximumValues:
            positions = torch.linspace(0, sampled.numel() - 1, self.maximumValues).long()
            sampled = sampled[positions]
        self.samples[name] = sampled
        return value


def calibrateModel(model, trainX, batchSize=256, maxSamples=2048,
                   percentile=99.9, seed=0, usePWL=True):
    """Call only with the training split. No targets or test data are needed."""
    if not 0 < percentile <= 100 or maxSamples < 1 or batchSize < 1:
        raise ValueError("Invalid PTQ calibration configuration")
    model.eval()
    sampleNum = min(int(len(trainX)), int(maxSamples))
    if sampleNum == 0:
        raise ValueError("Cannot calibrate on an empty training split")
    generator = np.random.default_rng(seed)
    indices = generator.permutation(len(trainX))[:sampleNum]
    observer = CalibrationObserver()
    device = next(model.parameters()).device
    with torch.no_grad():
        for startIdx in range(0, sampleNum, batchSize):
            batchIndices = indices[startIdx:startIdx + batchSize]
            batch = torch.as_tensor(trainX[batchIndices], dtype=torch.float32, device=device)
            forwardModel(model, batch, observer=observer, usePWL=usePWL)
    parameterNames = set(dict(model.named_parameters()))
    entries = {}
    for name, values in observer.samples.items():
        bits = 8
        selectedPercentile = 100.0 if name in parameterNames or name.endswith(".A") else percentile
        exponentValues = [observer.maximum[name]] if selectedPercentile == 100.0 else values
        exponent = chooseExponent(exponentValues, bits, selectedPercentile)
        if name.endswith(".Abar"):
            exponent = -7
        if name.endswith(".currentState") or name.endswith(".state"):
            bits = 17
            selectedPercentile = 100.0
            exponent = chooseExponent([observer.maximum[name]], bits, 100.0)
        entries[name] = {"exponent": exponent, "scale": 2.0 ** exponent,
                         "bits": bits, "zeroPoint": 0,
                         "calibrationAbsMax": observer.maximum[name],
                         "observedElements": observer.counts[name],
                         "sampledElements": int(values.numel()),
                         "percentile": selectedPercentile}
    # State storage and the pre-truncation value represent the same real tensor
    # with scales differing by seven binary places.
    for name in list(entries):
        if name.endswith(".currentState"):
            prefix = name[:-len(".currentState")]
            stateExponent = entries[name]["exponent"]
            entries[prefix + ".state"] = dict(entries[name])
            entries[prefix + ".state"]["bits"] = 17
            entries[name]["bits"] = 24
            entries[name]["exponent"] = stateExponent - 7
            entries[name]["scale"] = 2.0 ** (stateExponent - 7)
    return {"schemaVersion": 1,
            "source": "https://arxiv.org/html/2508.10370v1#S4.SS6",
            "calibrationSplit": "train", "calibrationSamples": sampleNum,
            "calibrationSeed": int(seed), "percentile": float(percentile),
            "calibrationIndexSHA256": hashlib.sha256(indices.astype("<i8").tobytes()).hexdigest(),
            "usePWL": bool(usePWL), "nodes": entries,
            "modelConfig": dict(model.config),
            "pwlKnots": {name: model.config[name] for name in ["silu_knots", "exp_knots"]
                         if name in model.config},
            "rounding": "nearest_ties_to_even; recurrent_state_store=arithmetic_right_shift_7",
            "overflow": "saturate signed INT8, current state INT24, retained state INT17",
            "simulationScope": "INT8 affine and convolution operands with INT64 reference MACs; integer SSM recurrence; normalization/nonlinearities/remaining elementwise graph use quantize-dequantize simulation; no RTL validation"}


class QuantizationObserver:

    def __init__(self, profile):
        self.profile = profile
        self.statistics = {}

    def exponent(self, name):
        if name not in self.profile["nodes"]:
            raise KeyError("Tensor has no training calibration: " + name)
        return int(self.profile["nodes"][name]["exponent"])

    def __call__(self, name, value):
        entry = self.profile["nodes"].get(name)
        if entry is None:
            raise KeyError("Tensor has no training calibration: " + name)
        quantized = quantizeTensor(value, entry["exponent"], entry["bits"])
        return quantized.to(value.dtype) * entry["scale"]

    def affineOutput(self, name, accumulator, accumulatorExponent, bias, biasName, dtype):
        if bias is not None:
            biasExponent = self.exponent(biasName)
            biasInt = quantizeTensor(bias, biasExponent)
            commonExponent = min(accumulatorExponent, biasExponent)
            accumulator = shiftInteger(accumulator, accumulatorExponent, commonExponent)
            accumulator = accumulator + shiftInteger(biasInt, biasExponent, commonExponent)
            accumulatorExponent = commonExponent
        outputExponent = self.exponent(name)
        output = shiftInteger(accumulator, accumulatorExponent, outputExponent).clamp(-128, 127)
        return output.to(dtype) * (2.0 ** outputExponent)

    def linear(self, name, x, weight, bias):
        if name == "embedding":
            inputName, weightName, biasName = "patches", "embedding.weight", "embedding.bias"
        elif name == "headHidden":
            inputName, weightName, biasName = "headInput", "headHidden.weight", "headHidden.bias"
        elif name == "output":
            inputName, weightName, biasName = "headActivation", "headOutput.weight", "headOutput.bias"
        else:
            prefix, operation = name.rsplit(".", 1)
            names = {"in": ("norm", "inWeight", "inBias"),
                     "xProjection": ("x", "xWeight", None),
                     "deltaProjection": ("deltaInput", "dtWeight", "dtBias"),
                     "out": ("gated", "outWeight", "outBias")}
            if operation not in names:
                raise KeyError("Unrecognized linear operation: " + name)
            inputKey, weightKey, biasKey = names[operation]
            inputName = prefix + "." + inputKey
            weightName = prefix + "." + weightKey
            biasName = prefix + "." + biasKey if biasKey is not None else None
        inputExponent = self.exponent(inputName)
        weightExponent = self.exponent(weightName)
        xInt = quantizeTensor(x, inputExponent)
        weightInt = quantizeTensor(weight, weightExponent)
        accumulator = torch.matmul(xInt, weightInt.transpose(-2, -1))
        return self.affineOutput(name, accumulator, inputExponent + weightExponent,
                                 bias, biasName, x.dtype)

    def conv1d(self, name, x, weight, bias):
        prefix = name.rsplit(".", 1)[0]
        inputExponent = self.exponent(prefix + ".convInput")
        weightExponent = self.exponent(prefix + ".convWeight")
        xInt = quantizeTensor(x, inputExponent)
        weightInt = quantizeTensor(weight, weightExponent)
        kernelSize = weightInt.shape[-1]
        zeroPadding = torch.zeros((xInt.shape[0], kernelSize - 1, xInt.shape[2]),
                                  device=xInt.device, dtype=torch.int64)
        padded = torch.cat((zeroPadding, xInt), dim=1)
        accumulator = torch.zeros_like(xInt)
        for kernelIdx in range(kernelSize):
            accumulator += padded[:, kernelIdx:kernelIdx + xInt.shape[1]] * weightInt[:, 0, kernelIdx]
        return self.affineOutput(name, accumulator, inputExponent + weightExponent,
                                 bias, prefix + ".convBias", x.dtype)

    def ssm(self, prefix, x, delta, A, B, C, D):
        # Inputs have already passed through their INT8 observation boundaries.
        # Abar/Bbar preprocessing remains floating-point PTQ simulation.
        try:
            from .model import piecewise, EXP_KNOTS
        except ImportError:
            from model import piecewise, EXP_KNOTS
        exponentInput = delta[:, :, :, None] * A[None, None, :, :]
        exponentInput = self(prefix + ".expInput", exponentInput)
        if self.profile["usePWL"]:
            aBar = piecewise(exponentInput, self.profile.get("pwlKnots", {}).get("exp_knots", EXP_KNOTS), "exp")
        else:
            aBar = torch.exp(exponentInput)
        bBar = delta[:, :, :, None] * B[:, :, None, :]
        xExponent = self.exponent(prefix + ".x")
        bExponent = self.exponent(prefix + ".Bbar")
        cExponent = self.exponent(prefix + ".C")
        dExponent = self.exponent(prefix + ".D")
        stateExponent = self.exponent(prefix + ".state")
        outputExponent = self.exponent(prefix + ".ssmY")
        output, statistics = integerSSM(
            quantizeTensor(x, xExponent), quantizeTensor(aBar, -7),
            quantizeTensor(bBar, bExponent), quantizeTensor(C, cExponent),
            quantizeTensor(D, dExponent), xExponent, bExponent, cExponent,
            dExponent, stateExponent, outputExponent)
        self.statistics[prefix] = statistics
        return output.to(x.dtype) * (2.0 ** outputExponent)


def validateModelProfile(model, profile):
    if profile.get("modelConfig", model.config) != model.config:
        raise ValueError("PTQ profile and model configuration differ")
    try:
        from .model import SILU_KNOTS, EXP_KNOTS
    except ImportError:
        from model import SILU_KNOTS, EXP_KNOTS
    for name, default in [("silu_knots", SILU_KNOTS), ("exp_knots", EXP_KNOTS)]:
        if profile.get("pwlKnots", {}).get(name, default) != model.config.get(name, default):
            raise ValueError("PTQ profile and model PWL knots differ: " + name)


def quantizedForward(model, x, profile, returnStatistics=False):
    validateModelProfile(model, profile)
    observer = QuantizationObserver(profile)
    with torch.no_grad():
        output = forwardModel(model, x, observer=observer, usePWL=profile["usePWL"])
    if returnStatistics:
        return output, observer.statistics
    return output


def _qatCorrection(exact, surrogate, exponent, bits=8):
    """Use exact integer forward values and clipped straight-through gradients.

    Subtracting identical surrogate tensors before adding the exact result
    avoids cancellation that could otherwise change a forward value by an ULP.
    The backward pass is an estimator, not the derivative of integer rounding.
    """
    lower, upper = signedLimits(bits)
    scale = 2.0 ** exponent
    surrogate = surrogate.clamp(lower * scale, upper * scale)
    return exact.detach() + (surrogate - surrogate.detach())


class QATObserver(QuantizationObserver):
    """Frozen-scale QAT with the same forward arithmetic as the PTQ reference.

    Integer affine operations and recurrence remain authoritative in the
    forward pass. Their float surrogates supply gradients through rounding;
    saturation clips those gradients. No scales or bit widths are learned.
    """

    def __call__(self, name, value):
        exact = super().__call__(name, value)
        entry = self.profile["nodes"][name]
        return _qatCorrection(exact, value, entry["exponent"], entry["bits"])

    def linear(self, name, x, weight, bias):
        exact = super().linear(name, x, weight, bias)
        surrogate = torch.nn.functional.linear(x, weight, bias)
        return _qatCorrection(exact, surrogate, self.exponent(name))

    def conv1d(self, name, x, weight, bias):
        exact = super().conv1d(name, x, weight, bias)
        padded = torch.nn.functional.pad(x.transpose(1, 2), (weight.shape[-1] - 1, 0))
        surrogate = torch.nn.functional.conv1d(
            padded, weight, bias, groups=x.shape[-1]).transpose(1, 2)
        return _qatCorrection(exact, surrogate, self.exponent(name))

    def ssm(self, prefix, x, delta, A, B, C, D):
        try:
            from .model import piecewise, EXP_KNOTS
        except ImportError:
            from model import piecewise, EXP_KNOTS
        exponentInput = self(prefix + ".expInput", delta[:, :, :, None] * A)
        if self.profile["usePWL"]:
            aBar = piecewise(exponentInput, self.profile.get("pwlKnots", {}).get("exp_knots", EXP_KNOTS), "exp")
        else:
            aBar = torch.exp(exponentInput)
        aBar = self(prefix + ".Abar", aBar)
        bBar = self(prefix + ".Bbar", delta[:, :, :, None] * B[:, :, None, :])
        xExponent = self.exponent(prefix + ".x")
        bExponent = self.exponent(prefix + ".Bbar")
        cExponent = self.exponent(prefix + ".C")
        dExponent = self.exponent(prefix + ".D")
        stateExponent = self.exponent(prefix + ".state")
        outputExponent = self.exponent(prefix + ".ssmY")
        exact, statistics = integerSSM(
            quantizeTensor(x, xExponent), quantizeTensor(aBar, -7),
            quantizeTensor(bBar, bExponent), quantizeTensor(C, cExponent),
            quantizeTensor(D, dExponent), xExponent, bExponent, cExponent,
            dExponent, stateExponent, outputExponent, trace=True)
        currentTrace = statistics.pop("currentState")
        storedTrace = statistics.pop("storedState")
        self.statistics[prefix] = statistics
        currentExponent = stateExponent - 7
        currentScale = 2.0 ** currentExponent
        stateScale = 2.0 ** stateExponent
        state = x.new_zeros(x.shape[0], x.shape[2], A.shape[-1])
        outputs = []
        for tokenIdx in range(x.shape[1]):
            inputTerm = bBar[:, tokenIdx] * x[:, tokenIdx, :, None]
            # Input alignment rounds before addition, with no independent
            # saturation. Only the resulting current state is INT24-clipped.
            aligned = torch.round(inputTerm.detach() / currentScale) * currentScale
            inputTerm = aligned + (inputTerm - inputTerm.detach())
            current = aBar[:, tokenIdx] * state + inputTerm
            exactCurrent = currentTrace[:, tokenIdx].to(x.dtype) * currentScale
            current = _qatCorrection(exactCurrent, current, currentExponent, 24)
            outputs.append((current * C[:, tokenIdx, None, :]).sum(dim=-1) + D * x[:, tokenIdx])
            # The exact trace applies arithmetic >>7, including negative floor
            # truncation. Its STE retains the current-state derivative.
            exactStored = storedTrace[:, tokenIdx].to(x.dtype) * stateScale
            state = _qatCorrection(exactStored, current, stateExponent, 17)
        surrogate = torch.stack(outputs, dim=1)
        exact = exact.to(x.dtype) * (2.0 ** outputExponent)
        return _qatCorrection(exact, surrogate, outputExponent)


def qatForward(model, x, profile, returnStatistics=False):
    """Train with frozen INT8/state formats and exact reference forward values."""
    observer = QATObserver(profile)
    output = forwardModel(model, x, observer=observer, usePWL=profile["usePWL"])
    if returnStatistics:
        return output, observer.statistics
    return output


def tensorName(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def exportIntegerTensor(outputDir, name, integerArray, bits=8):
    """Little-endian byte words, line-per-element hex, and row-major C array."""
    outputDir = Path(outputDir)
    outputDir.mkdir(parents=True, exist_ok=True)
    name = tensorName(name)
    array = np.asarray(integerArray, dtype=np.int64)
    minimum, maximum = signedLimits(bits)
    if array.size == 0 or int(array.min()) < minimum or int(array.max()) > maximum:
        raise ValueError("Exported integer values do not fit their declared width")
    storageBits = 8 if bits <= 8 else 16 if bits <= 16 else 32 if bits <= 32 else 64
    dtype = np.dtype("<i" + str(storageBits // 8))
    payload = array.astype(dtype).tobytes(order="C")
    binaryPath = outputDir / (name + ".bin")
    hexadecimalPath = outputDir / (name + ".hex")
    binaryPath.write_bytes(payload)
    hexDigits = (bits + 3) // 4
    bitMask = (1 << bits) - 1
    hexadecimalPath.write_text("".join(format(int(value) & bitMask, "0" + str(hexDigits) + "x") + "\n"
                                       for value in array.reshape(-1)), encoding="utf-8")
    return {"name": name, "shape": list(array.shape), "elements": int(array.size),
            "logicalBits": bits, "storageBits": storageBits, "dtype": dtype.str,
            "layout": "C row-major, last axis contiguous", "endianness": "little",
            "binary": binaryPath.name, "hex": hexadecimalPath.name,
            "hexEncoding": "one logical-width two's-complement element per line, no byte swapping",
            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def exportQuantizedModel(model, profile, outputDir):
    validateModelProfile(model, profile)
    try:
        from .model import SILU_KNOTS, EXP_KNOTS
    except ImportError:
        from model import SILU_KNOTS, EXP_KNOTS
    outputDir = Path(outputDir)
    outputDir.mkdir(parents=True, exist_ok=True)
    entries = []
    header = ["/* eMamba-MARS reimplementation PTQ parameters. */", "#ifndef SWAY_PARAMETERS_H",
              "#define SWAY_PARAMETERS_H", "", "#include <stdint.h>", ""]
    fp32Parameters = {}
    for name, parameter in model.named_parameters():
        value = parameter.detach().cpu()
        fp32Parameters[name] = value.numpy()
        exportName = name
        if name.endswith(".A_log"):
            exportName = name[:-len(".A_log")] + ".A"
            value = -torch.exp(value)
        if exportName not in profile["nodes"]:
            raise KeyError("Missing parameter quantization scale: " + exportName)
        exponent = int(profile["nodes"][exportName]["exponent"])
        array = quantizeTensor(value, exponent).numpy().astype(np.int8)
        metadata = exportIntegerTensor(outputDir, exportName, array)
        metadata.update({"parameterName": exportName, "trainingParameterName": name,
                         "scaleExponent": exponent, "scale": 2.0 ** exponent, "zeroPoint": 0})
        entries.append(metadata)
        cName = "sway_" + tensorName(exportName)
        header.append("static const int32_t " + cName + "_scaleExponent = " + str(exponent) + ";")
        header.append("static const uint32_t " + cName + "_shape[] = {" + ", ".join(str(size) for size in array.shape) + "};")
        header.append("static const int8_t " + cName + "[" + str(array.size) + "] = {")
        flat = array.reshape(-1)
        for startIdx in range(0, len(flat), 16):
            header.append("\t" + ", ".join(str(int(value)) for value in flat[startIdx:startIdx + 16]) + ",")
        header.extend(["};", ""])
    header.extend(["#endif", ""])
    (outputDir / "parameters.h").write_text("\n".join(header), encoding="utf-8")
    np.savez(outputDir / "parameters_fp32.npz", **fp32Parameters)
    (outputDir / "quantization.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    manifest = {"schemaVersion": 1, "format": "sway-emamba-ptq-v1",
                "modelConfig": model.config,
                "provenance": "Independently trained eMamba-MARS reimplementation, not author checkpoint",
                "parameterCount": sum(entry["elements"] for entry in entries),
                "int8ParameterBytes": sum(entry["bytes"] for entry in entries),
                "tensors": entries, "quantization": "quantization.json", "cArrays": "parameters.h",
                "fp32Parameters": "parameters_fp32.npz",
                "piecewiseApproximation": {"siluKnots": model.config.get("silu_knots", SILU_KNOTS),
                                            "expKnots": model.config.get("exp_knots", EXP_KNOTS),
                                            "ordinates": "exact function evaluated at each knot",
                                            "scope": "Reimplementation choices, original coefficients unpublished"},
                "stateFormat": {"currentBits": 24, "retainedBits": 17, "AbarScaleExponent": -7,
                                "order": "current_state -> output -> arithmetic_right_shift_7 -> retained_state"},
                "verificationScope": profile["simulationScope"]}
    (outputDir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verifyExport(outputDir)
    return manifest


def verifyExport(outputDir):
    outputDir = Path(outputDir)
    manifest = json.loads((outputDir / "manifest.json").read_text())
    for entry in manifest["tensors"]:
        payload = (outputDir / entry["binary"]).read_bytes()
        if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError("Binary export hash/size mismatch: " + entry["name"])
        array = np.frombuffer(payload, dtype=np.dtype(entry["dtype"]))
        if array.size != entry["elements"] or int(np.prod(entry["shape"])) != array.size:
            raise ValueError("Binary export shape mismatch: " + entry["name"])
        lines = (outputDir / entry["hex"]).read_text().splitlines()
        bits = entry["logicalBits"]
        decoded = []
        for line in lines:
            value = int(line, 16)
            if value >= 1 << (bits - 1):
                value -= 1 << bits
            decoded.append(value)
        if not np.array_equal(array, decoded):
            raise ValueError("Hex/binary disagreement: " + entry["name"])
    return True
