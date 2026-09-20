package SwayTypes;

import Vector::*;

typedef 20 ModelDim;
typedef 40 InnerDim;
typedef 8 StateDim;
typedef 16 TokenNum;
typedef 4 LinearLanes;
typedef 2 NormLanes;

typedef struct {
	Bit#(4) index;
	Vector#(n, Int#(8)) data;
} Token#(numeric type n) deriving (Bits, Eq, FShow);

interface LinearIfc#(numeric type n, numeric type m);
	method Action put(Token#(n) value);
	method ActionValue#(Token#(m)) get;
endinterface

interface NormIfc;
	method Action put(Token#(20) value);
	method ActionValue#(Token#(20)) get;
endinterface

interface BlockIfc;
	method Action put(Token#(20) value);
	method ActionValue#(Token#(20)) get;
endinterface

interface SwayIfc;
	// 320 signed INT8 elements per frame, HWC order, scale from nodeScale("input").
	method Action put(Int#(8) value);
	// 57 signed INT8 coordinates per frame, all X then Y then Z.
	method ActionValue#(Int#(8)) get;
endinterface

// Power-of-two scaling uses nearest ties-to-even, also for negative operands.
function Int#(64) shiftRound(Int#(64) value, Integer fromExp, Integer toExp);
	Int#(64) result = value;
	if ( fromExp >= toExp ) begin
		result = value << (fromExp - toExp);
	end else begin
		Integer shift = toExp - fromExp;
		UInt#(64) magnitude = unpack(pack(value < 0 ? -value : value));
		UInt#(64) quotient = magnitude >> shift;
		UInt#(64) remainder = magnitude - (quotient << shift);
		UInt#(64) half = fromInteger(2 ** (shift - 1));
		Bit#(64) quotientBits = pack(quotient);
		if ( remainder > half || (remainder == half && quotientBits[0] == 1) ) begin
			quotient = quotient + 1;
		end
		result = unpack(pack(quotient));
		if ( value < 0 ) result = -result;
	end
	return result;
endfunction

function Int#(8) clip8(Int#(64) value);
	Bit#(64) bits = pack(value);
	Bool fits = bits[63:7] == 0 || bits[63:7] == '1;
	return fits ? truncate(value) : (bits[63] == 0 ? 127 : -128);
endfunction

function Int#(24) clip24(Int#(64) value);
	Bit#(64) bits = pack(value);
	Bool fits = bits[63:23] == 0 || bits[63:23] == '1;
	return fits ? truncate(value) : (bits[63] == 0 ? 8388607 : -8388608);
endfunction

function Int#(8) requant(Int#(64) value, Integer fromExp, Integer toExp);
	Int#(8) result = 0;
	if ( fromExp >= toExp ) begin
		result = clip8(value << (fromExp - toExp));
	end else begin
		Integer shift = toExp - fromExp;
		Int#(64) floorValue = value >> shift;
		Bit#(64) valueBits = pack(value);
		Bit#(64) quotientBits = pack(floorValue);
		Bool sticky = (valueBits & fromInteger((2 ** (shift - 1)) - 1)) != 0;
		Bool increment = valueBits[shift - 1] == 1 && (sticky || quotientBits[0] == 1);
		Bool fits = quotientBits[63:7] == 0 || quotientBits[63:7] == '1;
		// Sign-extension checks are reduction trees, not wide carry-chain compares.
		// An out-of-range floor quotient saturates even after its possible +1.
		if ( !fits ) result = quotientBits[63] == 0 ? 127 : -128;
		else if ( quotientBits[7:0] == 8'h7f ) result = 127;
		else begin
			Bit#(8) rounded = quotientBits[7:0] + zeroExtend(pack(increment));
			result = unpack(rounded);
		end
	end
	return result;
endfunction

endpackage
