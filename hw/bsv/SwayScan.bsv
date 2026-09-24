package SwayScan;

import Assert::*;
import Vector::*;
import FIFO::*;
import RegFile::*;

import SwayTypes::*;
import SwayParameters::*;

typedef TDiv#(StateDim, ScanLanes) ScanParts;
typedef TDiv#(TMul#(InnerDim, StateDim), ScanLanes) ScanGroups;
typedef TMax#(1, TLog#(ScanParts)) ScanPartWidth;
typedef TMax#(1, TLog#(ScanGroups)) ScanAddressWidth;
typedef TLog#(TAdd#(ScanGroups, 1)) ScanCounterWidth;
typedef TAdd#(32, TLog#(ScanLanes)) ScanPartialWidth;
typedef TAdd#(32, TLog#(StateDim)) ScanSumWidth;

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
	Vector#(ScanLanes, Int#(8)) aBar;
	Vector#(ScanLanes, Int#(8)) bBar;
	Vector#(ScanLanes, Int#(8)) c;
	Vector#(ScanLanes, Int#(17)) previous;
	Int#(8) x;
} ScanPrepared deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Vector#(ScanLanes, Int#(24)) current;
	Vector#(ScanLanes, Int#(8)) c;
	Int#(8) x;
} ScanCurrent deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(ScanPartWidth) part;
	Int#(ScanPartialWidth) sum;
	Int#(16) direct;
} ScanPartial deriving (Bits, Eq, FShow);

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
	FIFO#(ScanPrepared) preparedQ <- mkFIFO;
	FIFO#(ScanCurrent) currentQ <- mkFIFO;
	FIFO#(ScanPartial) partialQ <- mkFIFO;
	FIFO#(Token#(InnerDim)) outputQ <- mkFIFO1;

	// One asynchronous-read RAM bank per lane retains its recurrent states.
	// Token zero bypasses old RAM contents; all rows are written before the next token.
	Vector#(ScanLanes, RegFile#(Bit#(ScanAddressWidth), Int#(17))) stateR <- replicateM(mkRegFile(0, fromInteger(valueOf(ScanGroups) - 1)));
	Reg#(ScanToken) inputR <- mkRegU;
	Reg#(Vector#(InnerDim, Int#(8))) outputR <- mkRegU;
	Reg#(Int#(ScanSumWidth)) partialR <- mkReg(0);
	Reg#(Bit#(ScanCounterWidth)) groupCnt <- mkReg(0);
	Reg#(Bool) processOn <- mkReg(False);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Retain one token until all recurrent state writes and output collection complete.
	//------------------------------------------------------------------------------------
	rule process1 ( !processOn );
		inputR <= inputQ.first;
		inputQ.deq;
		groupCnt <= 0;
		processOn <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Form quantized Abar/Bbar for one lane group of a channel per cycle.
	// Index zero selects zero state, independently for each complete radar frame.
	//------------------------------------------------------------------------------------
	rule process2 ( processOn && groupCnt < fromInteger(valueOf(ScanGroups)) );
		Bit#(6) channel = truncate(groupCnt / fromInteger(valueOf(ScanParts)));
		Bit#(ScanPartWidth) part = truncate(groupCnt % fromInteger(valueOf(ScanParts)));
		ScanPrepared value = unpack(0);
		value.channel = channel;
		value.part = part;
		value.x = inputR.x[channel];
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Bit#(3) stateIndex = zeroExtend(part) * fromInteger(valueOf(ScanLanes)) + fromInteger(lane);
			Int#(8) a = 0;
			for ( Integer group = 0; group < valueOf(ScanParts); group = group + 1 ) begin
				if ( part == fromInteger(group) ) begin
					a = stateA(blockId, group * valueOf(ScanLanes) + lane, channel);
				end
			end
			Int#(16) deltaA = signExtend(inputR.delta[channel]) * signExtend(a);
			Int#(8) expInput = requantN(deltaA, deltaExp + aExp, blockScale(blockId, "expInput"));
			Int#(16) deltaB = signExtend(inputR.delta[channel]) * signExtend(inputR.b[stateIndex]);
			value.aBar[lane] = nonlinearLookup(blockId * 3 + 2, expInput);
			value.bBar[lane] = requantN(deltaB, deltaExp + bExp, bBarExp);
			value.c[lane] = inputR.c[stateIndex];
			value.previous[lane] = inputR.index == 0 ? 0 : stateR[lane].sub(truncate(groupCnt));
		end
		preparedQ.enq(value);
		groupCnt <= groupCnt + 1;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Saturate INT24 current state; retain arithmetic >> 7 as signed INT17.
	// The complete INT24 value proceeds to the output product pipeline.
	//------------------------------------------------------------------------------------
	rule process3;
		let previous = preparedQ.first;
		preparedQ.deq;
		ScanCurrent value = unpack(0);
		value.channel = previous.channel;
		value.part = previous.part;
		value.c = previous.c;
		value.x = previous.x;
		Bit#(ScanAddressWidth) row = zeroExtend(previous.channel) * fromInteger(valueOf(ScanParts))
			+ zeroExtend(previous.part);
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(25) recurrent = signExtend(previous.aBar[lane]) * signExtend(previous.previous[lane]);
			Int#(16) inputProduct = signExtend(previous.bBar[lane]) * signExtend(previous.x);
			Int#(26) inputValue = signExtend(inputProduct);
			Int#(26) alignedInput = shiftRoundN(inputValue, bBarExp + xExp, currentExp);
			Int#(26) accumulator = signExtend(recurrent) + alignedInput;
			Int#(24) current = clip24N(accumulator);
			value.current[lane] = current;
			stateR[lane].upd(row, truncate(current >> 7));
		end
		currentQ.enq(value);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 4]
	// Multiply before reducing, using the pre-truncation state for this token.
	//------------------------------------------------------------------------------------
	rule process4;
		let value = currentQ.first;
		currentQ.deq;
		Vector#(ScanLanes, Int#(32)) products = newVector;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			products[lane] = signExtend(value.current[lane]) * signExtend(value.c[lane]);
		end
		Int#(ScanPartialWidth) sum = 0;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			sum = sum + signExtend(products[lane]);
		end
		Int#(16) direct = 0;
		if ( value.part == fromInteger(valueOf(ScanParts) - 1) ) begin
			direct = signExtend(value.x) * signExtend(directD(blockId, value.channel));
		end
		partialQ.enq(ScanPartial {channel: value.channel, part: value.part, sum: sum, direct: direct});
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 5]
	// Combine eight states, align the direct term, and collect 40 output channels.
	//------------------------------------------------------------------------------------
	rule process5 ( processOn );
		let value = partialQ.first;
		partialQ.deq;
		Int#(ScanSumWidth) sum = value.part == 0 ? signExtend(value.sum) : partialR + signExtend(value.sum);
		partialR <= sum;
		if ( value.part == fromInteger(valueOf(ScanParts) - 1) ) begin
			Int#(ScanSumWidth) direct = signExtend(value.direct);
			Int#(ScanSumWidth) accumulator = (sum << stateShift) + (direct << directShift);
			Vector#(InnerDim, Int#(8)) result = outputR;
			result[value.channel] = requantN(accumulator, accumulatorExp, blockScale(blockId, "ssmY"));
			outputR <= result;
			if ( value.channel == fromInteger(valueOf(InnerDim) - 1) ) begin
				outputQ.enq(Token {index: inputR.index, data: result});
				processOn <= False;
			end
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
