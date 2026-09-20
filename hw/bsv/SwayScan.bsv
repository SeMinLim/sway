package SwayScan;

import Vector::*;
import FIFO::*;
import RegFile::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayReset::*;
import SwayLookup::*;

typedef 2 ScanLanes;
typedef 160 ScanGroups;

// Constant bitplanes lower to muxes rather than an inferred synchronous ROM.
// Keep the address FIFO register separate from coefficient lookup and its enable.
function Int#(8) scanStateCoefficient(Integer blockId, Integer stateIndex, Bit#(6) channel);
	Vector#(8, Bit#(64)) planes = replicate(0);
	for ( Integer row = 0; row < 40; row = row + 1 ) begin
		Bit#(8) coefficient = pack(stateA(blockId, stateIndex, fromInteger(row)));
		for ( Integer bitIndex = 0; bitIndex < 8; bitIndex = bitIndex + 1 ) begin
			planes[bitIndex][row] = coefficient[bitIndex];
		end
	end
	Bit#(8) result = 0;
	for ( Integer bitIndex = 0; bitIndex < 8; bitIndex = bitIndex + 1 ) begin
		result[bitIndex] = truncate(planes[bitIndex] >> channel);
	end
	return unpack(result);
endfunction

function Int#(8) scanDirectCoefficient(Integer blockId, Bit#(6) channel);
	Vector#(8, Bit#(64)) planes = replicate(0);
	for ( Integer row = 0; row < 40; row = row + 1 ) begin
		Bit#(8) coefficient = pack(directD(blockId, fromInteger(row)));
		for ( Integer bitIndex = 0; bitIndex < 8; bitIndex = bitIndex + 1 ) begin
			planes[bitIndex][row] = coefficient[bitIndex];
		end
	end
	Bit#(8) result = 0;
	for ( Integer bitIndex = 0; bitIndex < 8; bitIndex = bitIndex + 1 ) begin
		result[bitIndex] = truncate(planes[bitIndex] >> channel);
	end
	return unpack(result);
endfunction

typedef struct {
	Bit#(8) group;
	Bool zeroState;
} ScanAddress deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(4) index;
	Vector#(40, Int#(8)) x;
	Vector#(40, Int#(8)) delta;
	Vector#(8, Int#(8)) b;
	Vector#(8, Int#(8)) c;
} ScanToken deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(2) part;
	Int#(8) x;
	Int#(8) direct;
	Vector#(ScanLanes, Int#(8)) c;
} ScanMetadata deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Int#(8) delta;
	Vector#(ScanLanes, Int#(8)) a;
	Vector#(ScanLanes, Int#(8)) b;
	Vector#(ScanLanes, Int#(17)) previous;
} ScanOperands deriving (Bits, Eq, FShow);

typedef struct {
	ScanOperands operands;
	Bit#(3) bank;
	Vector#(5, Int#(8)) x;
	Vector#(5, Int#(8)) delta;
} ScanOperandCandidates deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Vector#(2, Int#(13))) deltaA;
	Vector#(ScanLanes, Vector#(2, Int#(13))) deltaB;
	Vector#(ScanLanes, Int#(17)) previous;
} ScanDeltaPartial deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Int#(16)) deltaA;
	Vector#(ScanLanes, Int#(16)) deltaB;
	Vector#(ScanLanes, Int#(17)) previous;
} ScanDeltaProducts deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Int#(8)) a;
	Vector#(ScanLanes, Int#(8)) bBar;
	Vector#(ScanLanes, Int#(17)) previous;
} ScanPrepared deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Bit#(4)) high;
	Vector#(ScanLanes, Vector#(16, Int#(8))) candidates;
	Vector#(ScanLanes, Int#(8)) bBar;
	Vector#(ScanLanes, Int#(17)) previous;
} ScanLookup deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Vector#(2, Int#(14))) low;
	Vector#(ScanLanes, Vector#(2, Int#(14))) high;
	Vector#(ScanLanes, Vector#(2, Int#(13))) drive;
} ScanUpdateNibbles deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Int#(17)) low;
	Vector#(ScanLanes, Int#(17)) high;
	Vector#(ScanLanes, Int#(16)) drive;
} ScanUpdatePartial deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Int#(25)) recurrent;
	Vector#(ScanLanes, Int#(16)) drive;
} ScanUpdateProducts deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Int#(28)) current;
} ScanUnclipped deriving (Bits, Eq, FShow);

typedef struct {
	ScanMetadata meta;
	Vector#(ScanLanes, Int#(24)) current;
} ScanCurrent deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(2) part;
	Vector#(ScanLanes, Vector#(3, Vector#(4, Int#(12)))) products;
	Vector#(2, Int#(13)) direct;
} ScanOutputQuarters deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(2) part;
	Vector#(ScanLanes, Vector#(3, Vector#(2, Int#(14)))) products;
	Vector#(2, Int#(13)) direct;
} ScanOutputNibbles deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(2) part;
	Vector#(ScanLanes, Int#(17)) low;
	Vector#(ScanLanes, Int#(17)) middle;
	Vector#(ScanLanes, Int#(16)) high;
	Int#(16) direct;
} ScanOutputProducts deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(2) part;
	Vector#(ScanLanes, Int#(25)) low;
	Vector#(ScanLanes, Int#(16)) high;
	Int#(16) direct;
} ScanOutputLower deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(2) part;
	Vector#(ScanLanes, Int#(32)) products;
	Int#(16) direct;
} ScanOutputCombined deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Bit#(2) part;
	Int#(33) sum;
	Int#(16) direct;
} ScanPartial deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Int#(35) sum;
	Int#(16) direct;
} ScanTotal deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Int#(36) sum;
} ScanAligned deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) channel;
	Int#(8) value;
} ScanResult deriving (Bits, Eq, FShow);

interface ScanIfc;
	method Action put(ScanToken value);
	method ActionValue#(Token#(40)) get;
endinterface

module mkSwayScan#(Integer blockId)(ScanIfc);
	LocalResetIfc localReset <- mkSwayLocalReset;
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
	Integer driveShift = bBarExp + xExp - currentExp;
	// These fixed checkpoint exponents bound the drive to 27 bits and output accumulator to 36 bits.
	if ( driveShift < 0 || driveShift > 11 || stateOutputExp != accumulatorExp || directExp - accumulatorExp > 15 ) begin
		error("SwayScan fixed-point bounds do not cover this quantization profile");
	end

	FIFO#(ScanToken) inputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanAddress) addressQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanOperandCandidates) operandCandidateQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanOperands) operandQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanDeltaPartial) deltaPartialQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanDeltaProducts) deltaProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanPrepared) argumentQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanLookup) lookupQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanPrepared) preparedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanUpdateNibbles) updateNibbleQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanUpdatePartial) updatePartialQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanUpdateProducts) updateProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanUnclipped) unclippedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanCurrent) currentQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanOutputQuarters) outputQuarterQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanOutputNibbles) outputNibbleQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanOutputProducts) outputProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanOutputLower) outputLowerQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanOutputCombined) combinedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanPartial) partialQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanTotal) totalQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanAligned) alignedQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(ScanResult) resultQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Token#(40)) outputQ <- mkFIFO1(reset_by localReset.rst);

	// Two asynchronous-read RAM banks retain all 40 x 8 states. Token zero bypasses RAM.
	Vector#(ScanLanes, RegFile#(Bit#(8), Int#(17))) stateR <- replicateM(mkRegFile(0, fromInteger(valueOf(ScanGroups) - 1)));
	Reg#(ScanToken) inputR <- mkRegU;
	Vector#(40, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	// Part zero seeds the accumulator; process1 seeds the counter before use.
	Reg#(Int#(35)) partialR <- mkRegU;
	Reg#(Bit#(8)) groupCnt <- mkRegU;
	Reg#(Bool) processOn <- mkReg(False, reset_by localReset.rst);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Keep the token stable until every output and state write has drained.
	//------------------------------------------------------------------------------------
	rule process1 ( !processOn );
		inputR <= inputQ.first;
		inputQ.deq;
		groupCnt <= 0;
		processOn <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Register operand selection, constant lookups and asynchronous state RAM reads.
	//------------------------------------------------------------------------------------
	rule process2_1 ( processOn && groupCnt < fromInteger(valueOf(ScanGroups)) );
		addressQ.enq(ScanAddress {group: groupCnt, zeroState: inputR.index == 0});
		groupCnt <= groupCnt + 1;
	endrule

	rule process2_2 ( processOn );
		let address = addressQ.first;
		addressQ.deq;
		Bit#(6) channel = truncate(address.group >> 2);
		Bit#(2) part = truncate(address.group);
		ScanOperands value = unpack(0);
		value.meta.channel = channel;
		value.meta.part = part;
		value.meta.direct = scanDirectCoefficient(blockId, channel);
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Bit#(3) stateIndex = {part, fromInteger(lane)};
			case ( part )
				0: value.a[lane] = scanStateCoefficient(blockId, lane, channel);
				1: value.a[lane] = scanStateCoefficient(blockId, lane + 2, channel);
				2: value.a[lane] = scanStateCoefficient(blockId, lane + 4, channel);
				3: value.a[lane] = scanStateCoefficient(blockId, lane + 6, channel);
			endcase
			value.b[lane] = inputR.b[stateIndex];
			value.meta.c[lane] = inputR.c[stateIndex];
			value.previous[lane] = address.zeroState ? 0 : stateR[lane].sub(address.group);
		end
		ScanOperandCandidates candidates = unpack(0);
		candidates.operands = value;
		candidates.bank = channel[5:3];
		Bit#(3) offset = channel[2:0];
		for ( Integer bank = 0; bank < 5; bank = bank + 1 ) begin
			Vector#(8, Int#(8)) xBank = newVector;
			Vector#(8, Int#(8)) deltaBank = newVector;
			for ( Integer row = 0; row < 8; row = row + 1 ) begin
				xBank[row] = inputR.x[bank * 8 + row];
				deltaBank[row] = inputR.delta[bank * 8 + row];
			end
			candidates.x[bank] = xBank[offset];
			candidates.delta[bank] = deltaBank[offset];
		end
		operandCandidateQ.enq(candidates);
	endrule

	// Finish the 40-channel selection with a registered five-way bank mux.
	rule process2_3;
		let candidates = operandCandidateQ.first;
		operandCandidateQ.deq;
		ScanOperands value = candidates.operands;
		value.meta.x = candidates.x[candidates.bank];
		value.delta = candidates.delta[candidates.bank];
		operandQ.enq(value);
	endrule

	rule process3_1;
		let inputValue = operandQ.first;
		operandQ.deq;
		ScanDeltaPartial value = unpack(0);
		value.meta = inputValue.meta;
		value.previous = inputValue.previous;
		Bit#(8) deltaBits = pack(inputValue.delta);
		Int#(5) low = unpack(zeroExtend(deltaBits[3:0]));
		Int#(4) high = unpack(deltaBits[7:4]);
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			value.deltaA[lane][0] = signExtend(low) * signExtend(inputValue.a[lane]);
			value.deltaA[lane][1] = signExtend(high) * signExtend(inputValue.a[lane]);
			value.deltaB[lane][0] = signExtend(low) * signExtend(inputValue.b[lane]);
			value.deltaB[lane][1] = signExtend(high) * signExtend(inputValue.b[lane]);
		end
		deltaPartialQ.enq(value);
	endrule

	rule process3_2;
		let inputValue = deltaPartialQ.first;
		deltaPartialQ.deq;
		ScanDeltaProducts value = unpack(0);
		value.meta = inputValue.meta;
		value.previous = inputValue.previous;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(16) highA = signExtend(inputValue.deltaA[lane][1]);
			Int#(16) highB = signExtend(inputValue.deltaB[lane][1]);
			value.deltaA[lane] = signExtend(inputValue.deltaA[lane][0]) + (highA << 4);
			value.deltaB[lane] = signExtend(inputValue.deltaB[lane][0]) + (highB << 4);
		end
		deltaProductQ.enq(value);
	endrule

	rule process4;
		let inputValue = deltaProductQ.first;
		deltaProductQ.deq;
		ScanPrepared value = unpack(0);
		value.meta = inputValue.meta;
		value.previous = inputValue.previous;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			value.a[lane] = requant(signExtend(inputValue.deltaA[lane]), deltaExp + aExp, blockScale(blockId, "expInput"));
			value.bBar[lane] = requant(signExtend(inputValue.deltaB[lane]), deltaExp + bExp, bBarExp);
		end
		argumentQ.enq(value);
	endrule

	rule process5_1;
		let inputValue = argumentQ.first;
		argumentQ.deq;
		ScanLookup value = unpack(0);
		value.meta = inputValue.meta;
		value.bBar = inputValue.bBar;
		value.previous = inputValue.previous;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Bit#(8) argument = pack(inputValue.a[lane]);
			value.high[lane] = argument[7:4];
			value.candidates[lane] = nonlinearCandidates(blockId * 3 + 2, argument[3:0]);
		end
		lookupQ.enq(value);
	endrule

	rule process5_2;
		let inputValue = lookupQ.first;
		lookupQ.deq;
		ScanPrepared value = unpack(0);
		value.meta = inputValue.meta;
		value.bBar = inputValue.bBar;
		value.previous = inputValue.previous;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			value.a[lane] = inputValue.candidates[lane][inputValue.high[lane]];
		end
		preparedQ.enq(value);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Register each multiply, bounded addition and saturation separately.
	// Preserve INT24 for the output while arithmetic >> 7 writes INT17 recurrence.
	//------------------------------------------------------------------------------------
	rule process6_1;
		let inputValue = preparedQ.first;
		preparedQ.deq;
		ScanUpdateNibbles value = unpack(0);
		value.meta = inputValue.meta;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Bit#(17) previousBits = pack(inputValue.previous[lane]);
			Int#(9) low = unpack(zeroExtend(previousBits[7:0]));
			Int#(9) high = unpack(previousBits[16:8]);
			Bit#(8) aBits = pack(inputValue.a[lane]);
			Int#(5) aLow = unpack(zeroExtend(aBits[3:0]));
			Int#(4) aHigh = unpack(aBits[7:4]);
			value.low[lane][0] = signExtend(low) * signExtend(aLow);
			value.low[lane][1] = signExtend(low) * signExtend(aHigh);
			value.high[lane][0] = signExtend(high) * signExtend(aLow);
			value.high[lane][1] = signExtend(high) * signExtend(aHigh);
			Bit#(8) bBits = pack(inputValue.bBar[lane]);
			Int#(5) bLow = unpack(zeroExtend(bBits[3:0]));
			Int#(4) bHigh = unpack(bBits[7:4]);
			value.drive[lane][0] = signExtend(inputValue.meta.x) * signExtend(bLow);
			value.drive[lane][1] = signExtend(inputValue.meta.x) * signExtend(bHigh);
		end
		updateNibbleQ.enq(value);
	endrule

	rule process6_2;
		let inputValue = updateNibbleQ.first;
		updateNibbleQ.deq;
		ScanUpdatePartial value = unpack(0);
		value.meta = inputValue.meta;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(17) lowHigh = signExtend(inputValue.low[lane][1]);
			Int#(17) highHigh = signExtend(inputValue.high[lane][1]);
			Int#(16) driveHigh = signExtend(inputValue.drive[lane][1]);
			value.low[lane] = signExtend(inputValue.low[lane][0]) + (lowHigh << 4);
			value.high[lane] = signExtend(inputValue.high[lane][0]) + (highHigh << 4);
			value.drive[lane] = signExtend(inputValue.drive[lane][0]) + (driveHigh << 4);
		end
		updatePartialQ.enq(value);
	endrule

	rule process6_3;
		let inputValue = updatePartialQ.first;
		updatePartialQ.deq;
		ScanUpdateProducts value = unpack(0);
		value.meta = inputValue.meta;
		value.drive = inputValue.drive;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(25) high = signExtend(inputValue.high[lane]);
			value.recurrent[lane] = signExtend(inputValue.low[lane]) + (high << 8);
		end
		updateProductQ.enq(value);
	endrule

	rule process7;
		let inputValue = updateProductQ.first;
		updateProductQ.deq;
		ScanUnclipped value = unpack(0);
		value.meta = inputValue.meta;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(28) drive = signExtend(inputValue.drive[lane]);
			drive = drive << driveShift;
			value.current[lane] = signExtend(inputValue.recurrent[lane]) + drive;
		end
		unclippedQ.enq(value);
	endrule

	rule process8;
		let inputValue = unclippedQ.first;
		unclippedQ.deq;
		ScanCurrent value = unpack(0);
		value.meta = inputValue.meta;
		Bit#(8) row = {inputValue.meta.channel, inputValue.meta.part};
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(24) current = clip24(signExtend(inputValue.current[lane]));
			value.current[lane] = current;
			stateR[lane].upd(row, truncate(current >> 7));
		end
		currentQ.enq(value);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 4]
	// Split signed INT24 into two unsigned bytes and one signed byte. Register the
	// narrow products and each reconstruction addition to limit soft-multiplier depth.
	//------------------------------------------------------------------------------------
	rule process9_1;
		let inputValue = currentQ.first;
		currentQ.deq;
		ScanOutputQuarters value = unpack(0);
		value.channel = inputValue.meta.channel;
		value.part = inputValue.meta.part;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Bit#(24) currentBits = pack(inputValue.current[lane]);
			Vector#(3, Int#(9)) bytes = newVector;
			bytes[0] = unpack(zeroExtend(currentBits[7:0]));
			bytes[1] = unpack(zeroExtend(currentBits[15:8]));
			Int#(8) high = unpack(currentBits[23:16]);
			bytes[2] = signExtend(high);
			Bit#(8) cBits = pack(inputValue.meta.c[lane]);
			for ( Integer byteIndex = 0; byteIndex < 3; byteIndex = byteIndex + 1 ) begin
				for ( Integer quarter = 0; quarter < 3; quarter = quarter + 1 ) begin
					Bit#(2) quarterBits = truncate(cBits >> (quarter * 2));
					Int#(3) coefficient = unpack(zeroExtend(quarterBits));
					value.products[lane][byteIndex][quarter] = signExtend(bytes[byteIndex]) * signExtend(coefficient);
				end
				Int#(2) high = unpack(cBits[7:6]);
				value.products[lane][byteIndex][3] = signExtend(bytes[byteIndex]) * signExtend(high);
			end
		end
		Bit#(8) directBits = pack(inputValue.meta.direct);
		Int#(5) directLow = unpack(zeroExtend(directBits[3:0]));
		Int#(4) directHigh = unpack(directBits[7:4]);
		value.direct[0] = signExtend(inputValue.meta.x) * signExtend(directLow);
		value.direct[1] = signExtend(inputValue.meta.x) * signExtend(directHigh);
		outputQuarterQ.enq(value);
	endrule

	// c = unsigned(c[1:0]) + 4*unsigned(c[3:2]) + 16*unsigned(c[5:4])
	//     + 64*signed(c[7:6]); the largest partial multiplier is 9x3 bits.
	rule process9_2;
		let inputValue = outputQuarterQ.first;
		outputQuarterQ.deq;
		ScanOutputNibbles value = unpack(0);
		value.channel = inputValue.channel;
		value.part = inputValue.part;
		value.direct = inputValue.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			for ( Integer byteIndex = 0; byteIndex < 3; byteIndex = byteIndex + 1 ) begin
				value.products[lane][byteIndex][0] = signExtend(inputValue.products[lane][byteIndex][0])
					+ (signExtend(inputValue.products[lane][byteIndex][1]) << 2);
				value.products[lane][byteIndex][1] = signExtend(inputValue.products[lane][byteIndex][2])
					+ (signExtend(inputValue.products[lane][byteIndex][3]) << 2);
			end
		end
		outputNibbleQ.enq(value);
	endrule

	rule process9_3;
		let inputValue = outputNibbleQ.first;
		outputNibbleQ.deq;
		ScanOutputProducts value = unpack(0);
		value.channel = inputValue.channel;
		value.part = inputValue.part;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Vector#(3, Int#(17)) products = newVector;
			for ( Integer byteIndex = 0; byteIndex < 3; byteIndex = byteIndex + 1 ) begin
				Int#(17) high = signExtend(inputValue.products[lane][byteIndex][1]);
				products[byteIndex] = signExtend(inputValue.products[lane][byteIndex][0]) + (high << 4);
			end
			value.low[lane] = products[0];
			value.middle[lane] = products[1];
			value.high[lane] = truncate(products[2]);
		end
		Int#(16) directHigh = signExtend(inputValue.direct[1]);
		value.direct = signExtend(inputValue.direct[0]) + (directHigh << 4);
		outputProductQ.enq(value);
	endrule

	rule process10_1;
		let inputValue = outputProductQ.first;
		outputProductQ.deq;
		ScanOutputLower value = unpack(0);
		value.channel = inputValue.channel;
		value.part = inputValue.part;
		value.high = inputValue.high;
		value.direct = inputValue.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(25) middle = signExtend(inputValue.middle[lane]);
			value.low[lane] = signExtend(inputValue.low[lane]) + (middle << 8);
		end
		outputLowerQ.enq(value);
	endrule

	rule process10_2;
		let inputValue = outputLowerQ.first;
		outputLowerQ.deq;
		ScanOutputCombined value = unpack(0);
		value.channel = inputValue.channel;
		value.part = inputValue.part;
		value.direct = inputValue.direct;
		for ( Integer lane = 0; lane < valueOf(ScanLanes); lane = lane + 1 ) begin
			Int#(32) high = signExtend(inputValue.high[lane]);
			value.products[lane] = signExtend(inputValue.low[lane]) + (high << 16);
		end
		combinedQ.enq(value);
	endrule

	rule process11;
		let inputValue = combinedQ.first;
		combinedQ.deq;
		Int#(33) sum = signExtend(inputValue.products[0]) + signExtend(inputValue.products[1]);
		partialQ.enq(ScanPartial {channel: inputValue.channel, part: inputValue.part, sum: sum, direct: inputValue.direct});
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 5]
	// Accumulate four pairs, add the aligned direct term and requantize separately.
	//------------------------------------------------------------------------------------
	rule process12;
		let value = partialQ.first;
		partialQ.deq;
		Int#(35) sum = value.part == 0 ? signExtend(value.sum) : partialR + signExtend(value.sum);
		partialR <= sum;
		if ( value.part == 3 ) begin
			totalQ.enq(ScanTotal {channel: value.channel, sum: sum, direct: value.direct});
		end
	endrule

	rule process13;
		let value = totalQ.first;
		totalQ.deq;
		Int#(36) direct = signExtend(value.direct);
		direct = direct << (directExp - accumulatorExp);
		Int#(36) accumulator = signExtend(value.sum) + direct;
		alignedQ.enq(ScanAligned {channel: value.channel, sum: accumulator});
	endrule

	rule process14;
		let value = alignedQ.first;
		alignedQ.deq;
		resultQ.enq(ScanResult {channel: value.channel, value: requant(signExtend(value.sum), accumulatorExp, blockScale(blockId, "ssmY"))});
	endrule

	rule process15 ( processOn );
		let value = resultQ.first;
		resultQ.deq;
		outputR[value.channel] <= value.value;
		if ( value.channel == fromInteger(valueOf(InnerDim) - 1) ) begin
			Vector#(40, Int#(8)) result = newVector;
			for ( Integer channel = 0; channel < valueOf(InnerDim); channel = channel + 1 ) begin
				result[channel] = channel == valueOf(InnerDim) - 1 ? value.value : outputR[channel];
			end
			outputQ.enq(Token {index: inputR.index, data: result});
			processOn <= False;
		end
	endrule

	method Action put(ScanToken value) if ( localReset.ready );
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(40)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

endpackage
