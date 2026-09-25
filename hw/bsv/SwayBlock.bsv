package SwayBlock;

import Assert::*;
import Vector::*;
import FIFO::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayLinear::*;
import SwayNorm::*;
import SwayScan::*;

typedef 18 ConvAccumulatorWidth;
typedef 10 ResidualAccumulatorWidth;
typedef TDiv#(InnerDim, ConvLanes) ConvGroups;
typedef TLog#(TAdd#(ConvGroups, 1)) ConvCountWidth;
typedef TDiv#(InnerDim, GateLanes) GateGroups;
typedef TLog#(TAdd#(GateGroups, 1)) GateCountWidth;

typedef struct {
	Bit#(ConvCountWidth) group;
	Vector#(ConvLanes, Int#(18)) sum;
} ConvPartial deriving (Bits, Eq, FShow);

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

	FIFO#(Token#(ModelDim)) inputQ <- mkFIFO1;
	// Four residual slots allow tokens to occupy independent downstream engines.
	FIFO#(Token#(ModelDim)) residualQ <- mkSizedFIFO(valueOf(ResidualSlots));
	FIFO#(Token#(ModelDim)) outputQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) mainQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) gateInputQ <- mkFIFO1;
	FIFO#(ConvPartial) convolvedQ <- mkFIFO;
	FIFO#(Token#(InnerDim)) xQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) xDelayQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) gateDelayQ <- mkSizedFIFO(valueOf(ResidualSlots));
	FIFO#(Token#(StateDim)) bQ <- mkFIFO1;
	FIFO#(Token#(StateDim)) cQ <- mkFIFO1;

	// Three causal samples per channel, banked across the convolution lanes.
	Vector#(ConvHistory, Vector#(ConvLanes, Vector#(ConvGroups, Reg#(Int#(8))))) historyR <- replicateM(replicateM(replicateM(mkReg(0))));
	Reg#(Token#(InnerDim)) mainR <- mkRegU;
	Reg#(Vector#(InnerDim, Int#(8))) xR <- mkRegU;
	Reg#(Bit#(ConvCountWidth)) convGroupCnt <- mkReg(0);
	Reg#(Bool) convolutionOn <- mkReg(False);

	Reg#(Token#(InnerDim)) gateInputR <- mkRegU;
	Reg#(Vector#(InnerDim, Int#(8))) gateR <- mkRegU;
	Reg#(Bit#(GateCountWidth)) gateActivationCnt <- mkReg(0);
	Reg#(Bool) gateActivationOn <- mkReg(False);

	Reg#(Token#(InnerDim)) scannedR <- mkRegU;
	Reg#(Token#(InnerDim)) delayedGateR <- mkRegU;
	Reg#(Vector#(InnerDim, Int#(8))) gatedR <- mkRegU;
	Reg#(Bit#(GateCountWidth)) gateGroupCnt <- mkReg(0);
	Reg#(Bool) gatingOn <- mkReg(False);

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
		mainR <= mainQ.first;
		mainQ.deq;
		convGroupCnt <= 0;
		convolutionOn <= True;
	endrule

	rule process4_2 ( convolutionOn && convGroupCnt < fromInteger(valueOf(ConvGroups)) );
		ConvPartial value = unpack(0);
		value.group = convGroupCnt;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Bit#(6) channel = (zeroExtend(convGroupCnt) * fromInteger(valueOf(ConvLanes))) + fromInteger(lane);
			Int#(8) inputValue = requant32(signExtend(mainR.data[channel]), inExp, convInputExp);
			Vector#(ConvTaps, Int#(8)) samples = newVector;
			for ( Integer tap = 0; tap < valueOf(ConvHistory); tap = tap + 1 ) begin
				samples[tap] = mainR.index == 0 ? 0 : historyR[tap][lane][convGroupCnt];
			end
			samples[3] = inputValue;
			Vector#(ConvTaps, Int#(16)) products = newVector;
			for ( Integer tap = 0; tap < valueOf(ConvTaps); tap = tap + 1 ) begin
				products[tap] = signExtend(samples[tap]) * signExtend(convWeight(blockId, tap, channel));
			end
			Int#(17) firstSum = signExtend(products[0]) + signExtend(products[1]);
			Int#(17) secondSum = signExtend(products[2]) + signExtend(products[3]);
			value.sum[lane] = signExtend(firstSum) + signExtend(secondSum);
			historyR[0][lane][convGroupCnt] <= samples[1];
			historyR[1][lane][convGroupCnt] <= samples[2];
			historyR[2][lane][convGroupCnt] <= samples[3];
		end
		convolvedQ.enq(value);
		convGroupCnt <= convGroupCnt + 1;
	endrule

	rule process4_3 ( convolutionOn );
		let value = convolvedQ.first;
		convolvedQ.deq;
		Vector#(InnerDim, Int#(8)) x = xR;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Bit#(6) channel = (zeroExtend(value.group) * fromInteger(valueOf(ConvLanes))) + fromInteger(lane);
			Int#(ConvAccumulatorWidth) accumulator = shiftRoundN(signExtend(value.sum[lane]), convProductExp, convAccumulatorExp);
			accumulator = accumulator + shiftRoundN(signExtend(convBias(blockId, channel)), convBiasExp, convAccumulatorExp);
			Int#(8) convolved = requantN(accumulator, convAccumulatorExp, blockScale(blockId, "conv"));
			x[channel] = convolved;
		end
		xR <= x;
		if ( value.group == fromInteger(valueOf(ConvGroups) - 1) ) begin
			xQ.enq(Token {index: mainR.index, data: x});
			convolutionOn <= False;
		end
	endrule

	rule gateSiLU1 ( !gateActivationOn );
		gateInputR <= gateInputQ.first;
		gateInputQ.deq;
		gateActivationCnt <= 0;
		gateActivationOn <= True;
	endrule

	rule gateSiLU2 ( gateActivationOn );
		Vector#(InnerDim, Int#(8)) gate = gateR;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			Bit#(6) channel = zeroExtend(gateActivationCnt) * fromInteger(valueOf(GateLanes)) + fromInteger(lane);
			Int#(8) inputValue = requant32(signExtend(gateInputR.data[channel]), inExp, gateInputExp);
			gate[channel] = nonlinearLookup(blockId * 2, inputValue);
		end
		gateR <= gate;
		if ( gateActivationCnt == fromInteger(valueOf(GateGroups) - 1) ) begin
			gateDelayQ.enq(Token {index: gateInputR.index, data: gate});
			gateActivationOn <= False;
		end else begin
			gateActivationCnt <= gateActivationCnt + 1;
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
		delayedGateR <= gateDelayQ.first;
		gateDelayQ.deq;
		gateGroupCnt <= 0;
		gatingOn <= True;
	endrule

	rule process9 ( gatingOn );
		Vector#(InnerDim, Int#(8)) result = gatedR;
		for ( Integer lane = 0; lane < valueOf(GateLanes); lane = lane + 1 ) begin
			Bit#(6) channel = (zeroExtend(gateGroupCnt) * fromInteger(valueOf(GateLanes))) + fromInteger(lane);
			Int#(16) product = signExtend(scannedR.data[channel]) * signExtend(delayedGateR.data[channel]);
			result[channel] = requant32(signExtend(product), blockScale(blockId, "ssmY") + blockScale(blockId, "gate"), blockScale(blockId, "gated"));
		end
		gatedR <= result;
		if ( gateGroupCnt == fromInteger(valueOf(GateGroups) - 1) ) begin
			outputProjection.put(Token {index: scannedR.index, data: result});
			gatingOn <= False;
		end else begin
			gateGroupCnt <= gateGroupCnt + 1;
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
