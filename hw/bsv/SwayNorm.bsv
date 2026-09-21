package SwayNorm;

import Assert::*;
import FIFO::*;
import Vector::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayReset::*;

typedef struct {
	Vector#(2, Int#(8)) inputValues;
	Vector#(2, Int#(8)) weights;
	Vector#(2, Int#(8)) biases;
} NormRead deriving (Bits);

typedef struct {
	Vector#(2, Int#(14)) values;
	Vector#(2, Int#(8)) weights;
	Vector#(2, Int#(8)) biases;
} NormCentered deriving (Bits);

typedef struct {
	Vector#(2, Vector#(3, Int#(13))) weightedLow;
	Vector#(2, Int#(10)) weightedHigh;
	Vector#(2, Vector#(3, Int#(13))) biasedLow;
	Vector#(2, Int#(10)) biasedHigh;
} NormNibbleProducts deriving (Bits);

typedef struct {
	Vector#(2, Int#(16)) weightedLow;
	Vector#(2, Int#(14)) weightedHigh;
	Vector#(2, Int#(16)) biasedLow;
	Vector#(2, Int#(14)) biasedHigh;
} NormPartialProducts deriving (Bits);

typedef struct {
	Vector#(2, UInt#(9)) quotientLow;
	Vector#(2, Bool) negative;
	Vector#(2, Bool) above127;
	Vector#(2, Bool) exactly127;
	Vector#(2, Bool) increment;
} NormRounding deriving (Bits);

module mkSwayNorm#(Integer blockId)(NormIfc);
	Integer weightExp = blockScale(blockId, "normWeight");
	Integer biasExp = blockScale(blockId, "normBias");
	Integer outputExp = blockScale(blockId, "norm");
	Integer commonExp = min(min(weightExp, biasExp), outputExp);
	Integer weightShift = weightExp - commonExp;
	Integer biasShift = biasExp - commonExp;
	Integer outputShift = outputExp - commonExp;
	Integer groupNum = valueOf(ModelDim) / valueOf(NormLanes);

	// |20*x-sum|<=4845. The aligned numerator is bounded by
	// (4845*128+5100*128)*16=20367360 < 2^25; denominator<=5100*8=40800.
	staticAssert(valueOf(NormLanes) == 2 && weightShift <= 4 && biasShift <= 4 && outputShift <= 3,
		"Range-normalization scales exceed the 26-bit numerator / 16-bit denominator bounds");

	LocalResetIfc localReset <- mkSwayLocalReset;

	FIFO#(Token#(20)) inputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Token#(20)) outputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Int#(8), Bool)) statisticsQ <- mkFIFO(reset_by localReset.rst);
	FIFO#(Bit#(4)) readCommandQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(NormRead) readQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(NormCentered) scaledQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(NormCentered) centeredQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(NormNibbleProducts) nibbleProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(NormPartialProducts) partialProductQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(2, Int#(22)), Vector#(2, Int#(22)))) productQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Vector#(2, Int#(26))) numeratorQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(NormRounding) roundingQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Vector#(2, Int#(8))) resultQ <- mkFIFO1(reset_by localReset.rst);

	// Each phase writes its data before enabling the next; reset only validity state.
	Vector#(20, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	Reg#(Bit#(4)) indexR <- mkRegU;
	Reg#(Int#(13)) sumR <- mkRegU;
	Reg#(Int#(8)) minimumR <- mkRegU;
	Reg#(Int#(8)) maximumR <- mkRegU;
	Reg#(Int#(14)) denominatorBaseR <- mkRegU;
	Reg#(UInt#(16)) denominatorR <- mkRegU;
	Reg#(Bit#(5)) channelCnt <- mkRegU;
	Reg#(Bit#(4)) groupCnt <- mkRegU;
	Reg#(Bit#(10)) collectMaskR <- mkRegU;
	Reg#(Bit#(5)) divideCnt <- mkRegU;
	Reg#(Vector#(2, UInt#(26))) quotientR <- mkRegU;
	Reg#(Vector#(2, UInt#(16))) remainderR <- mkRegU;
	Reg#(Vector#(2, Bool)) negativeR <- mkRegU;
	Reg#(Bool) activeOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) statisticsDone <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) denominatorDone <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) preparingOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) divideOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) roundOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) emitOn <- mkReg(False, reset_by localReset.rst);

`ifdef SWAY_BLOCK_PROFILE
	// Endpoint-only observation distinguishes ready normalization data from
	// waiting for the input-projection engine to accept it.
	Reg#(UInt#(64)) blockProfileCycleCnt <- mkReg(0);
	Reg#(UInt#(32)) blockProfileFrameCnt <- mkReg(0);
	if ( blockId == 0 ) begin
		rule blockProfileTick;
			blockProfileCycleCnt <= blockProfileCycleCnt + 1;
		endrule
	end
`endif

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// The input queue retains the token while channel statistics are accumulated.
	//------------------------------------------------------------------------------------
	rule process1 ( !activeOn );
		indexR <= inputQ.first.index;
		sumR <= 0;
		minimumR <= 127;
		maximumR <= -128;
		channelCnt <= 0;
		groupCnt <= 0;
		statisticsDone <= False;
		denominatorDone <= False;
		activeOn <= True;
	endrule

	// Register the channel mux before the sum and min/max feedback comparisons.
	// Counter 20 stops issue; completion follows the last queued sample's update.
	rule process2Select ( activeOn && !statisticsDone && channelCnt < 20 );
		Int#(8) value = inputQ.first.data[channelCnt];
		statisticsQ.enq(tuple2(value, channelCnt == 19));
		channelCnt <= channelCnt + 1;
	endrule

	rule process2Statistics ( activeOn && !statisticsDone );
		let sample = statisticsQ.first;
		statisticsQ.deq;
		Int#(8) value = tpl_1(sample);
		sumR <= sumR + signExtend(value);
		minimumR <= value < minimumR ? value : minimumR;
		maximumR <= value > maximumR ? value : maximumR;
		if ( tpl_2(sample) ) begin
			statisticsDone <= True;
		end
	endrule

	rule process3Denominator ( activeOn && statisticsDone && !denominatorDone );
		Int#(9) span = signExtend(maximumR) - signExtend(minimumR);
		Int#(14) wideSpan = signExtend(span);
		Int#(14) denominator = (wideSpan << 4) + (wideSpan << 2);
		if ( span == 0 ) begin
			denominator = 1;
		end
		denominatorBaseR <= denominator;
		denominatorR <= zeroExtend(unpack(pack(denominator))) << outputShift;
		denominatorDone <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Channel selection, constant scaling, centering, partial products, aligned addition,
	// and absolute value each terminate at an explicit register/FIFO boundary.
	//------------------------------------------------------------------------------------
	rule process4Request ( activeOn && denominatorDone && !preparingOn && !emitOn );
		readCommandQ.enq(groupCnt);
		// Decode the two destination rows before the arithmetic pipeline starts.
		collectMaskR <= 1 << groupCnt;
		preparingOn <= True;
	endrule

	// FIFO readiness separates coefficient lookup from the group-update controller.
	rule process4Read;
		let group = readCommandQ.first;
		readCommandQ.deq;
		NormRead value = ?;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			Bit#(6) channel = zeroExtend(group) * 2 + fromInteger(lane);
			value.inputValues[lane] = inputQ.first.data[channel];
			value.weights[lane] = normWeight(blockId, channel);
			value.biases[lane] = normBias(blockId, channel);
		end
		readQ.enq(value);
	endrule

	rule process5Scale;
		let value = readQ.first;
		readQ.deq;
		NormCentered scaled = NormCentered { values: ?, weights: value.weights, biases: value.biases };
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			Int#(14) inputValue = signExtend(value.inputValues[lane]);
			scaled.values[lane] = (inputValue << 4) + (inputValue << 2);
		end
		scaledQ.enq(scaled);
	endrule

	rule process6Center;
		NormCentered value = scaledQ.first;
		scaledQ.deq;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			value.values[lane] = value.values[lane] - signExtend(sumR);
		end
		centeredQ.enq(value);
	endrule

	rule process7Multiply;
		let value = centeredQ.first;
		centeredQ.deq;
		NormNibbleProducts products = ?;
		Bit#(14) denominatorBits = pack(denominatorBaseR);
		Int#(2) denominatorHigh = unpack(denominatorBits[13:12]);
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			Bit#(14) centeredBits = pack(value.values[lane]);
			Int#(2) centeredHigh = unpack(centeredBits[13:12]);
			for ( Integer nibble = 0; nibble < 3; nibble = nibble + 1 ) begin
				Bit#(4) centeredNibble = truncate(centeredBits >> (nibble * 4));
				Bit#(4) denominatorNibble = truncate(denominatorBits >> (nibble * 4));
				Int#(5) centeredLow = unpack(zeroExtend(centeredNibble));
				Int#(5) denominatorLow = unpack(zeroExtend(denominatorNibble));
				products.weightedLow[lane][nibble] = signExtend(centeredLow) * signExtend(value.weights[lane]);
				products.biasedLow[lane][nibble] = signExtend(denominatorLow) * signExtend(value.biases[lane]);
			end
			products.weightedHigh[lane] = signExtend(centeredHigh) * signExtend(value.weights[lane]);
			products.biasedHigh[lane] = signExtend(denominatorHigh) * signExtend(value.biases[lane]);
		end
		nibbleProductQ.enq(products);
	endrule

	// Each unsigned nibble uses a 5x8 signed product; the signed top two bits use 2x8.
	// Pairwise assembly produces unsigned-byte x weight and signed-six-bit x weight.
	rule process7CombinePairs;
		let value = nibbleProductQ.first;
		nibbleProductQ.deq;
		NormPartialProducts products = ?;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			products.weightedLow[lane] = signExtend(value.weightedLow[lane][0])
				+ (signExtend(value.weightedLow[lane][1]) << 4);
			products.weightedHigh[lane] = signExtend(value.weightedLow[lane][2])
				+ (signExtend(value.weightedHigh[lane]) << 4);
			products.biasedLow[lane] = signExtend(value.biasedLow[lane][0])
				+ (signExtend(value.biasedLow[lane][1]) << 4);
			products.biasedHigh[lane] = signExtend(value.biasedLow[lane][2])
				+ (signExtend(value.biasedHigh[lane]) << 4);
		end
		partialProductQ.enq(products);
	endrule

	// x = unsigned(x[7:0]) + 256*signed(x[13:8]); no partial product rounds.
	// The low product fits 16 signed bits, the high product fits 14.
	rule process7Combine;
		let value = partialProductQ.first;
		partialProductQ.deq;
		Vector#(2, Int#(22)) weighted = newVector;
		Vector#(2, Int#(22)) biased = newVector;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			weighted[lane] = signExtend(value.weightedLow[lane]) + (signExtend(value.weightedHigh[lane]) << 8);
			biased[lane] = signExtend(value.biasedLow[lane]) + (signExtend(value.biasedHigh[lane]) << 8);
		end
		productQ.enq(tuple2(weighted, biased));
	endrule

	rule process8Add;
		let value = productQ.first;
		productQ.deq;
		Vector#(2, Int#(26)) numerator = newVector;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			numerator[lane] = (signExtend(tpl_1(value)[lane]) << weightShift)
				+ (signExtend(tpl_2(value)[lane]) << biasShift);
		end
		numeratorQ.enq(numerator);
	endrule

	rule process9Absolute ( !divideOn && !roundOn );
		let value = numeratorQ.first;
		numeratorQ.deq;
		Vector#(2, UInt#(26)) numerator = newVector;
		Vector#(2, Bool) negative = newVector;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			negative[lane] = value[lane] < 0;
			numerator[lane] = unpack(pack(negative[lane] ? -value[lane] : value[lane]));
		end
		quotientR <= numerator;
		remainderR <= replicate(0);
		negativeR <= negative;
		divideCnt <= 0;
		divideOn <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Two restoring dividers, 26 steps. A 17-bit subtraction supplies both the
	// candidate remainder and borrow; there is no compare-then-subtract carry chain.
	//------------------------------------------------------------------------------------
	rule process10Divide ( activeOn && divideOn );
		Vector#(2, UInt#(26)) quotients = newVector;
		Vector#(2, UInt#(16)) remainders = newVector;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			Bit#(26) dividendBits = pack(quotientR[lane]);
			UInt#(17) trial = (zeroExtend(remainderR[lane]) << 1) | zeroExtend(unpack(dividendBits[25:25]));
			UInt#(17) difference = trial - zeroExtend(denominatorR);
			Bit#(17) differenceBits = pack(difference);
			UInt#(26) quotient = quotientR[lane] << 1;
			if ( differenceBits[16] == 0 ) begin
				remainders[lane] = truncate(difference);
				quotient = quotient | 1;
			end else begin
				remainders[lane] = truncate(trial);
			end
			quotients[lane] = quotient;
		end
		quotientR <= quotients;
		remainderR <= remainders;
		if ( divideCnt == 25 ) begin
			divideOn <= False;
			roundOn <= True;
		end else begin
			divideCnt <= divideCnt + 1;
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 4]
	// Full rational ties-to-even rounding, signed saturation, and static row writes.
	//------------------------------------------------------------------------------------
	rule process11Compare ( activeOn && roundOn && !divideOn );
		NormRounding value = ?;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			UInt#(17) twiceRemainder = zeroExtend(remainderR[lane]) << 1;
			Bit#(26) quotientBits = pack(quotientR[lane]);
			value.quotientLow[lane] = truncate(quotientR[lane]);
			value.negative[lane] = negativeR[lane];
			value.increment[lane] = twiceRemainder > zeroExtend(denominatorR)
				|| (twiceRemainder == zeroExtend(denominatorR) && quotientBits[0] == 1);
			value.above127[lane] = quotientBits[25:7] != 0;
			value.exactly127[lane] = quotientBits[6:0] == 127;
		end
		roundingQ.enq(value);
		roundOn <= False;
	endrule

	rule process11Round;
		let value = roundingQ.first;
		roundingQ.deq;
		Vector#(2, Int#(8)) result = newVector;
		for ( Integer lane = 0; lane < 2; lane = lane + 1 ) begin
			if ( !value.negative[lane] && (value.above127[lane] || value.exactly127[lane]) ) begin
				result[lane] = 127;
			end else if ( value.negative[lane] && value.above127[lane] ) begin
				result[lane] = -128;
			end else begin
				UInt#(9) rounded = value.quotientLow[lane];
				if ( value.increment[lane] ) begin
					rounded = rounded + 1;
				end
				Int#(10) signedResult = unpack(zeroExtend(pack(rounded)));
				result[lane] = truncate(value.negative[lane] ? -signedResult : signedResult);
			end
		end
		resultQ.enq(result);
	endrule

	rule process12Collect ( activeOn && preparingOn && !emitOn );
		let result = resultQ.first;
		resultQ.deq;
		for ( Integer channel = 0; channel < 20; channel = channel + 1 ) begin
			if ( collectMaskR[channel / 2] == 1 ) begin
				outputR[channel] <= result[channel % 2];
			end
		end
		preparingOn <= False;
		if ( groupCnt == fromInteger(groupNum - 1) ) begin
			emitOn <= True;
		end else begin
			groupCnt <= groupCnt + 1;
		end
	endrule

	rule process13Emit ( activeOn && emitOn );
		outputQ.enq(Token { index: indexR, data: readVReg(outputR) });
`ifdef SWAY_BLOCK_PROFILE
		if ( blockId == 0 ) begin
			$display("SWAY_BLOCK,norm_ready,%0d,%0d,%0d", blockProfileFrameCnt, indexR, blockProfileCycleCnt);
			if ( indexR == 15 ) blockProfileFrameCnt <= blockProfileFrameCnt + 1;
		end
`endif
		inputQ.deq;
		emitOn <= False;
		activeOn <= False;
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
