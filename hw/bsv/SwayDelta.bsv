package SwayDelta;

import Assert::*;
import FIFO::*;
import FIFOF::*;
import Vector::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayReset::*;

interface SwayDeltaIfc;
	interface LinearIfc#(2, 40) block0;
	interface LinearIfc#(2, 40) block1;
endinterface

typedef struct {
	Bit#(6) row;
	Bool last;
	Int#(8) x;
	Int#(8) weight;
} DeltaOperand deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) row;
	Bool last;
	Int#(13) low;
	Int#(12) high;
} DeltaPartial deriving (Bits, Eq, FShow);

typedef struct {
	Bit#(6) row;
	Bool last;
	Int#(16) value;
} DeltaProduct deriving (Bits, Eq, FShow);

// The generated layout stores row r in bank r % 4, at (r / 4) * 2 + input.
// Read those unchanged constants through a scalar LUT table in all modes.
function Int#(8) deltaWeight(Integer layerId, Bit#(7) address);
	Vector#(80, Int#(8)) values = newVector;
	for ( Integer row = 0; row < 40; row = row + 1 ) begin
		for ( Integer inputId = 0; inputId < 2; inputId = inputId + 1 ) begin
			values[row * 2 + inputId] = linearWeight(layerId, row % 4,
				fromInteger((row / 4) * 2 + inputId));
		end
	end
	return values[address];
endfunction

// Mode 0/1 builds a dedicated scalar engine for that block; mode 2 shares
// the same arithmetic/issue pipeline between the two ordered clients.
module mkSwayDelta#(Integer mode)(SwayDeltaIfc);
	staticAssert(mode >= 0 && mode <= 2, "Delta mode must be 0, 1, or 2");
	staticAssert(layerInputScale(3) + layerWeightScale(3) == -11 &&
		layerInputScale(7) + layerWeightScale(7) == -11 &&
		layerBiasScale(3) == -8 && layerBiasScale(7) == -8,
		"Delta bounds require the checked-in quantization profile");

	LocalResetIfc localReset <- mkSwayLocalReset;
	FIFOF#(Token#(2)) input0Q <- mkFIFOF1(reset_by localReset.rst);
	FIFOF#(Token#(2)) input1Q <- mkFIFOF1(reset_by localReset.rst);
	FIFOF#(Token#(40)) output0Q <- mkFIFOF1(reset_by localReset.rst);
	FIFOF#(Token#(40)) output1Q <- mkFIFOF1(reset_by localReset.rst);
	FIFO#(DeltaOperand) operandQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(DeltaPartial) partialQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(DeltaProduct) productQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(Tuple3#(Int#(18), Int#(18), Bit#(6))) biasQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(Tuple2#(Int#(18), Bit#(6))) affineQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(Tuple2#(Int#(8), Bit#(6))) resultQ <- mkSizedFIFO(2, reset_by localReset.rst);

	Reg#(Token#(2)) inputR <- mkRegU;
	Vector#(40, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	Reg#(Int#(17)) sumR <- mkRegU;
	Reg#(Bit#(7)) issueCnt <- mkRegU;
	Reg#(Bit#(1)) clientR <- mkRegU;
	Reg#(Bit#(1)) nextClientR <- mkReg(0, reset_by localReset.rst);
	Reg#(Bool) activeOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) emitOn <- mkReg(False, reset_by localReset.rst);

	//------------------------------------------------------------------------------------
	// [STAGE 1] Reserve a result slot before selecting a complete projection.
	// A stalled consumer cannot block the other client after its slot fills.
	//------------------------------------------------------------------------------------
	if ( mode != 1 ) begin
		rule process1Block0 ( !activeOn && input0Q.notEmpty && output0Q.notFull &&
			(mode == 0 || !input1Q.notEmpty || !output1Q.notFull || nextClientR == 0) );
			inputR <= input0Q.first;
			input0Q.deq;
			clientR <= 0;
			nextClientR <= 1;
			issueCnt <= 0;
			activeOn <= True;
		endrule
	end
	if ( mode != 0 ) begin
		rule process1Block1 ( !activeOn && input1Q.notEmpty && output1Q.notFull &&
			(mode == 1 || !input0Q.notEmpty || !output0Q.notFull || nextClientR == 1) );
			inputR <= input1Q.first;
			input1Q.deq;
			clientR <= 1;
			nextClientR <= 0;
			issueCnt <= 0;
			activeOn <= True;
		endrule
	end

	// Two inputs per row need no chunk/group preparation bubbles. Register the
	// coefficient and its associated input before the multiplication pipeline.
	rule process2Issue ( activeOn && issueCnt < 80 );
		Int#(8) weight = 0;
		if ( mode == 0 ) weight = deltaWeight(3, issueCnt);
		else if ( mode == 1 ) weight = deltaWeight(7, issueCnt);
		else weight = clientR == 0 ? deltaWeight(3, issueCnt) : deltaWeight(7, issueCnt);
		operandQ.enq(DeltaOperand {row: truncate(issueCnt >> 1), last: issueCnt[0] == 1,
			x: inputR.data[issueCnt[0]], weight: weight});
		issueCnt <= issueCnt + 1;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2] One signed INT8 product per cycle, matching SwayLinear exactly.
	//------------------------------------------------------------------------------------
	rule process3Multiply;
		let value = operandQ.first;
		operandQ.deq;
		Bit#(8) bits = pack(value.x);
		Int#(5) low = unpack(zeroExtend(bits[3:0]));
		Int#(4) high = unpack(bits[7:4]);
		partialQ.enq(DeltaPartial {row: value.row, last: value.last,
			low: signExtend(low) * signExtend(value.weight),
			high: signExtend(high) * signExtend(value.weight)});
	endrule

	rule process4Combine;
		let value = partialQ.first;
		partialQ.deq;
		Int#(16) high = signExtend(value.high);
		productQ.enq(DeltaProduct {row: value.row, last: value.last,
			value: signExtend(value.low) + (high << 4)});
	endrule

	rule process5Accumulate ( !productQ.first.last );
		let value = productQ.first;
		productQ.deq;
		sumR <= signExtend(value.value);
	endrule

	// Two INT8 products fit signed 17 bits. The bias adds three low zero bits;
	// the complete affine sum fits signed 18 bits before unchanged rounding.
	rule process5Last ( productQ.first.last );
		let value = productQ.first;
		productQ.deq;
		Int#(18) sum = signExtend(sumR) + signExtend(value.value);
		Int#(8) bias = 0;
		if ( mode == 0 ) bias = linearBias(3, zeroExtend(value.row));
		else if ( mode == 1 ) bias = linearBias(7, zeroExtend(value.row));
		else bias = clientR == 0 ? linearBias(3, zeroExtend(value.row)) : linearBias(7, zeroExtend(value.row));
		Int#(18) alignedBias = signExtend(bias) << 3;
		biasQ.enq(tuple3(sum, alignedBias, value.row));
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3] Preserve layer bias, saturation, and ties-to-even rounding.
	//------------------------------------------------------------------------------------
	rule process6Bias;
		let value = biasQ.first;
		biasQ.deq;
		affineQ.enq(tuple2(tpl_1(value) + tpl_2(value), tpl_3(value)));
	endrule

	rule process7Round;
		let value = affineQ.first;
		affineQ.deq;
		Int#(8) result = 0;
		if ( mode == 0 ) result = requant(signExtend(tpl_1(value)), -11, layerOutputScale(3));
		else if ( mode == 1 ) result = requant(signExtend(tpl_1(value)), -11, layerOutputScale(7));
		else result = clientR == 0 ? requant(signExtend(tpl_1(value)), -11, layerOutputScale(3)) :
			requant(signExtend(tpl_1(value)), -11, layerOutputScale(7));
		resultQ.enq(tuple2(result, tpl_2(value)));
	endrule

	rule process8Collect ( activeOn && !emitOn );
		let value = resultQ.first;
		resultQ.deq;
		for ( Integer row = 0; row < 40; row = row + 1 ) begin
			if ( tpl_2(value) == fromInteger(row) ) outputR[row] <= tpl_1(value);
		end
		if ( tpl_2(value) == 39 ) emitOn <= True;
	endrule

	// Split rules avoid gating emission on the unrelated client's FIFO guard.
	if ( mode != 1 ) begin
		rule process9Block0 ( activeOn && emitOn && clientR == 0 );
			output0Q.enq(Token {index: inputR.index, data: readVReg(outputR)});
			emitOn <= False;
			activeOn <= False;
		endrule
	end
	if ( mode != 0 ) begin
		rule process9Block1 ( activeOn && emitOn && clientR == 1 );
			output1Q.enq(Token {index: inputR.index, data: readVReg(outputR)});
			emitOn <= False;
			activeOn <= False;
		endrule
	end

	interface LinearIfc block0;
		method Action put(Token#(2) value) if ( localReset.ready && mode != 1 );
			input0Q.enq(value);
		endmethod
		method ActionValue#(Token#(40)) get if ( localReset.ready && mode != 1 );
			let value = output0Q.first;
			output0Q.deq;
			return value;
		endmethod
	endinterface
	interface LinearIfc block1;
		method Action put(Token#(2) value) if ( localReset.ready && mode != 0 );
			input1Q.enq(value);
		endmethod
		method ActionValue#(Token#(40)) get if ( localReset.ready && mode != 0 );
			let value = output1Q.first;
			output1Q.deq;
			return value;
		endmethod
	endinterface
endmodule

endpackage
