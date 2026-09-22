package SwayBlock;

import Vector::*;
import FIFO::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayReset::*;
import SwayLookup::*;
import SwayLinear::*;
`ifdef SWAY_REALLOCATE
import SwayFoldedLinear::*;
`endif
import SwayNorm::*;
import SwayScan::*;

typedef 2 ConvLanes;
typedef 20 ConvGroups;

typedef struct {
	Bit#(5) group;
	Bool zeroHistory;
} ConvAddress deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Vector#(4, Int#(8))) samples;
	Vector#(ConvLanes, Vector#(4, Int#(8))) weights;
	Vector#(ConvLanes, Int#(8)) bias;
	Vector#(ConvLanes, Int#(8)) gate;
} ConvOperands deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Vector#(4, Int#(13))) low;
	Vector#(ConvLanes, Vector#(4, Int#(12))) high;
	Vector#(ConvLanes, Int#(8)) bias;
	Vector#(ConvLanes, Int#(8)) gate;
} ConvPartialProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Vector#(4, Int#(16))) products;
	Vector#(ConvLanes, Int#(8)) bias;
	Vector#(ConvLanes, Int#(8)) gate;
} ConvProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Vector#(2, Int#(17))) pairs;
	Vector#(ConvLanes, Int#(8)) bias;
	Vector#(ConvLanes, Int#(8)) gate;
} ConvPairs deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Int#(18)) sum;
	Vector#(ConvLanes, Int#(8)) bias;
	Vector#(ConvLanes, Int#(8)) gate;
} ConvPartial deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Int#(19)) sum;
	Vector#(ConvLanes, Int#(8)) gate;
} ConvAccumulated deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Int#(8)) x;
	Vector#(ConvLanes, Int#(8)) gate;
} ConvActivated deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Bit#(4)) xHigh;
	Vector#(ConvLanes, Bit#(4)) gateHigh;
	Vector#(ConvLanes, Vector#(16, Int#(8))) xTable;
	Vector#(ConvLanes, Vector#(16, Int#(8))) gateTable;
} ConvLookup deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Int#(8)) x;
	Vector#(ConvLanes, Int#(8)) gate;
} GateOperands deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Int#(13)) low;
	Vector#(ConvLanes, Int#(12)) high;
} GatePartialProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Int#(16)) products;
} GateProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(5) group;
	Vector#(ConvLanes, Int#(8)) values;
} GateResults deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(4) index;
	Vector#(20, Int#(10)) values;
} ResidualSums deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(4) index;
	Vector#(8, Int#(8)) b;
	Vector#(8, Int#(8)) c;
} StateParameters deriving (Bits, Eq, FShow);

`ifdef SWAY_REALLOCATE
module mkSwayBlock#(Integer blockId, LinearIfc#(2, 40) deltaProjection)(BlockIfc);
`else
module mkSwayBlock#(Integer blockId)(BlockIfc);
`endif
	LocalResetIfc localReset <- mkSwayLocalReset;
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
	// Four INT8 products need 18 bits; aligned bias needs 11 bits for this export.
	if ( convProductExp != convAccumulatorExp || convBiasExp - convAccumulatorExp > 3 || residualInputExp - residualAccumulatorExp > 1 || outExp - residualAccumulatorExp > 1 ) begin
		error("SwayBlock fixed-point bounds do not cover this quantization profile");
	end

	// Child engines derive sibling reset leaves from the unchanged root reset.
	NormIfc normalization <- mkSwayNorm(blockId);
	LinearIfc#(20, 80) inputProjection <- mkSwayLinear(firstLinear);
`ifdef SWAY_REALLOCATE
	LinearIfc#(40, 18) stateProjection <- mkSwayFoldedLinear(firstLinear + 1, 2);
	LinearIfc#(40, 20) outputProjection <- mkSwayFoldedLinear(firstLinear + 3, 2);
`else
	LinearIfc#(40, 18) stateProjection <- mkSwayLinear(firstLinear + 1);
	LinearIfc#(2, 40) deltaProjection <- mkSwayLinear(firstLinear + 2);
	LinearIfc#(40, 20) outputProjection <- mkSwayLinear(firstLinear + 3);
`endif
	ScanIfc scan <- mkSwayScan(blockId);

	FIFO#(Token#(20)) inputQ <- mkFIFO1(reset_by localReset.rst);
	// Buffer sizing is a conventional baseline correction, applied to both blocks.
`ifdef SWAY_BUFFERED
	FIFO#(Token#(20)) residualQ <- mkSizedFIFO(5, reset_by localReset.rst);
`else
	FIFO#(Token#(20)) residualQ <- mkSizedFIFO(4, reset_by localReset.rst);
`endif
	FIFO#(Token#(20)) outputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Token#(80)) expandedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvAddress) convAddressQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvOperands) convSelectedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvOperands) convOperandQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvPartialProducts) convPartialProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvProducts) convProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvPairs) convPairQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvPartial) convolvedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvAccumulated) convAccumulatedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvActivated) convQuantizedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvLookup) convLookupQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ConvActivated) convActivatedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(GateOperands) gateOperandQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(GatePartialProducts) gatePartialProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(GateProducts) gateProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(GateResults) gateResultQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ResidualSums) residualSumQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Token#(40)) activatedQ <- mkFIFO1(reset_by localReset.rst);
`ifdef SWAY_BUFFERED
	FIFO#(Token#(40)) xDelayQ <- mkSizedFIFO(2, reset_by localReset.rst);
`else
	FIFO#(Token#(40)) xDelayQ <- mkFIFO1(reset_by localReset.rst);
`endif
	FIFO#(Token#(40)) gateDelayQ <- mkSizedFIFO(4, reset_by localReset.rst);
	FIFO#(StateParameters) stateParameterQ <- mkFIFO1(reset_by localReset.rst);

	// Token zero initializes all three causal samples before any later token reads them.
	Vector#(3, Vector#(ConvLanes, Vector#(ConvGroups, Reg#(Int#(8))))) historyR <- replicateM(replicateM(replicateM(mkRegU)));
	Reg#(Token#(80)) expandedR <- mkRegU;
	Vector#(ConvLanes, Vector#(ConvGroups, Reg#(Int#(8)))) activatedR <- replicateM(replicateM(mkRegU));
	Vector#(ConvLanes, Vector#(ConvGroups, Reg#(Int#(8)))) gateR <- replicateM(replicateM(mkRegU));
	Reg#(Bit#(5)) convGroupCnt <- mkRegU;
	Reg#(Bool) convolutionOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) convEmitOn <- mkReg(False, reset_by localReset.rst);

	Reg#(Token#(40)) scannedR <- mkRegU;
	Reg#(Token#(40)) delayedGateR <- mkRegU;
	Vector#(ConvLanes, Vector#(ConvGroups, Reg#(Int#(8)))) gatedR <- replicateM(replicateM(mkRegU));
	Reg#(Bit#(5)) gateGroupCnt <- mkRegU;
	Reg#(Bool) gatingOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) gateEmitOn <- mkReg(False, reset_by localReset.rst);

`ifdef SWAY_BLOCK_PROFILE
	// Only Block0 emits observations. Root reset keeps cycle stamps aligned with
	// the kernel testbench. Each boundary owns its counter; datapath rules never read it.
	Reg#(UInt#(64)) blockProfileCycleCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileNormFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileInProjFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileExpandedFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileConvStartFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileConvOutFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileStateProjFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileDeltaProjFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileScanFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileGateFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileOutProjFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileResidualFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileReadyFrameCnt <- mkReg(0);
	if ( blockId == 0 ) begin
		rule blockProfileTick;
			blockProfileCycleCnt <= blockProfileCycleCnt + 1;
		endrule
	end
`endif

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Normalize and expand, preserving the original token on the residual branch.
	//------------------------------------------------------------------------------------
	rule process1;
		let value = inputQ.first;
		inputQ.deq;
		normalization.put(value);
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,norm_in,%0d,%0d,%0d", blockProfileNormFrameCnt, value.index, blockProfileCycleCnt);
			if ( value.index == 15 ) blockProfileNormFrameCnt <= blockProfileNormFrameCnt + 1;
		end
`endif
		residualQ.enq(value);
	endrule

	rule process2;
		let value <- normalization.get;
		inputProjection.put(value);
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,inproj_in,%0d,%0d,%0d", blockProfileInProjFrameCnt, value.index, blockProfileCycleCnt);
			if ( value.index == 15 ) blockProfileInProjFrameCnt <= blockProfileInProjFrameCnt + 1;
		end
`endif
	endrule

	rule process3;
		let value <- inputProjection.get;
		expandedQ.enq(value);
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,inproj_out,%0d,%0d,%0d", blockProfileExpandedFrameCnt, value.index, blockProfileCycleCnt);
			if ( value.index == 15 ) blockProfileExpandedFrameCnt <= blockProfileExpandedFrameCnt + 1;
		end
`endif
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Two depthwise channels per cycle, each using all four taps in parallel.
	// Token zero injects causal zero padding and replaces every history entry.
	//------------------------------------------------------------------------------------
	rule process4_1 ( !convolutionOn );
		expandedR <= expandedQ.first;
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,conv_start,%0d,%0d,%0d", blockProfileConvStartFrameCnt, expandedQ.first.index, blockProfileCycleCnt);
			if ( expandedQ.first.index == 15 ) blockProfileConvStartFrameCnt <= blockProfileConvStartFrameCnt + 1;
		end
`endif
		expandedQ.deq;
		convGroupCnt <= 0;
		convolutionOn <= True;
	endrule

	rule process4_2_1 ( convolutionOn && convGroupCnt < fromInteger(valueOf(ConvGroups)) );
		convAddressQ.enq(ConvAddress {group: convGroupCnt, zeroHistory: expandedR.index == 0});
		convGroupCnt <= convGroupCnt + 1;
	endrule

	rule process4_2_2 ( convolutionOn );
		let address = convAddressQ.first;
		convAddressQ.deq;
		ConvOperands value = unpack(0);
		value.group = address.group;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Bit#(6) channel = {address.group, fromInteger(lane)};
			Vector#(ConvGroups, Int#(8)) xBank = newVector;
			Vector#(ConvGroups, Int#(8)) gateBank = newVector;
			for ( Integer group = 0; group < valueOf(ConvGroups); group = group + 1 ) begin
				xBank[group] = expandedR.data[group * valueOf(ConvLanes) + lane];
				gateBank[group] = expandedR.data[valueOf(InnerDim) + group * valueOf(ConvLanes) + lane];
			end
			for ( Integer tap = 0; tap < 3; tap = tap + 1 ) begin
				value.samples[lane][tap] = address.zeroHistory ? 0 : historyR[tap][lane][address.group];
			end
			value.samples[lane][3] = xBank[address.group];
			for ( Integer tap = 0; tap < 4; tap = tap + 1 ) begin
				value.weights[lane][tap] = convWeight(blockId, tap, channel);
			end
			value.bias[lane] = convBias(blockId, channel);
			value.gate[lane] = gateBank[address.group];
		end
		convSelectedQ.enq(value);
	endrule

	rule process4_2_3 ( convolutionOn );
		ConvOperands value = convSelectedQ.first;
		convSelectedQ.deq;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.samples[lane][3] = requant(signExtend(value.samples[lane][3]), inExp, convInputExp);
			value.gate[lane] = requant(signExtend(value.gate[lane]), inExp, gateInputExp);
			historyR[0][lane][value.group] <= value.samples[lane][1];
			historyR[1][lane][value.group] <= value.samples[lane][2];
			historyR[2][lane][value.group] <= value.samples[lane][3];
		end
		convOperandQ.enq(value);
	endrule

	rule process4_3_1;
		let inputValue = convOperandQ.first;
		convOperandQ.deq;
		ConvPartialProducts value = unpack(0);
		value.group = inputValue.group;
		value.bias = inputValue.bias;
		value.gate = inputValue.gate;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			for ( Integer tap = 0; tap < 4; tap = tap + 1 ) begin
				Bit#(8) sampleBits = pack(inputValue.samples[lane][tap]);
				Int#(5) low = unpack(zeroExtend(sampleBits[3:0]));
				Int#(4) high = unpack(sampleBits[7:4]);
				value.low[lane][tap] = signExtend(low) * signExtend(inputValue.weights[lane][tap]);
				value.high[lane][tap] = signExtend(high) * signExtend(inputValue.weights[lane][tap]);
			end
		end
		convPartialProductQ.enq(value);
	endrule

	rule process4_3_2;
		let inputValue = convPartialProductQ.first;
		convPartialProductQ.deq;
		ConvProducts value = unpack(0);
		value.group = inputValue.group;
		value.bias = inputValue.bias;
		value.gate = inputValue.gate;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			for ( Integer tap = 0; tap < 4; tap = tap + 1 ) begin
				Int#(16) high = signExtend(inputValue.high[lane][tap]);
				value.products[lane][tap] = signExtend(inputValue.low[lane][tap]) + (high << 4);
			end
		end
		convProductQ.enq(value);
	endrule

	rule process4_4;
		let inputValue = convProductQ.first;
		convProductQ.deq;
		ConvPairs value = unpack(0);
		value.group = inputValue.group;
		value.bias = inputValue.bias;
		value.gate = inputValue.gate;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.pairs[lane][0] = signExtend(inputValue.products[lane][0]) + signExtend(inputValue.products[lane][1]);
			value.pairs[lane][1] = signExtend(inputValue.products[lane][2]) + signExtend(inputValue.products[lane][3]);
		end
		convPairQ.enq(value);
	endrule

	rule process4_5;
		let inputValue = convPairQ.first;
		convPairQ.deq;
		ConvPartial value = unpack(0);
		value.group = inputValue.group;
		value.bias = inputValue.bias;
		value.gate = inputValue.gate;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.sum[lane] = signExtend(inputValue.pairs[lane][0]) + signExtend(inputValue.pairs[lane][1]);
		end
		convolvedQ.enq(value);
	endrule

	rule process4_6;
		let inputValue = convolvedQ.first;
		convolvedQ.deq;
		ConvAccumulated value = unpack(0);
		value.group = inputValue.group;
		value.gate = inputValue.gate;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Int#(19) bias = signExtend(inputValue.bias[lane]);
			bias = bias << (convBiasExp - convAccumulatorExp);
			value.sum[lane] = signExtend(inputValue.sum[lane]) + bias;
		end
		convAccumulatedQ.enq(value);
	endrule

	rule process4_7;
		let inputValue = convAccumulatedQ.first;
		convAccumulatedQ.deq;
		ConvActivated value = unpack(0);
		value.group = inputValue.group;
		value.gate = inputValue.gate;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.x[lane] = requant(signExtend(inputValue.sum[lane]), convAccumulatorExp, blockScale(blockId, "conv"));
		end
		convQuantizedQ.enq(value);
	endrule

	rule process4_8_1;
		let inputValue = convQuantizedQ.first;
		convQuantizedQ.deq;
		ConvLookup value = unpack(0);
		value.group = inputValue.group;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Bit#(8) xBits = pack(inputValue.x[lane]);
			Bit#(8) gateBits = pack(inputValue.gate[lane]);
			value.xHigh[lane] = xBits[7:4];
			value.gateHigh[lane] = gateBits[7:4];
			value.xTable[lane] = nonlinearCandidates(blockId * 3, xBits[3:0]);
			value.gateTable[lane] = nonlinearCandidates(blockId * 3 + 1, gateBits[3:0]);
		end
		convLookupQ.enq(value);
	endrule

	rule process4_8_2;
		let inputValue = convLookupQ.first;
		convLookupQ.deq;
		ConvActivated value = unpack(0);
		value.group = inputValue.group;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.x[lane] = inputValue.xTable[lane][inputValue.xHigh[lane]];
			value.gate[lane] = inputValue.gateTable[lane][inputValue.gateHigh[lane]];
		end
		convActivatedQ.enq(value);
	endrule

	rule process4_9 ( convolutionOn && !convEmitOn );
		let value = convActivatedQ.first;
		convActivatedQ.deq;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			activatedR[lane][value.group] <= value.x[lane];
			gateR[lane][value.group] <= value.gate[lane];
		end
		if ( value.group == fromInteger(valueOf(ConvGroups) - 1) ) begin
			convEmitOn <= True;
		end
	endrule

	// Emit only after the last pair has been stored. Wide output enables depend
	// on a registered completion flag rather than last-group decode and FIFO readiness.
	rule process4_10 ( convolutionOn && convEmitOn );
		Vector#(40, Int#(8)) x = newVector;
		Vector#(40, Int#(8)) gate = newVector;
		for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
			Integer lane = channel % valueOf(ConvLanes);
			Integer group = channel / valueOf(ConvLanes);
			x[channel] = activatedR[lane][group];
			gate[channel] = gateR[lane][group];
		end
		activatedQ.enq(Token {index: expandedR.index, data: x});
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,conv_out,%0d,%0d,%0d", blockProfileConvOutFrameCnt, expandedR.index, blockProfileCycleCnt);
			if ( expandedR.index == 15 ) blockProfileConvOutFrameCnt <= blockProfileConvOutFrameCnt + 1;
		end
`endif
		gateDelayQ.enq(Token {index: expandedR.index, data: gate});
		convEmitOn <= False;
		convolutionOn <= False;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Form delta, B and C. Each split has its own calibration exponent.
	//------------------------------------------------------------------------------------
	rule process5;
		let value = activatedQ.first;
		activatedQ.deq;
		stateProjection.put(value);
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,stateproj_in,%0d,%0d,%0d", blockProfileStateProjFrameCnt, value.index, blockProfileCycleCnt);
			if ( value.index == 15 ) blockProfileStateProjFrameCnt <= blockProfileStateProjFrameCnt + 1;
		end
`endif
		xDelayQ.enq(value);
	endrule

	rule process6;
		let value <- stateProjection.get;
		Vector#(2, Int#(8)) deltaInput = newVector;
		StateParameters parameters = unpack(0);
		parameters.index = value.index;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			deltaInput[lane] = requant(signExtend(value.data[lane]), projectionExp, blockScale(blockId, "deltaInput"));
		end
		for ( Integer lane = 0; lane < valueOf(StateDim); lane = lane + 1 ) begin
			parameters.b[lane] = requant(signExtend(value.data[2 + lane]), projectionExp, blockScale(blockId, "B"));
			parameters.c[lane] = requant(signExtend(value.data[2 + valueOf(StateDim) + lane]), projectionExp, blockScale(blockId, "C"));
		end
		deltaProjection.put(Token {index: value.index, data: deltaInput});
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,deltaproj_in,%0d,%0d,%0d", blockProfileDeltaProjFrameCnt, value.index, blockProfileCycleCnt);
			if ( value.index == 15 ) blockProfileDeltaProjFrameCnt <= blockProfileDeltaProjFrameCnt + 1;
		end
`endif
		stateParameterQ.enq(parameters);
	endrule

	rule process7;
		let delta <- deltaProjection.get;
		let x = xDelayQ.first;
		xDelayQ.deq;
		let parameters = stateParameterQ.first;
		stateParameterQ.deq;
		Vector#(40, Int#(8)) positiveDelta = newVector;
		for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
			Int#(8) reluValue = delta.data[channel] < 0 ? 0 : delta.data[channel];
			positiveDelta[channel] = requant(signExtend(reluValue), blockScale(blockId, "deltaProjection"), blockScale(blockId, "delta"));
		end
		scan.put(ScanToken {index: x.index, x: x.data, delta: positiveDelta, b: parameters.b, c: parameters.c});
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,scan_in,%0d,%0d,%0d", blockProfileScanFrameCnt, x.index, blockProfileCycleCnt);
			if ( x.index == 15 ) blockProfileScanFrameCnt <= blockProfileScanFrameCnt + 1;
		end
`endif
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 4]
	// Rejoin the aligned gate after recurrence and apply two products per cycle.
	//------------------------------------------------------------------------------------
	rule process8 ( !gatingOn );
		let value <- scan.get;
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,gate_in,%0d,%0d,%0d", blockProfileGateFrameCnt, value.index, blockProfileCycleCnt);
			if ( value.index == 15 ) blockProfileGateFrameCnt <= blockProfileGateFrameCnt + 1;
		end
`endif
		scannedR <= value;
		delayedGateR <= gateDelayQ.first;
		gateDelayQ.deq;
		gateGroupCnt <= 0;
		gatingOn <= True;
	endrule

	rule process9_1 ( gatingOn && gateGroupCnt < fromInteger(valueOf(ConvGroups)) );
		GateOperands value = unpack(0);
		value.group = gateGroupCnt;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Bit#(6) channel = {gateGroupCnt, fromInteger(lane)};
			value.x[lane] = scannedR.data[channel];
			value.gate[lane] = delayedGateR.data[channel];
		end
		gateOperandQ.enq(value);
		gateGroupCnt <= gateGroupCnt + 1;
	endrule

	rule process9_2_1;
		let inputValue = gateOperandQ.first;
		gateOperandQ.deq;
		GatePartialProducts value = unpack(0);
		value.group = inputValue.group;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Bit#(8) xBits = pack(inputValue.x[lane]);
			Int#(5) low = unpack(zeroExtend(xBits[3:0]));
			Int#(4) high = unpack(xBits[7:4]);
			value.low[lane] = signExtend(low) * signExtend(inputValue.gate[lane]);
			value.high[lane] = signExtend(high) * signExtend(inputValue.gate[lane]);
		end
		gatePartialProductQ.enq(value);
	endrule

	rule process9_2_2;
		let inputValue = gatePartialProductQ.first;
		gatePartialProductQ.deq;
		GateProducts value = unpack(0);
		value.group = inputValue.group;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			Int#(16) high = signExtend(inputValue.high[lane]);
			value.products[lane] = signExtend(inputValue.low[lane]) + (high << 4);
		end
		gateProductQ.enq(value);
	endrule

	rule process9_3;
		let inputValue = gateProductQ.first;
		gateProductQ.deq;
		GateResults value = unpack(0);
		value.group = inputValue.group;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			value.values[lane] = requant(signExtend(inputValue.products[lane]), blockScale(blockId, "ssmY") + blockScale(blockId, "gate"), blockScale(blockId, "gated"));
		end
		gateResultQ.enq(value);
	endrule

	rule process9_4 ( gatingOn && !gateEmitOn );
		let value = gateResultQ.first;
		gateResultQ.deq;
		for ( Integer lane = 0; lane < valueOf(ConvLanes); lane = lane + 1 ) begin
			gatedR[lane][value.group] <= value.values[lane];
		end
		if ( value.group == fromInteger(valueOf(ConvGroups) - 1) ) begin
			gateEmitOn <= True;
		end
	endrule

	rule process9_5 ( gatingOn && gateEmitOn );
		Vector#(40, Int#(8)) result = newVector;
		for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
			Integer lane = channel % valueOf(ConvLanes);
			Integer group = channel / valueOf(ConvLanes);
			result[channel] = gatedR[lane][group];
		end
		outputProjection.put(Token {index: scannedR.index, data: result});
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,outproj_in,%0d,%0d,%0d", blockProfileOutProjFrameCnt, scannedR.index, blockProfileCycleCnt);
			if ( scannedR.index == 15 ) blockProfileOutProjFrameCnt <= blockProfileOutProjFrameCnt + 1;
		end
`endif
		gateEmitOn <= False;
		gatingOn <= False;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 5]
	// Align exponents before residual addition, then round and saturate once.
	//------------------------------------------------------------------------------------
	rule process10;
		let projected <- outputProjection.get;
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,residual_in,%0d,%0d,%0d", blockProfileResidualFrameCnt, projected.index, blockProfileCycleCnt);
			if ( projected.index == 15 ) blockProfileResidualFrameCnt <= blockProfileResidualFrameCnt + 1;
		end
`endif
		let residual = residualQ.first;
		residualQ.deq;
		ResidualSums value = unpack(0);
		value.index = projected.index;
		for ( Integer channel = 0; channel < valueOf(ModelDim); channel = channel + 1 ) begin
			Int#(10) a = signExtend(projected.data[channel]);
			Int#(10) b = signExtend(residual.data[channel]);
			a = a << (outExp - residualAccumulatorExp);
			b = b << (residualInputExp - residualAccumulatorExp);
			value.values[channel] = a + b;
		end
		residualSumQ.enq(value);
	endrule

	rule process11;
		let value = residualSumQ.first;
		residualSumQ.deq;
		Vector#(20, Int#(8)) result = newVector;
		for ( Integer channel = 0; channel < valueOf(ModelDim); channel = channel + 1 ) begin
			result[channel] = requant(signExtend(value.values[channel]), residualAccumulatorExp, blockScale(blockId, "residual"));
		end
		outputQ.enq(Token {index: value.index, data: result});
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,block_ready,%0d,%0d,%0d", blockProfileReadyFrameCnt, value.index, blockProfileCycleCnt);
			if ( value.index == 15 ) blockProfileReadyFrameCnt <= blockProfileReadyFrameCnt + 1;
		end
`endif
	endrule

	method Action put(Token#(20) value) if ( localReset.ready );
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(20)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

endpackage
