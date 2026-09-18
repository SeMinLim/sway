"""Trainable reconstruction of the published eMamba MARS configuration.

The paper does not specify the regression head, convolution kernel, delta rank,
bias flags, initialization, or PWL coefficients. config/model.json records the
choices made here. This is not an author-checkpoint or bit-exact reproduction.
"""

import math

import torch
from torch import nn
from torch.nn import functional as F


SILU_KNOTS = [-7.0, -5.0, -4.0, -3.0, -2.0, -1.5, -1.0, -0.5,
	0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
EXP_KNOTS = [-4.0, -3.5, -3.0, -2.5, -2.0, -1.5, -1.0, -0.5,
	0.0, 0.25, 0.5, 1.0]


def observe(observer, name, value):
	if observer is None:
		return value
	return observer(name, value)


def piecewise(value, knots, functionName):
	boundaries = value.new_tensor(knots)
	if functionName == "silu":
		ordinates = F.silu(boundaries)
	else:
		ordinates = torch.exp(boundaries)
	indices = torch.bucketize(value.detach().contiguous(), boundaries) - 1
	indices = indices.clamp(0, len(knots) - 2)
	x0 = boundaries[indices]
	x1 = boundaries[indices + 1]
	y0 = ordinates[indices]
	y1 = ordinates[indices + 1]
	result = y0 + (value - x0) * (y1 - y0) / (x1 - x0)
	if functionName == "silu":
		result = torch.where(value < -7.0, torch.zeros_like(value), result)
		result = torch.where(value > 7.0, value, result)
	else:
		result = torch.where(value < -4.0, torch.zeros_like(value), result)
		result = torch.where(value > 1.0, torch.full_like(value, math.e), result)
	return result


def linear(observer, name, value, weight, bias):
	if observer is not None and hasattr(observer, "linear"):
		return observer.linear(name, value, weight, bias)
	return observe(observer, name, F.linear(value, weight, bias))


def rangeNorm(observer, name, value, weight, bias, epsilon):
	if observer is not None and hasattr(observer, "rangeNorm"):
		return observer.rangeNorm(name, value, weight, bias, epsilon)
	meanValue = value.mean(dim=-1, keepdim=True)
	rangeValue = value.amax(dim=-1, keepdim=True) - value.amin(dim=-1, keepdim=True)
	rangeValue = rangeValue.clamp_min(epsilon)
	result = (value - meanValue) / rangeValue
	return observe(observer, name, result * weight + bias)


class MambaBlock(nn.Module):
	"""Parameter container; computation is explicit in forwardBlock."""

	def __init__(self, config):
		super().__init__()
		modelDim = config["D"]
		innerDim = modelDim * config["E"]
		stateDim = config["N"]
		rank = config["dt_rank"]
		kernel = config["conv_kernel"]
		self.normWeight = nn.Parameter(torch.ones(modelDim))
		self.normBias = nn.Parameter(torch.zeros(modelDim))
		self.inWeight = nn.Parameter(torch.empty(2 * innerDim, modelDim))
		self.inBias = None
		self.convWeight = nn.Parameter(torch.empty(innerDim, 1, kernel))
		self.convBias = nn.Parameter(torch.zeros(innerDim))
		self.xWeight = nn.Parameter(torch.empty(rank + 2 * stateDim, innerDim))
		self.dtWeight = nn.Parameter(torch.empty(innerDim, rank))
		self.dtBias = nn.Parameter(torch.empty(innerDim))
		initialA = torch.arange(1, stateDim + 1, dtype=torch.float32)
		self.A_log = nn.Parameter(initialA.log().repeat(innerDim, 1))
		self.D = nn.Parameter(torch.ones(innerDim))
		self.outWeight = nn.Parameter(torch.empty(modelDim, innerDim))
		self.outBias = None
		for weight in [self.inWeight, self.convWeight, self.xWeight, self.outWeight]:
			nn.init.kaiming_uniform_(weight, a=math.sqrt(5))
		nn.init.uniform_(self.dtWeight, -rank ** -0.5, rank ** -0.5)
		with torch.no_grad():
			self.dtBias.uniform_(math.log(0.01), math.log(0.1)).exp_()


class MARSModel(nn.Module):
	"""The nn.Module wrapper supplies parameter registration to PyTorch."""

	def __init__(self, config):
		super().__init__()
		self.config = dict(config)
		patchDim = config["P"] * config["P"] * config["input_channels"]
		self.embedding = nn.Linear(patchDim, config["D"])
		self.blocks = nn.ModuleList()
		for blockIdx in range(config["M"]):
			self.blocks.append(MambaBlock(config))
		self.headHidden = nn.Linear(config["L"] * config["D"], config["head_hidden"])
		self.headOutput = nn.Linear(config["head_hidden"], config["outputs"])

	def forward(self, value):
		return forwardModel(self, value)


def createModel(config):
	if config["D"] <= 0 or config["E"] <= 0 or config["N"] <= 0:
		raise ValueError("Model dimensions must be positive")
	if config["input_height"] % config["P"] or config["input_width"] % config["P"]:
		raise ValueError("Patch size must divide both input dimensions")
	sequenceLength = (config["input_height"] // config["P"]) * (config["input_width"] // config["P"])
	if sequenceLength != config["L"]:
		raise ValueError("L differs from the number of non-overlapping patches")
	return MARSModel(config)


def forwardBlock(block, value, config, prefix, observer=None, usePWL=False):
	# Phase 1: normalize and form the two expanded branches.
	innerDim = config["D"] * config["E"]
	stateDim = config["N"]
	weight = observe(observer, prefix + ".normWeight", block.normWeight)
	bias = observe(observer, prefix + ".normBias", block.normBias)
	normalized = rangeNorm(observer, prefix + ".norm", value, weight, bias, config["norm_epsilon"])
	weight = observe(observer, prefix + ".inWeight", block.inWeight)
	expanded = linear(observer, prefix + ".in", normalized, weight, None)
	convInput, gateInput = expanded.split(innerDim, dim=-1)
	convInput = observe(observer, prefix + ".convInput", convInput)
	gateInput = observe(observer, prefix + ".gateInput", gateInput)
	weight = observe(observer, prefix + ".convWeight", block.convWeight)
	bias = observe(observer, prefix + ".convBias", block.convBias)
	if observer is not None and hasattr(observer, "conv1d"):
		convolved = observer.conv1d(prefix + ".conv", convInput, weight, bias)
	else:
		padded = F.pad(convInput.transpose(1, 2), (config["conv_kernel"] - 1, 0))
		convolved = F.conv1d(padded, weight, bias, groups=innerDim).transpose(1, 2)
		convolved = observe(observer, prefix + ".conv", convolved)
	if usePWL:
		x = piecewise(convolved, SILU_KNOTS, "silu")
		gate = piecewise(gateInput, SILU_KNOTS, "silu")
	else:
		x = F.silu(convolved)
		gate = F.silu(gateInput)
	x = observe(observer, prefix + ".x", x)
	gate = observe(observer, prefix + ".gate", gate)

	# Phase 2: form input-dependent state parameters.
	weight = observe(observer, prefix + ".xWeight", block.xWeight)
	projected = linear(observer, prefix + ".xProjection", x, weight, None)
	deltaInput, B, C = projected.split([config["dt_rank"], stateDim, stateDim], dim=-1)
	deltaInput = observe(observer, prefix + ".deltaInput", deltaInput)
	B = observe(observer, prefix + ".B", B)
	C = observe(observer, prefix + ".C", C)
	weight = observe(observer, prefix + ".dtWeight", block.dtWeight)
	bias = observe(observer, prefix + ".dtBias", block.dtBias)
	delta = linear(observer, prefix + ".deltaProjection", deltaInput, weight, bias)
	delta = observe(observer, prefix + ".delta", F.relu(delta))
	A = observe(observer, prefix + ".A", -torch.exp(block.A_log))
	D = observe(observer, prefix + ".D", block.D)

	# Phase 3: recurrence, reset independently for each radar frame.
	y = None
	if observer is not None and hasattr(observer, "ssm"):
		y = observer.ssm(prefix, x, delta, A, B, C, D)
	if y is None:
		argument = observe(observer, prefix + ".expInput", delta.unsqueeze(-1) * A)
		if usePWL:
			Abar = piecewise(argument, EXP_KNOTS, "exp")
		else:
			Abar = torch.exp(argument)
		Abar = observe(observer, prefix + ".Abar", Abar)
		Bbar = observe(observer, prefix + ".Bbar", delta.unsqueeze(-1) * B.unsqueeze(2))
		state = x.new_zeros(x.shape[0], innerDim, stateDim)
		outputs = []
		for tokenIdx in range(x.shape[1]):
			current = Abar[:, tokenIdx] * state + Bbar[:, tokenIdx] * x[:, tokenIdx].unsqueeze(-1)
			current = observe(observer, prefix + ".currentState", current)
			output = (current * C[:, tokenIdx].unsqueeze(1)).sum(dim=-1)
			output = output + D * x[:, tokenIdx]
			outputs.append(output)
			state = observe(observer, prefix + ".state", current)
		y = observe(observer, prefix + ".ssmY", torch.stack(outputs, dim=1))

	# Phase 4: gate, project, and add the residual.
	gated = observe(observer, prefix + ".gated", y * gate)
	weight = observe(observer, prefix + ".outWeight", block.outWeight)
	projected = linear(observer, prefix + ".out", gated, weight, None)
	return observe(observer, prefix + ".residual", value + projected)


def forwardModel(model, value, observer=None, usePWL=False):
	config = model.config
	expected = (config["input_height"], config["input_width"], config["input_channels"])
	if tuple(value.shape[1:]) != expected:
		raise ValueError("Expected input [batch, %d, %d, %d]" % expected)
	value = observe(observer, "input", value)
	batchSize = value.shape[0]
	patchSize = config["P"]
	rows = config["input_height"] // patchSize
	columns = config["input_width"] // patchSize
	# Row-major patches; elements within a patch remain H,W,C ordered.
	patches = value.reshape(batchSize, rows, patchSize, columns, patchSize, config["input_channels"])
	patches = patches.permute(0, 1, 3, 2, 4, 5).reshape(batchSize, config["L"], -1)
	patches = observe(observer, "patches", patches)
	weight = observe(observer, "embedding.weight", model.embedding.weight)
	bias = observe(observer, "embedding.bias", model.embedding.bias)
	value = linear(observer, "embedding", patches, weight, bias)
	for blockIdx in range(config["M"]):
		value = forwardBlock(model.blocks[blockIdx], value, config,
			"blocks.%d" % blockIdx, observer, usePWL)
	value = observe(observer, "headInput", value.reshape(batchSize, -1))
	weight = observe(observer, "headHidden.weight", model.headHidden.weight)
	bias = observe(observer, "headHidden.bias", model.headHidden.bias)
	value = linear(observer, "headHidden", value, weight, bias)
	value = observe(observer, "headActivation", F.relu(value))
	weight = observe(observer, "headOutput.weight", model.headOutput.weight)
	bias = observe(observer, "headOutput.bias", model.headOutput.bias)
	return linear(observer, "output", value, weight, bias)
