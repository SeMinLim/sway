package QuantTypes;

import Vector::*;

typedef 4 LaneNum;
typedef 7 TokenNum;
typedef 4 OutputTileNum;
typedef Vector#(LaneNum, Int#(32)) FeatureVec;
typedef Vector#(LaneNum, Int#(8)) QuantVec;
typedef Vector#(LaneNum, Bit#(16)) WeightVec;

typedef struct {
	Bit#(16) token;
	Bit#(1) slot;
} TokenTag deriving (Bits, Eq, FShow);

typedef enum { SmoothOp, ScaleOp, QuantOp } MultiplyOp
	deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	MultiplyOp op;
	Vector#(LaneNum, Int#(64)) product;
	Bit#(6) shift;
} MultiplyRaw deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	MultiplyOp op;
	FeatureVec value;
} MultiplyResult deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	Bit#(32) denominator;
	UInt#(34) remainder;
	Bit#(32) quotient;
	Bool zeroScale;
} ReciprocalWork deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	Bit#(32) value;
	Bool zeroScale;
} ReciprocalResult deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	QuantVec value;
} QuantToken deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	Bit#(2) tile;
	QuantVec value;
} TileTag deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	Bit#(2) tile;
	FeatureVec left;
	FeatureVec right;
} DotPairs deriving (Bits, Eq, FShow);

typedef struct {
	TokenTag tag;
	Bit#(2) tile;
	FeatureVec sums;
} PartialResult deriving (Bits, Eq, FShow);

// AP_TRN/AP_WRAP feature arithmetic uses 18 fractional bits.
function Int#(32) featureProduct(Int#(64) product, Bit#(6) shift);
	return truncate(product >> shift);
endfunction

function Int#(32) maxMagnitude(FeatureVec values);
	Int#(32) largest = 0;
	for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
		// Preserve fixed-width negation. INT_MIN wraps, as in the source type.
		Int#(32) magnitude = (values[i] < 0) ? -values[i] : values[i];
		if ( magnitude > largest ) begin
			largest = magnitude;
		end
	end
	return largest;
endfunction

function Int#(8) quantizeRounded(Int#(32) scaled);
	Int#(32) half = 131072;
	Int#(32) rounded = (scaled >= 0) ? scaled + half : scaled - half;
	Int#(64) wide = signExtend(rounded);
	Int#(64) integerPart = (wide >= 0) ? (wide >> 18) : -((-wide) >> 18);
	Int#(8) result = truncate(integerPart);
	if ( rounded >= 33423360 ) begin // 127.5 in Q18
		result = 127;
	end else if ( rounded <= -33685504 ) begin // -128.5 in Q18
		result = -128;
	end
	return result;
endfunction

function Int#(16) apotProduct(Int#(8) activation, Bit#(4) code);
	Int#(16) x = signExtend(activation);
	Int#(16) magnitude = 0;
	case ( code[2:0] )
		0: magnitude = 0;
		1: magnitude = x << 7;
		2: magnitude = x << 6;
		3: magnitude = x << 4;
		4: magnitude = x << 5;
		5: magnitude = (x << 7) + (x << 5);
		6: magnitude = (x << 6) + (x << 5);
		7: magnitude = (x << 4) + (x << 5);
	endcase
	return (code[3] == 1) ? -magnitude : magnitude;
endfunction

endpackage
