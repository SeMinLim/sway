package SwayScan;

import Assert::*;
import Vector::*;
import FIFO::*;
import RegFile::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayMultiply::*;

typedef TDiv#(StateDim, ScanLanes) ScanParts;
typedef TDiv#(TMul#(InnerDim, StateDim), ScanLanes) ScanGroups;
typedef TMax#(1, TLog#(ScanParts)) ScanPartWidth;
typedef TMax#(1, TLog#(ScanGroups)) ScanAddressWidth;
typedef TLog#(TAdd#(ScanGroups, 1)) ScanCounterWidth;
typedef TAdd#(32, TLog#(ScanLanes)) ScanPartialWidth;
typedef TAdd#(32, TLog#(StateDim)) ScanSumWidth;
typedef TSub#(ScanSumWidth, 16) ScanSumHighWidth;

typedef struct {
	Bit#(4) index;
	Vector#(InnerDim, Int#(8)) x;
	Vector#(InnerDim, Int#(8)) delta;
	Vector#(StateDim, Int#(8)) b;
	Vector#(StateDim, Int#(8)) c;
} ScanToken deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(8)) a;
	Vector#(ScanLanes, Int#(8)) b;
	Vector#(ScanLanes, Int#(8)) c;
	Vector#(ScanLanes, Int#(17)) previous;
	Int#(8) delta;
	Int#(8) x;
	Int#(8) d;
} ScanSelected deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(16)) deltaA;
	Vector#(ScanLanes, Int#(16)) deltaB;
	Vector#(ScanLanes, Int#(8)) c;
	Vector#(ScanLanes, Int#(17)) previous;
	Int#(8) x;
	Int#(16) direct;
} ScanDeltaProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(8)) expInput;
	Vector#(ScanLanes, Int#(8)) bBar;
	Vector#(ScanLanes, Int#(8)) c;
	Vector#(ScanLanes, Int#(17)) previous;
	Int#(8) x;
	Int#(16) direct;
} ScanQuantized deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(8)) aBar;
	Vector#(ScanLanes, Int#(8)) bBar;
	Vector#(ScanLanes, Int#(8)) c;
	Vector#(ScanLanes, Int#(17)) previous;
	Int#(8) x;
	Int#(16) direct;
} ScanPrepared deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, UInt#(8)) aMagnitude;
	Vector#(ScanLanes, UInt#(17)) stateMagnitude;
	Vector#(ScanLanes, Bool) negative;
	Vector#(ScanLanes, Int#(8)) bBar;
	Int#(8) x;
	Vector#(ScanLanes, Int#(8)) c;
	Int#(16) direct;
} ScanRecurrenceOperands deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, UInt#(25)) magnitude;
	Vector#(ScanLanes, Bool) negative;
	Vector#(ScanLanes, Int#(16)) inputProduct;
	Vector#(ScanLanes, Int#(8)) c;
	Int#(16) direct;
} ScanRecurrenceProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(25)) recurrent;
	Vector#(ScanLanes, Int#(16)) inputProduct;
	Vector#(ScanLanes, Int#(8)) c;
	Int#(16) direct;
} ScanRecurrence deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(26)) accumulator;
	Vector#(ScanLanes, Int#(8)) c;
	Int#(16) direct;
} ScanAccumulator deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(24)) current;
	Vector#(ScanLanes, Int#(8)) c;
	Int#(16) direct;
} ScanCurrent deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, UInt#(16)) low;
	Vector#(ScanLanes, UInt#(8)) highMagnitude;
	Vector#(ScanLanes, UInt#(8)) cMagnitude;
	Vector#(ScanLanes, Bool) lowNegative;
	Vector#(ScanLanes, Bool) highNegative;
	Int#(16) direct;
} ScanProductOperands deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, UInt#(25)) lowMagnitude;
	Vector#(ScanLanes, UInt#(16)) highMagnitude;
	Vector#(ScanLanes, Bool) lowNegative;
	Vector#(ScanLanes, Bool) highNegative;
	Int#(16) direct;
} ScanProductMagnitudes deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(25)) low;
	Vector#(ScanLanes, Int#(16)) high;
	Int#(16) direct;
} ScanSplitProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(32)) products;
	Int#(16) direct;
} ScanProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Int#(ScanPartialWidth) sum;
	Int#(16) direct;
} ScanPartial deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Int#(ScanSumWidth) sum;
	Int#(16) direct;
} ScanSum deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(16) low;
	Bit#(ScanSumHighWidth) high;
	Bit#(1) carry;
} ScanOutputParts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Int#(ScanSumWidth) accumulator;
} ScanOutput deriving (Bits, Eq, FShow);

interface ScanIfc;
	//------------------------------------------------------------------------------------
	// Interface
	//------------------------------------------------------------------------------------
	method Action put(ScanToken value);
	method ActionValue#(Token#(InnerDim)) get;
endinterface

module mkSwayScan#(Integer blockId)(ScanIfc);
	Integer xExp = blockScale(blockId, "x");
	Integer deltaExp = blockScale(blockId, "delta");
	Integer aExp = blockScale(blockId, "A");
	Integer bExp = blockScale(blockId, "B");
	Integer bBarExp = blockScale(blockId, "Bbar");
	Integer cExp = blockScale(blockId, "C");
	Integer dExp = blockScale(blockId, "D");
	Integer currentExp = blockScale(blockId, "state") - 7;
	Integer stateOutputExp = currentExp + cExp;
	Integer directExp = xExp + dExp;
	Integer accumulatorExp = stateOutputExp < directExp ? stateOutputExp : directExp;
	Integer inputShift = max(0, bBarExp + xExp - currentExp);
	Integer stateShift = stateOutputExp - accumulatorExp;
	Integer directShift = directExp - accumulatorExp;

	staticAssert(valueOf(StateDim) % valueOf(ScanLanes) == 0,
		"Scan lanes must divide the recurrent state dimension");
	// INT8 x INT17 plus the aligned INT8 x INT8 drive fits signed INT26.
	staticAssert(128 * (2 ** 16) + (128 * 128) * (2 ** inputShift) < 2 ** 25,
		"Recurrent state alignment exceeds the INT26 accumulator");
	// Eight INT24 x INT8 products and the aligned direct term fit signed INT35.
	staticAssert(valueOf(StateDim) * (2 ** 23) * 128 * (2 ** stateShift)
		+ (128 * 128) * (2 ** directShift) < 2 ** (valueOf(ScanSumWidth) - 1),
		"SSM output alignment exceeds the state-sum accumulator");

	FIFO#(ScanToken) inputQ <- mkFIFO1;
	FIFO#(ScanSelected) selectedQ <- mkFIFO1;
	FIFO#(ScanDeltaProducts) deltaProductsQ <- mkFIFO1;
	FIFO#(ScanQuantized) quantizedQ <- mkFIFO;
	FIFO#(ScanPrepared) preparedQ <- mkFIFO;
	FIFO#(ScanRecurrenceOperands) recurrenceOperandsQ <- mkFIFO1;
	FIFO#(ScanRecurrenceProducts) recurrenceProductsQ <- mkFIFO1;
	FIFO#(ScanRecurrence) recurrenceQ <- mkFIFO1;
	FIFO#(ScanAccumulator) accumulatorQ <- mkFIFO;
	FIFO#(ScanCurrent) currentQ <- mkFIFO;
	FIFO#(ScanProductOperands) productOperandsQ <- mkFIFO1;
	FIFO#(ScanProductMagnitudes) productMagnitudesQ <- mkFIFO1;
	FIFO#(ScanSplitProducts) splitProductsQ <- mkFIFO1;
	FIFO#(ScanProducts) productsQ <- mkFIFO;
	FIFO#(ScanPartial) partialQ <- mkFIFO;
	FIFO#(ScanSum) sumQ <- mkLFIFO;
	FIFO#(ScanOutputParts) outputPartsQ <- mkFIFO1;
	FIFO#(ScanOutput) resultQ <- mkFIFO1;
	FIFO#(Tuple2#(Bit#(6), Int#(8))) channelResultQ <- mkFIFO1;
	FIFO#(Token#(InnerDim)) outputQ <- mkFIFO1;

	// Metadata FIFO credits match each registered multiplier's eight result slots.
	FIFO#(ScanDeltaProducts) deltaMetadataQ <- mkSizedFIFO(8);
	FIFO#(ScanRecurrenceProducts) recurrenceMetadataQ <- mkSizedFIFO(8);
	FIFO#(ScanProductMagnitudes) productMetadataQ <- mkSizedFIFO(8);
	Vector#(ScanLanes, MultiplyIfc) deltaAMultipliers <- replicateM(mkMultiply);
	Vector#(ScanLanes, MultiplyIfc) deltaBMultipliers <- replicateM(mkMultiply);
	MultiplyIfc directMultiplier <- mkMultiply;
	Vector#(ScanLanes, MultiplyIfc) stateMultipliers <- replicateM(mkMultiply);
	Vector#(ScanLanes, MultiplyIfc) inputMultipliers <- replicateM(mkMultiply);
	Vector#(ScanLanes, MultiplyIfc) lowMultipliers <- replicateM(mkMultiply);
	Vector#(ScanLanes, MultiplyIfc) highMultipliers <- replicateM(mkMultiply);

	// Each row is issued once per token. Token zero bypasses uninitialized state.
	Vector#(ScanLanes, RegFile#(Bit#(ScanAddressWidth), Int#(17))) stateR <- replicateM(mkRegFile(0, fromInteger(valueOf(ScanGroups) - 1)));
	Reg#(Vector#(InnerDim, Int#(8))) xR <- mkRegU;
	Reg#(Vector#(InnerDim, Int#(8))) deltaR <- mkRegU;
	Reg#(Vector#(StateDim, Int#(8))) bR <- mkRegU;
	Reg#(Vector#(StateDim, Int#(8))) cR <- mkRegU;
	Reg#(Bit#(4)) indexR <- mkRegU;
	Vector#(InnerDim, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	Reg#(Int#(ScanSumWidth)) partialR <- mkRegU;
	Reg#(Bit#(ScanCounterWidth)) groupCnt <- mkReg(0);
	Reg#(Bool) processOn <- mkReg(False);
	Reg#(Bool) issueOn <- mkReg(False);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Retain one token until all recurrent state writes and output collection complete.
	//------------------------------------------------------------------------------------
	rule process1 ( !processOn );
		let value = inputQ.first;
		inputQ.deq;
		xR <= value.x;
		deltaR <= value.delta;
		bR <= value.b;
		cR <= value.c;
		indexR <= value.index;
		groupCnt <= 0;
		processOn <= True;
		issueOn <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Register selection, delta products, requantization, and nonlinear lookup separately.
	//------------------------------------------------------------------------------------
	rule process2 ( processOn && issueOn );
		Bit#(6) channel = truncate(groupCnt / fromInteger(valueOf(ScanParts)));
		Bit#(ScanPartWidth) part = truncate(groupCnt % fromInteger(valueOf(ScanParts)));
		ScanSelected value = unpack(0);
		value.channel = channel;
		value.part = part;
		value.x = xR[0];
		value.delta = deltaR[0];
		value.d = directD(blockId, channel);
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Bit#(3) stateIndex = zeroExtend(part) * fromInteger(valueOf(ScanLanes)) + fromInteger(lane);
			for ( Integer group = 0; group < valueOf(ScanParts); group = group + 1 ) begin
				if ( part == fromInteger(group) ) begin
					value.a[lane] = stateA(blockId, group * valueOf(ScanLanes) + lane, channel);
				end
			end
			value.b[lane] = bR[stateIndex];
			value.c[lane] = cR[stateIndex];
			value.previous[lane] = indexR == 0 ? 0 : stateR[lane].sub(truncate(groupCnt));
		end
		selectedQ.enq(value);
		// Consume one fixed channel after its final accepted state group.
		if ( part == fromInteger(valueOf(ScanParts) - 1) ) begin
			Vector#(InnerDim, Int#(8)) nextX = replicate(0);
			Vector#(InnerDim, Int#(8)) nextDelta = replicate(0);
			for ( Integer channelIndex = 0; channelIndex < valueOf(InnerDim) - 1; channelIndex = channelIndex + 1 ) begin
				nextX[channelIndex] = xR[channelIndex + 1];
				nextDelta[channelIndex] = deltaR[channelIndex + 1];
			end
			xR <= nextX;
			deltaR <= nextDelta;
		end
		if ( groupCnt == fromInteger(valueOf(ScanGroups) - 1) ) begin
			issueOn <= False;
		end else begin
			groupCnt <= groupCnt + 1;
		end
	endrule

	rule process2_2;
		let previous = selectedQ.first;
		selectedQ.deq;
		ScanDeltaProducts value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.previous = previous.previous;
		value.x = previous.x;
		directMultiplier.put(signExtend(previous.x), signExtend(previous.d));
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			deltaAMultipliers[lane].put(signExtend(previous.delta), signExtend(previous.a[lane]));
			deltaBMultipliers[lane].put(signExtend(previous.delta), signExtend(previous.b[lane]));
		end
		deltaMetadataQ.enq(value);
	endrule

	rule process2_2_2;
		ScanDeltaProducts value = deltaMetadataQ.first;
		deltaMetadataQ.deq;
		let direct <- directMultiplier.get;
		value.direct = truncate(direct);
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			let deltaA <- deltaAMultipliers[lane].get;
			let deltaB <- deltaBMultipliers[lane].get;
			value.deltaA[lane] = truncate(deltaA);
			value.deltaB[lane] = truncate(deltaB);
		end
		deltaProductsQ.enq(value);
	endrule

	rule process2_3;
		let previous = deltaProductsQ.first;
		deltaProductsQ.deq;
		ScanQuantized value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.previous = previous.previous;
		value.x = previous.x;
		value.direct = previous.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			value.expInput[lane] = requantN(previous.deltaA[lane], deltaExp + aExp, blockScale(blockId, "expInput"));
			value.bBar[lane] = requantN(previous.deltaB[lane], deltaExp + bExp, bBarExp);
		end
		quantizedQ.enq(value);
	endrule

	rule process2_4;
		let previous = quantizedQ.first;
		quantizedQ.deq;
		ScanPrepared value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.bBar = previous.bBar;
		value.c = previous.c;
		value.previous = previous.previous;
		value.x = previous.x;
		value.direct = previous.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			value.aBar[lane] = nonlinearLookup(blockId * 2 + 1, previous.expInput[lane]);
		end
		preparedQ.enq(value);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Register recurrence products and their aligned sum before saturation and RAM writes.
	// Retain arithmetic >> 7 as INT17; forward the complete INT24 current state.
	//------------------------------------------------------------------------------------
	rule process3;
		let previous = preparedQ.first;
		preparedQ.deq;
		ScanRecurrenceOperands value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.direct = previous.direct;
		value.bBar = previous.bBar;
		value.x = previous.x;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(8) a = previous.aBar[lane];
			Int#(17) state = previous.previous[lane];
			// Interpret minimum-negative magnitudes as unsigned at their original widths.
			value.aMagnitude[lane] = unpack(pack(a < 0 ? -a : a));
			value.stateMagnitude[lane] = unpack(pack(state < 0 ? -state : state));
			value.negative[lane] = (a < 0) != (state < 0);
		end
		recurrenceOperandsQ.enq(value);
	endrule

	// Unsigned operands fit one DSP; raw-product storage precedes sign restoration.
	rule process3_1;
		let previous = recurrenceOperandsQ.first;
		recurrenceOperandsQ.deq;
		ScanRecurrenceProducts value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.direct = previous.direct;
		value.negative = previous.negative;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(18) a = unpack(zeroExtend(pack(previous.aMagnitude[lane])));
			Int#(18) state = unpack(zeroExtend(pack(previous.stateMagnitude[lane])));
			stateMultipliers[lane].put(a, state);
			inputMultipliers[lane].put(signExtend(previous.bBar[lane]), signExtend(previous.x));
		end
		recurrenceMetadataQ.enq(value);
	endrule

	rule process3_1_1;
		ScanRecurrenceProducts value = recurrenceMetadataQ.first;
		recurrenceMetadataQ.deq;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			let magnitude <- stateMultipliers[lane].get;
			let inputProduct <- inputMultipliers[lane].get;
			value.magnitude[lane] = unpack(truncate(pack(magnitude)));
			value.inputProduct[lane] = truncate(inputProduct);
		end
		recurrenceProductsQ.enq(value);
	endrule

	rule process3_1_2;
		let previous = recurrenceProductsQ.first;
		recurrenceProductsQ.deq;
		ScanRecurrence value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.direct = previous.direct;
		value.inputProduct = previous.inputProduct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(25) magnitude = unpack(pack(previous.magnitude[lane]));
			value.recurrent[lane] = previous.negative[lane] ? -magnitude : magnitude;
		end
		recurrenceQ.enq(value);
	endrule

	rule process3_2;
		let previous = recurrenceQ.first;
		recurrenceQ.deq;
		ScanAccumulator value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.direct = previous.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(26) inputValue = signExtend(previous.inputProduct[lane]);
			Int#(26) alignedInput = shiftRoundN(inputValue, bBarExp + xExp, currentExp);
			value.accumulator[lane] = signExtend(previous.recurrent[lane]) + alignedInput;
		end
		accumulatorQ.enq(value);
	endrule

	rule process3_3;
		let previous = accumulatorQ.first;
		accumulatorQ.deq;
		ScanCurrent value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.direct = previous.direct;
		Bit#(ScanAddressWidth) row = zeroExtend(previous.channel) * fromInteger(valueOf(ScanParts))
			+ zeroExtend(previous.part);
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(24) current = clip24N(previous.accumulator[lane]);
			value.current[lane] = current;
			stateR[lane].upd(row, truncate(current >> 7));
		end
		currentQ.enq(value);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 4]
	// Split INT24 x INT8 into two DSP-sized products and register before recombination.
	// The low 16 bits are unsigned; the high eight bits retain the original sign.
	//------------------------------------------------------------------------------------
	rule process4;
		let previous = currentQ.first;
		currentQ.deq;
		ScanProductOperands value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.direct = previous.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Bit#(24) currentBits = pack(previous.current[lane]);
			Int#(8) high = unpack(truncate(currentBits >> 16));
			Int#(8) c = previous.c[lane];
			value.low[lane] = unpack(truncate(currentBits));
			value.highMagnitude[lane] = unpack(pack(high < 0 ? -high : high));
			value.cMagnitude[lane] = unpack(pack(c < 0 ? -c : c));
			value.lowNegative[lane] = c < 0;
			value.highNegative[lane] = (high < 0) != (c < 0);
		end
		productOperandsQ.enq(value);
	endrule

	rule process4_1;
		let previous = productOperandsQ.first;
		productOperandsQ.deq;
		ScanProductMagnitudes value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.direct = previous.direct;
		value.lowNegative = previous.lowNegative;
		value.highNegative = previous.highNegative;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(18) low = unpack(zeroExtend(pack(previous.low[lane])));
			Int#(18) high = unpack(zeroExtend(pack(previous.highMagnitude[lane])));
			Int#(18) c = unpack(zeroExtend(pack(previous.cMagnitude[lane])));
			lowMultipliers[lane].put(low, c);
			highMultipliers[lane].put(high, c);
		end
		productMetadataQ.enq(value);
	endrule

	rule process4_1_1;
		ScanProductMagnitudes value = productMetadataQ.first;
		productMetadataQ.deq;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			let low <- lowMultipliers[lane].get;
			let high <- highMultipliers[lane].get;
			value.lowMagnitude[lane] = unpack(truncate(pack(low)));
			value.highMagnitude[lane] = unpack(truncate(pack(high)));
		end
		productMagnitudesQ.enq(value);
	endrule

	rule process4_1_2;
		let previous = productMagnitudesQ.first;
		productMagnitudesQ.deq;
		ScanSplitProducts value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.direct = previous.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(25) low = unpack(pack(previous.lowMagnitude[lane]));
			Int#(16) high = unpack(pack(previous.highMagnitude[lane]));
			value.low[lane] = previous.lowNegative[lane] ? -low : low;
			value.high[lane] = previous.highNegative[lane] ? -high : high;
		end
		splitProductsQ.enq(value);
	endrule

	rule process4_2;
		let previous = splitProductsQ.first;
		splitProductsQ.deq;
		ScanProducts value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.direct = previous.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(32) low = signExtend(previous.low[lane]);
			Int#(32) high = signExtend(previous.high[lane]);
			value.products[lane] = low + (high << 16);
		end
		productsQ.enq(value);
	endrule

	rule process4_3;
		let value = productsQ.first;
		productsQ.deq;
		Int#(ScanPartialWidth) sum = 0;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			sum = sum + signExtend(value.products[lane]);
		end
		partialQ.enq(ScanPartial {channel: value.channel, part: value.part, sum: sum, direct: value.direct});
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 5]
	// Accumulate each channel, add its aligned direct term, then requantize and collect.
	// FIFO order and final collection keep the next token behind every state write.
	//------------------------------------------------------------------------------------
	rule process5;
		let value = partialQ.first;
		partialQ.deq;
		Int#(ScanSumWidth) sum = value.part == 0 ? signExtend(value.sum) : partialR + signExtend(value.sum);
		partialR <= sum;
		if ( value.part == fromInteger(valueOf(ScanParts) - 1) ) begin
			sumQ.enq(ScanSum {channel: value.channel, sum: sum, direct: value.direct});
		end
	endrule

	rule process5_2;
		let value = sumQ.first;
		sumQ.deq;
		Int#(ScanSumWidth) direct = signExtend(value.direct);
		Bit#(ScanSumWidth) stateBits = pack(value.sum << stateShift);
		Bit#(ScanSumWidth) directBits = pack(direct << directShift);
		Bit#(16) stateLow = truncate(stateBits);
		Bit#(16) directLow = truncate(directBits);
		Bit#(17) low = zeroExtend(stateLow) + zeroExtend(directLow);
		Bit#(ScanSumHighWidth) stateHigh = truncate(stateBits >> 16);
		Bit#(ScanSumHighWidth) directHigh = truncate(directBits >> 16);
		outputPartsQ.enq(ScanOutputParts {
			channel: value.channel, low: truncate(low),
			high: stateHigh + directHigh, carry: low[16]
		});
	endrule

	rule process5_2_2;
		let value = outputPartsQ.first;
		outputPartsQ.deq;
		Bit#(ScanSumHighWidth) high = value.high + zeroExtend(value.carry);
		Int#(ScanSumWidth) accumulator = unpack({high, value.low});
		resultQ.enq(ScanOutput {channel: value.channel, accumulator: accumulator});
	endrule

	rule process5_3;
		let value = resultQ.first;
		resultQ.deq;
		Int#(8) channelResult = requantN(value.accumulator, accumulatorExp, blockScale(blockId, "ssmY"));
		channelResultQ.enq(tuple2(value.channel, channelResult));
	endrule

	rule process5_4 ( processOn );
		let value = channelResultQ.first;
		channelResultQ.deq;
		let channelIndex = tpl_1(value);
		let channelResult = tpl_2(value);
		Vector#(InnerDim, Int#(8)) result = newVector;
		for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
			if ( channelIndex == fromInteger(channel) ) begin
				outputR[channel] <= channelResult;
			end
			// Forward the last channel before its register write takes effect.
			if ( channel == valueOf(InnerDim) - 1 ) begin
				result[channel] = channelResult;
			end else begin
				result[channel] = outputR[channel];
			end
		end
		if ( channelIndex == fromInteger(valueOf(InnerDim) - 1) ) begin
			outputQ.enq(Token {index: indexR, data: result});
			processOn <= False;
		end
	endrule

	method Action put(ScanToken value);
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(InnerDim)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

endpackage
