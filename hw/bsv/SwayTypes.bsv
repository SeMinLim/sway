package SwayTypes;

import Vector::*;

// Fixed MARS dimensions. These aliases describe the baseline hardware;
// changing them requires matching generated parameters and counter widths.
typedef 8 InputHeight;
typedef 8 InputWidth;
typedef 5 InputChannels;
typedef 2 PatchSide;
typedef TDiv#(InputHeight, PatchSide) PatchRows;
typedef TDiv#(InputWidth, PatchSide) PatchColumns;
typedef TMul#(TMul#(InputHeight, InputWidth), InputChannels) FrameElements;
typedef TMul#(TMul#(PatchSide, PatchSide), InputChannels) PatchElements;
typedef TMul#(PatchRows, PatchColumns) TokenNum;

typedef 20 ModelDim;
typedef TMul#(2, ModelDim) InnerDim;
typedef 8 StateDim;
typedef 2 DeltaRank;
typedef TMul#(2, InnerDim) ExpandedDim;
typedef TAdd#(DeltaRank, TMul#(2, StateDim)) ProjectionDim;
typedef TMul#(TokenNum, ModelDim) HeadInputDim;
typedef 19 JointNum;
typedef TMul#(3, JointNum) OutputDim;

// Change only this divisor to scale the independent engines: 1, 2, or 4.
// Convolution retains one lane and serializes its taps for every divisor.
// Model dimensions, independent engines, and weights stay fixed.
typedef 4 ParallelismDivisor;
typedef TMax#(1, TDiv#(4, ParallelismDivisor)) LinearLanes;
typedef TMax#(1, TDiv#(2, ParallelismDivisor)) NormLanes;
typedef 1 ConvLanes;
typedef TMax#(1, TDiv#(2, ParallelismDivisor)) GateLanes;
typedef TMax#(1, TDiv#(2, ParallelismDivisor)) ScanLanes;
typedef 4 ConvTaps;
typedef TSub#(ConvTaps, 1) ConvHistory;
typedef 32 SerialFifoDepth;
typedef 4 ResidualSlots;

typedef struct {
	Bit#(4) index;
	Vector#(n, Int#(8)) data;
} Token#(numeric type n) deriving (Bits, Eq, FShow);

interface LinearIfc#(numeric type n, numeric type m);
	method Action put(Token#(n) value);
	method ActionValue#(Token#(m)) get;
endinterface

interface NormIfc;
	method Action put(Token#(ModelDim) value);
	method ActionValue#(Token#(ModelDim)) get;
endinterface

interface BlockIfc;
	method Action put(Token#(ModelDim) value);
	method ActionValue#(Token#(ModelDim)) get;
endinterface

interface SwayIfc;
	// 320 signed INT8 elements per frame, HWC order, scale from nodeScale("input").
	method Action put(Int#(8) value);
	// 57 signed INT8 coordinates per frame, all X then Y then Z.
	method ActionValue#(Int#(8)) get;
endinterface

// The caller selects a proven width for aligned values. No helper widens it.
// Power-of-two scaling uses nearest ties-to-even, also for negative operands.
function Int#(n) shiftRoundN(Int#(n) value, Integer fromExp, Integer toExp);
	Int#(n) result = value;
	if ( fromExp >= toExp ) begin
		result = value << (fromExp - toExp);
	end else if ( toExp - fromExp >= valueOf(n) ) begin
		result = 0;
	end else begin
		Integer shift = toExp - fromExp;
		UInt#(n) magnitude = unpack(pack(value < 0 ? -value : value));
		UInt#(n) quotient = magnitude >> shift;
		UInt#(n) remainder = magnitude - (quotient << shift);
		UInt#(n) half = fromInteger(2 ** (shift - 1));
		Bit#(n) quotientBits = pack(quotient);
		if ( remainder > half || (remainder == half && quotientBits[0] == 1) ) begin
			quotient = quotient + 1;
		end
		result = unpack(pack(quotient));
		if ( value < 0 ) begin
			result = -result;
		end
	end
	return result;
endfunction

function Int#(8) clip8N(Int#(n) value) provisos(Add#(8, padding, n));
	return value > 127 ? 127 : (value < -128 ? -128 : truncate(value));
endfunction

function Int#(24) clip24N(Int#(n) value) provisos(Add#(24, padding, n));
	return value > 8388607 ? 8388607 : (value < -8388608 ? -8388608 : truncate(value));
endfunction

function Int#(8) requantN(Int#(n) value, Integer fromExp, Integer toExp)
	provisos(Add#(9, padding9, n), Add#(8, padding8, n));
	Int#(8) result = 0;
	if ( fromExp >= toExp ) begin
		// Compare in the source domain before shifting so saturation cannot wrap.
		Integer shift = fromExp - toExp;
		if ( shift >= 8 ) begin
			result = value > 0 ? 127 : (value < 0 ? -128 : 0);
		end else begin
			Int#(n) upper = fromInteger(127 / (2 ** shift));
			Int#(n) lower = fromInteger(-128 / (2 ** shift));
			if ( value > upper ) begin
				result = 127;
			end else if ( value < lower ) begin
				result = -128;
			end else begin
				result = truncate(value << shift);
			end
		end
	end else if ( toExp - fromExp >= valueOf(n) ) begin
		result = 0;
	end else begin
		Integer shift = toExp - fromExp;
		Int#(n) floorValue = value >> shift;
		Bit#(n) remainder = pack(value) & fromInteger((2 ** shift) - 1);
		Bit#(n) half = fromInteger(2 ** (shift - 1));
		Bit#(n) quotientBits = pack(floorValue);
		Bool increment = remainder > half || (remainder == half && quotientBits[0] == 1);
		if ( floorValue >= 127 ) begin
			result = 127;
		end else if ( floorValue <= -129 ) begin
			result = -128;
		end else begin
			Int#(9) rounded = truncate(floorValue);
			if ( increment ) begin
				rounded = rounded + 1;
			end
			result = truncate(rounded);
		end
	end
	return result;
endfunction

function Int#(32) shiftRound32(Int#(32) value, Integer fromExp, Integer toExp);
	return shiftRoundN(value, fromExp, toExp);
endfunction

function Int#(8) requant32(Int#(32) value, Integer fromExp, Integer toExp);
	return requantN(value, fromExp, toExp);
endfunction

// Compatibility for untouched interfaces; bounded datapaths use the helpers above.
function Int#(64) shiftRound(Int#(64) value, Integer fromExp, Integer toExp);
	return shiftRoundN(value, fromExp, toExp);
endfunction

function Int#(8) clip8(Int#(64) value);
	return clip8N(value);
endfunction

function Int#(24) clip24(Int#(64) value);
	return clip24N(value);
endfunction

function Int#(8) requant(Int#(64) value, Integer fromExp, Integer toExp);
	return requantN(value, fromExp, toExp);
endfunction

endpackage
