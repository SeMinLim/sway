package SwayTypes;
import Vector::*;

typedef Int#(16) SwayValue;
typedef Int#(48) SwayAcc;
typedef 64 SwayDim;
typedef 128 SwayInner;
typedef 16 SwayState;
typedef 4 SwayRank;
Integer swayFraction = 10;

typedef struct {
	Bool first;
	Bit#(16) tag;
	Vector#(n, SwayValue) data;
} SwayFrame#(numeric type n) deriving (Bits, Eq);

typedef struct {
	Bool first;
	Bit#(16) tag;
	Vector#(128, SwayValue) x;
	Vector#(128, SwayValue) gate;
} SwayConvFrame deriving (Bits, Eq);

typedef struct {
	SwayConvFrame inputFrame;
	Vector#(16, SwayValue) b;
	Vector#(16, SwayValue) c;
} SwayParamMeta deriving (Bits, Eq);

typedef struct {
	SwayParamMeta meta;
	Vector#(128, SwayValue) delta;
} SwayScanFrame deriving (Bits, Eq);

typedef struct {
	Bit#(64) cycles;
	Bit#(64) busyCycles;
	Bit#(64) mulCount;
	Bit#(64) inputEmptyCycles;
	Bit#(64) outputFullCycles;
} SwayStats deriving (Bits, Eq);

interface SwayVectorIfc#(numeric type ni, numeric type no);
	method Action put(SwayFrame#(ni) data);
	method ActionValue#(SwayFrame#(no)) get;
	method SwayStats stats;
	method Bool busy;
endinterface

function SwayValue swaySaturate(SwayAcc x);
	SwayValue y = truncate(x);
	if ( x > 32767 ) y = 32767;
	if ( x < -32768 ) y = -32768;
	return y;
endfunction

function SwayValue swayRound(SwayAcc x);
	// Arithmetic shift, round toward negative infinity, then saturate.
	return swaySaturate(x >> swayFraction);
endfunction

function Int#(32) swayProduct(SwayValue a, SwayValue b);
	Int#(32) aa = signExtend(a);
	Int#(32) bb = signExtend(b);
	return aa * bb;
endfunction

function SwayValue swayMul(SwayValue a, SwayValue b);
	return swayRound(signExtend(swayProduct(a, b)));
endfunction

function SwayValue swayAdd(SwayValue a, SwayValue b);
	SwayAcc aa = signExtend(a);
	SwayAcc bb = signExtend(b);
	return swaySaturate(aa + bb);
endfunction

function Bit#(12) swayLutAddress(SwayValue x);
	Bit#(16) ordered = pack(x) ^ 16'h8000;
	return truncate(ordered >> 4);
endfunction
endpackage
