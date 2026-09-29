package SwayBlock;

import Assert::*;
import Vector::*;
import FIFO::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayLinear::*;
import SwayNorm::*;
import SwayScan::*;
import SwayMultiply::*;

typedef 18 ConvAccumulatorWidth;
typedef 10 ResidualAccumulatorWidth;
typedef TDiv#(InnerDim, ConvLanes) ConvGroups;
typedef TLog#(TAdd#(ConvGroups, 1)) ConvCountWidth;
typedef TDiv#(InnerDim, GateLanes) GateGroups;
typedef TLog#(TAdd#(GateGroups, 1)) GateCountWidth;

typedef struct {
	Bit#(ConvCountWidth) group;
	Vector#(ConvLanes, Vector#(ConvTaps, Int#(8))) samples;
	Vector#(ConvLanes, Vector#(ConvTaps, Int#(8))) weights;
	Vector#(ConvLanes, Int#(8)) bias;
} ConvOperands deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(ConvCountWidth) group;
	Vector#(ConvLanes, Int#(8)) bias;
} ConvMultiplyMetadata deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(ConvCountWidth) group;
	Vector#(ConvLanes, Vector#(ConvTaps, Int#(16))) products;
	Vector#(ConvLanes, Int#(8)) bias;
} ConvProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(ConvCountWidth) group;
	Vector#(ConvLanes, Int#(17)) firstSum;
	Vector#(ConvLanes, Int#(17)) secondSum;
	Vector#(ConvLanes, Int#(8)) bias;
} ConvPairs deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(ConvCountWidth) group;
	Vector#(ConvLanes, Int#(18)) sum;
	Vector#(ConvLanes, Int#(8)) bias;
} ConvPartial deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(ConvCountWidth) group;
	Vector#(ConvLanes, Int#(ConvAccumulatorWidth)) value;
} ConvAffine deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(GateCountWidth) group;
	Vector#(GateLanes, Int#(8)) value;
} GateValues deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(GateCountWidth) group;
	Vector#(GateLanes, Int#(8)) scanned;
	Vector#(GateLanes, Int#(8)) gate;
} GateOperands deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(GateCountWidth) group;
	Vector#(GateLanes, Int#(16)) value;
} GateProducts deriving (Bits, Eq, FShow);

module mkSwayBlock#(Integer blockId)(BlockIfc);
	Integer firstLinear = 1 + blockId * 4;
	Integer inExp = blockScale(blockId, "in");
	Integer convInputExp = blockScale(blockId, "convInput");
	Integer gateInputExp = blockScale(blockId, "gateInput");
	Integer convProductExp = convInputExp + blockScale(blockId, "convWeight");
	Integer convBiasExp = blockScale(blockId, "convBias");
	Integer convAccumulatorExp = convProductExp < convBiasExp ? convProductExp : convBiasExp;
	Integer projectionExp = blockScale(blockId, "xProjection");
	Integer residualInputExp = blockId == 0 ? nodeScale("embedding") : blockScale(blockId - 1, "residual");
	Integer outExp = blockScale(blockId, "out");
	Integer residualAccumulatorExp = residualInputExp < outExp ? residualInputExp : outExp;
	Integer convolutionBound = valueOf(ConvTaps) * 16384 * (2 ** (convProductExp - convAccumulatorExp))
		+ 128 * (2 ** (convBiasExp - convAccumulatorExp));
	Integer residualBound = 128 * (2 ** (outExp - residualAccumulatorExp))
		+ 128 * (2 ** (residualInputExp - residualAccumulatorExp));
	staticAssert(valueOf(InnerDim) % valueOf(ConvLanes) == 0 && valueOf(InnerDim) % valueOf(GateLanes) == 0,
		"Convolution and gate lanes must divide the channel count");
	staticAssert(convolutionBound < 2 ** (valueOf(ConvAccumulatorWidth) - 1)
		&& residualBound < 2 ** (valueOf(ResidualAccumulatorWidth) - 1),
		"Aligned convolution or residual sum exceeds its datapath width");
	staticAssert(blockScale(blockId, "conv") == blockScale(blockId, "x")
		&& blockScale(blockId, "deltaProjection") == blockScale(blockId, "delta"),
		"MARS convolution and delta aliases must retain their source scales");

	NormIfc normalization <- mkSwayNorm(blockId);
	// Slices retain the frozen affine output scale before each branch's requantization.
	LinearIfc#(ModelDim, InnerDim) mainProjection <- mkSwayLinearSlice(firstLinear, 0);
	LinearIfc#(ModelDim, InnerDim) gateProjection <- mkSwayLinearSlice(firstLinear, valueOf(InnerDim));
	LinearIfc#(InnerDim, DeltaRank) deltaInputProjection <- mkSwayLinearSlice(firstLinear + 1, 0);
	LinearIfc#(InnerDim, StateDim) bProjection <- mkSwayLinearSlice(firstLinear + 1, valueOf(DeltaRank));
	LinearIfc#(InnerDim, StateDim) cProjection <- mkSwayLinearSlice(firstLinear + 1, valueOf(DeltaRank) + valueOf(StateDim));
	LinearIfc#(DeltaRank, InnerDim) deltaProjection <- mkSwayLinear(firstLinear + 2);
	LinearIfc#(InnerDim, ModelDim) outputProjection <- mkSwayLinear(firstLinear + 3);
	ScanIfc scan <- mkSwayScan(blockId);
	Vector#(ConvLanes, Vector#(ConvTaps, MultiplyIfc)) convMultipliers <- replicateM(replicateM(mkMultiply));
	Vector#(GateLanes, MultiplyIfc) gateMultipliers <- replicateM(mkMultiply);

	FIFO#(Token#(ModelDim)) inputQ <- mkFIFO1;
	// Four residual slots allow tokens to occupy independent downstream engines.
	FIFO#(Token#(ModelDim)) residualQ <- mkSizedFIFO(valueOf(ResidualSlots));
	FIFO#(Token#(ModelDim)) outputQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) mainQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) gateInputQ <- mkFIFO1;
	FIFO#(ConvOperands) convOperandsQ <- mkFIFO;
	FIFO#(ConvProducts) convProductsQ <- mkFIFO;
	FIFO#(ConvMultiplyMetadata) convMultiplyMetadataQ <- mkSizedFIFO(8);
	FIFO#(ConvPairs) convPairsQ <- mkFIFO;
	FIFO#(ConvPartial) convolvedQ <- mkFIFO;
	FIFO#(ConvAffine) convAffineQ <- mkFIFO;
	FIFO#(GateValues) gateActivationInputQ <- mkFIFO;
	FIFO#(GateValues) gateActivationResultQ <- mkFIFO;
	FIFO#(GateOperands) gateOperandsQ <- mkFIFO;
	FIFO#(GateProducts) gateProductsQ <- mkFIFO;
	FIFO#(Bit#(GateCountWidth)) gateMultiplyMetadataQ <- mkSizedFIFO(8);
	FIFO#(Token#(InnerDim)) xQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) xDelayQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) gateDelayQ <- mkSizedFIFO(valueOf(ResidualSlots));
	FIFO#(Token#(StateDim)) bQ <- mkFIFO1;
	FIFO#(Token#(StateDim)) cQ <- mkFIFO1;

	// Three causal samples per channel. Each lane rotates its rows once per token.
	Vector#(ConvHistory, Vector#(ConvLanes, Vector#(ConvGroups, Reg#(Int#(8))))) historyR <- replicateM(replicateM(replicateM(mkRegU)));
	Reg#(Token#(InnerDim)) mainR <- mkRegU;
	Reg#(Bit#(4)) convIndexR <- mkRegU;
	Vector#(InnerDim, Reg#(Int#(8))) xR <- replicateM(mkRegU);
	Reg#(Bit#(ConvCountWidth)) convGroupCnt <- mkReg(0);
	Reg#(Bool) convolutionOn <- mkReg(False);
	Reg#(Bool) convIssueOn <- mkReg(False);

	Reg#(Token#(InnerDim)) gateInputR <- mkRegU;
	Reg#(Bit#(4)) gateActivationIndexR <- mkRegU;
	Vector#(InnerDim, Reg#(Int#(8))) gateR <- replicateM(mkRegU);
	Reg#(Bit#(GateCountWidth)) gateActivationCnt <- mkReg(0);
	Reg#(Bool) gateActivationOn <- mkReg(False);
	Reg#(Bool) gateActivationIssueOn <- mkReg(False);

	Reg#(Token#(InnerDim)) scannedR <- mkRegU;
	Reg#(Bit#(4)) gatingIndexR <- mkRegU;
	Reg#(Token#(InnerDim)) delayedGateR <- mkRegU;
	Vector#(InnerDim, Reg#(Int#(8))) gatedR <- replicateM(mkRegU);
	Reg#(Bit#(GateCountWidth)) gateGroupCnt <- mkReg(0);
	Reg#(Bool) gatingOn <- mkReg(False);
	Reg#(Bool) gatingIssueOn <- mkReg(False);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Normalize, then broadcast to independent main and gate projection engines.
	//------------------------------------------------------------------------------------
	rule process1;
		let value = inputQ.first;
		inputQ.deq;
		normalization.put(value);
		residualQ.enq(value);
	endrule

	rule process2;
		let value <- normalization.get;
		mainProjection.put(value);
		gateProjection.put(value);
	endrule

	rule process3Main;
		let value <- mainProjection.get;
		mainQ.enq(value);
	endrule

	rule process3Gate;
		let value <- gateProjection.get;
		gateInputQ.enq(value);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Depthwise channels use all four taps in parallel. Token zero replaces history.
	// The quantized convolution output enters the SSM path without an activation.
	// Gate SiLU has its own stage and does not wait for convolution.
	//------------------------------------------------------------------------------------
	rule process4_1 ( !convolutionOn );
		let value = mainQ.first;
		mainQ.deq;
		mainR <= value;
		convIndexR <= value.index;
		convolutionOn <= True;
		convIssueOn <= True;
	endrule

	rule process4_2 ( convolutionOn && convIssueOn );
		ConvOperands value = unpack(0);
		value.group = convGroupCnt;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Bit#(6) channel = (zeroExtend(convGroupCnt) * fromInteger(valueOf(ConvLanes))) + fromInteger(lane);
			Int#(8) inputValue = requant32(signExtend(mainR.data[lane]), inExp, convInputExp);
			for ( Integer tap = 0; tap < valueOf(ConvHistory); tap = tap + 1 ) begin
				value.samples[lane][tap] = mainR.index == 0 ? 0 : historyR[tap][lane][0];
			end
			value.samples[lane][3] = inputValue;
			for ( Integer tap = 0; tap < valueOf(ConvTaps); tap = tap + 1 ) begin
				value.weights[lane][tap] = convWeight(blockId, tap, channel);
			end
			value.bias[lane] = convBias(blockId, channel);
			// A complete token rotates every row back to its original channel.
			for ( Integer tap = 0; tap < valueOf(ConvHistory); tap = tap + 1 ) begin
				for ( Integer group = 0; group < valueOf(ConvGroups) - 1; group = group + 1 ) begin
					historyR[tap][lane][group] <= historyR[tap][lane][group + 1];
				end
			end
			// Token zero replaces every row before the next token starts.
			historyR[0][lane][valueOf(ConvGroups) - 1] <= value.samples[lane][1];
			historyR[1][lane][valueOf(ConvGroups) - 1] <= value.samples[lane][2];
			historyR[2][lane][valueOf(ConvGroups) - 1] <= value.samples[lane][3];
		end
		convOperandsQ.enq(value);
		// Fixed lane selection avoids a channel mux feeding every history row.
		Token#(InnerDim) remaining = Token {index: mainR.index, data: replicate(0)};
		for ( Integer channel = 0; channel < valueOf(InnerDim) - valueOf(ConvLanes); channel = channel + 1 ) begin
			remaining.data[channel] = mainR.data[channel + valueOf(ConvLanes)];
		end
		mainR <= remaining;
		if ( convGroupCnt == fromInteger(valueOf(ConvGroups) - 1) ) begin
			convGroupCnt <= 0;
			convIssueOn <= False;
		end else begin
			convGroupCnt <= convGroupCnt + 1;
		end
	endrule

	rule process4_3 ( convolutionOn );
		let operands = convOperandsQ.first;
		convOperandsQ.deq;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			for ( Integer tap = 0; tap < valueOf(ConvTaps); tap = tap + 1 ) begin
				convMultipliers[lane][tap].put(signExtend(operands.samples[lane][tap]), signExtend(operands.weights[lane][tap]));
			end
		end
		convMultiplyMetadataQ.enq(ConvMultiplyMetadata {group: operands.group, bias: operands.bias});
	endrule

	rule process4_3_2 ( convolutionOn );
		let metadata = convMultiplyMetadataQ.first;
		convMultiplyMetadataQ.deq;
		ConvProducts value = unpack(0);
		value.group = metadata.group;
		value.bias = metadata.bias;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			for ( Integer tap = 0; tap < valueOf(ConvTaps); tap = tap + 1 ) begin
				let product <- convMultipliers[lane][tap].get;
				value.products[lane][tap] = truncate(product);
			end
		end
		convProductsQ.enq(value);
	endrule

	rule process4_4 ( convolutionOn );
		let products = convProductsQ.first;
		convProductsQ.deq;
		ConvPairs value = unpack(0);
		value.group = products.group;
		value.bias = products.bias;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.firstSum[lane] = signExtend(products.products[lane][0]) + signExtend(products.products[lane][1]);
			value.secondSum[lane] = signExtend(products.products[lane][2]) + signExtend(products.products[lane][3]);
		end
		convPairsQ.enq(value);
	endrule

	rule process4_5 ( convolutionOn );
		let pairs = convPairsQ.first;
		convPairsQ.deq;
		ConvPartial value = unpack(0);
		value.group = pairs.group;
		value.bias = pairs.bias;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.sum[lane] = signExtend(pairs.firstSum[lane]) + signExtend(pairs.secondSum[lane]);
		end
		convolvedQ.enq(value);
	endrule

	rule process4_6 ( convolutionOn );
		let convolved = convolvedQ.first;
		convolvedQ.deq;
		ConvAffine value = unpack(0);
		value.group = convolved.group;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Int#(ConvAccumulatorWidth) accumulator = shiftRoundN(signExtend(convolved.sum[lane]), convProductExp, convAccumulatorExp);
			value.value[lane] = accumulator + shiftRoundN(signExtend(convolved.bias[lane]), convBiasExp, convAccumulatorExp);
		end
		convAffineQ.enq(value);
	endrule

	rule process4_7 ( convolutionOn );
		let value = convAffineQ.first;
		convAffineQ.deq;
		Vector#(ConvLanes, Int#(8)) laneResults = newVector;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			laneResults[lane] = requantN(value.value[lane], convAccumulatorExp, blockScale(blockId, "conv"));
		end
		Vector#(InnerDim, Int#(8)) x = newVector;
		for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
			Integer group = channel / valueOf(ConvLanes);
			Integer lane = channel % valueOf(ConvLanes);
			if ( value.group == fromInteger(group) ) begin
				xR[channel] <= laneResults[lane];
			end
			// Forward the final group before its register writes take effect.
			if ( group == valueOf(ConvGroups) - 1 ) begin
				x[channel] = laneResults[lane];
			end else begin
				x[channel] = xR[channel];
			end
		end
		if ( value.group == fromInteger(valueOf(ConvGroups) - 1) ) begin
			xQ.enq(Token {index: convIndexR, data: x});
			convolutionOn <= False;
		end
	endrule

	rule gateSiLU1 ( !gateActivationOn );
		let value = gateInputQ.first;
		gateInputQ.deq;
		gateInputR <= value;
		gateActivationIndexR <= value.index;
		gateActivationCnt <= 0;
		gateActivationOn <= True;
		gateActivationIssueOn <= True;
	endrule

	rule gateSiLU2 ( gateActivationOn && gateActivationIssueOn );
		GateValues value = unpack(0);
		value.group = gateActivationCnt;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			value.value[lane] = requant32(signExtend(gateInputR.data[lane]), inExp, gateInputExp);
		end
		gateActivationInputQ.enq(value);
		Token#(InnerDim) remaining = Token {index: gateInputR.index, data: replicate(0)};
		for ( Integer channel = 0; channel < valueOf(InnerDim) - valueOf(GateLanes); channel = channel + 1 ) begin
			remaining.data[channel] = gateInputR.data[channel + valueOf(GateLanes)];
		end
		gateInputR <= remaining;
		gateActivationCnt <= gateActivationCnt + 1;
		if ( gateActivationCnt == fromInteger(valueOf(GateGroups) - 1) ) begin
			gateActivationIssueOn <= False;
		end
	endrule

	rule gateSiLU3 ( gateActivationOn );
		let value = gateActivationInputQ.first;
		gateActivationInputQ.deq;
		GateValues result = unpack(0);
		result.group = value.group;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			result.value[lane] = nonlinearLookup(blockId * 2, value.value[lane]);
		end
		gateActivationResultQ.enq(result);
	endrule

	rule gateSiLU4 ( gateActivationOn );
		let value = gateActivationResultQ.first;
		gateActivationResultQ.deq;
		let laneResults = value.value;
		Vector#(InnerDim, Int#(8)) gate = newVector;
		for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
			Integer group = channel / valueOf(GateLanes);
			Integer lane = channel % valueOf(GateLanes);
			if ( value.group == fromInteger(group) ) begin
				gateR[channel] <= laneResults[lane];
			end
			// Forward the final group before its register writes take effect.
			if ( group == valueOf(GateGroups) - 1 ) begin
				gate[channel] = laneResults[lane];
			end else begin
				gate[channel] = gateR[channel];
			end
		end
		if ( value.group == fromInteger(valueOf(GateGroups) - 1) ) begin
			gateDelayQ.enq(Token {index: gateActivationIndexR, data: gate});
			gateActivationOn <= False;
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Independent delta-input, B and C engines retain their split calibration scales.
	// Delta follows Linear -> ReLU -> Linear; the second projection remains signed.
	//------------------------------------------------------------------------------------
	rule process5;
		let value = xQ.first;
		xQ.deq;
		deltaInputProjection.put(value);
		bProjection.put(value);
		cProjection.put(value);
		xDelayQ.enq(value);
	endrule

	rule process6Delta;
		let value <- deltaInputProjection.get;
		Vector#(DeltaRank, Int#(8)) deltaInput = newVector;
		for ( Integer lane = 0; lane < valueOf(DeltaRank); lane = lane + 1 ) begin
			Int#(8) reluValue = value.data[lane] < 0 ? 0 : value.data[lane];
			deltaInput[lane] = requant32(signExtend(reluValue), projectionExp, blockScale(blockId, "deltaInput"));
		end
		deltaProjection.put(Token {index: value.index, data: deltaInput});
	endrule

	rule process6B;
		let value <- bProjection.get;
		Vector#(StateDim, Int#(8)) b = newVector;
		for ( Integer lane = 0; lane < valueOf(StateDim); lane = lane + 1 ) begin
			b[lane] = requant32(signExtend(value.data[lane]), projectionExp, blockScale(blockId, "B"));
		end
		bQ.enq(Token {index: value.index, data: b});
	endrule

	rule process6C;
		let value <- cProjection.get;
		Vector#(StateDim, Int#(8)) c = newVector;
		for ( Integer lane = 0; lane < valueOf(StateDim); lane = lane + 1 ) begin
			c[lane] = requant32(signExtend(value.data[lane]), projectionExp, blockScale(blockId, "C"));
		end
		cQ.enq(Token {index: value.index, data: c});
	endrule

	rule process7;
		let delta <- deltaProjection.get;
		let x = xDelayQ.first;
		xDelayQ.deq;
		let b = bQ.first;
		bQ.deq;
		let c = cQ.first;
		cQ.deq;
		scan.put(ScanToken {index: x.index, x: x.data, delta: delta.data, b: b.data, c: c.data});
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 4]
	// Rejoin the aligned gate after recurrence and apply GateLanes products per cycle.
	//------------------------------------------------------------------------------------
	rule process8 ( !gatingOn );
		let value <- scan.get;
		scannedR <= value;
		gatingIndexR <= value.index;
		delayedGateR <= gateDelayQ.first;
		gateDelayQ.deq;
		gatingOn <= True;
		gatingIssueOn <= True;
	endrule

	rule process9 ( gatingOn && gatingIssueOn );
		GateOperands value = unpack(0);
		value.group = gateGroupCnt;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			value.scanned[lane] = scannedR.data[lane];
			value.gate[lane] = delayedGateR.data[lane];
		end
		gateOperandsQ.enq(value);
		Token#(InnerDim) remainingScan = Token {index: scannedR.index, data: replicate(0)};
		Token#(InnerDim) remainingGate = Token {index: delayedGateR.index, data: replicate(0)};
		for ( Integer channel = 0; channel < valueOf(InnerDim) - valueOf(GateLanes); channel = channel + 1 ) begin
			remainingScan.data[channel] = scannedR.data[channel + valueOf(GateLanes)];
			remainingGate.data[channel] = delayedGateR.data[channel + valueOf(GateLanes)];
		end
		scannedR <= remainingScan;
		delayedGateR <= remainingGate;
		if ( gateGroupCnt == fromInteger(valueOf(GateGroups) - 1) ) begin
			gateGroupCnt <= 0;
			gatingIssueOn <= False;
		end else begin
			gateGroupCnt <= gateGroupCnt + 1;
		end
	endrule

	rule process9_2 ( gatingOn );
		let operands = gateOperandsQ.first;
		gateOperandsQ.deq;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			gateMultipliers[lane].put(signExtend(operands.scanned[lane]), signExtend(operands.gate[lane]));
		end
		gateMultiplyMetadataQ.enq(operands.group);
	endrule

	rule process9_2_2 ( gatingOn );
		let group = gateMultiplyMetadataQ.first;
		gateMultiplyMetadataQ.deq;
		GateProducts value = unpack(0);
		value.group = group;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			let product <- gateMultipliers[lane].get;
			value.value[lane] = truncate(product);
		end
		gateProductsQ.enq(value);
	endrule

	rule process9_3 ( gatingOn );
		let value = gateProductsQ.first;
		gateProductsQ.deq;
		Vector#(GateLanes, Int#(8)) laneResults = newVector;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			laneResults[lane] = requant32(signExtend(value.value[lane]), blockScale(blockId, "ssmY") + blockScale(blockId, "gate"), blockScale(blockId, "gated"));
		end
		Vector#(InnerDim, Int#(8)) result = newVector;
		for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
			Integer group = channel / valueOf(GateLanes);
			Integer lane = channel % valueOf(GateLanes);
			if ( value.group == fromInteger(group) ) begin
				gatedR[channel] <= laneResults[lane];
			end
			// Forward the final group before its register writes take effect.
			if ( group == valueOf(GateGroups) - 1 ) begin
				result[channel] = laneResults[lane];
			end else begin
				result[channel] = gatedR[channel];
			end
		end
		if ( value.group == fromInteger(valueOf(GateGroups) - 1) ) begin
			outputProjection.put(Token {index: gatingIndexR, data: result});
			gatingOn <= False;
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 5]
	// Align exponents before residual addition, then round and saturate once.
	//------------------------------------------------------------------------------------
	rule process10;
		let projected <- outputProjection.get;
		let residual = residualQ.first;
		residualQ.deq;
		Vector#(ModelDim, Int#(8)) result = newVector;
		for ( Integer channel = 0; channel < valueOf(ModelDim); channel = channel + 1 ) begin
			Int#(ResidualAccumulatorWidth) accumulator = shiftRoundN(signExtend(projected.data[channel]), outExp, residualAccumulatorExp);
			accumulator = accumulator + shiftRoundN(signExtend(residual.data[channel]), residualInputExp, residualAccumulatorExp);
			result[channel] = requantN(accumulator, residualAccumulatorExp, blockScale(blockId, "residual"));
		end
		outputQ.enq(Token {index: projected.index, data: result});
	endrule

	//------------------------------------------------------------------------------------
	// Interface
	//------------------------------------------------------------------------------------
	method Action put(Token#(ModelDim) value);
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(ModelDim)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

endpackage
