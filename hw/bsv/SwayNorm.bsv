package SwayNorm;

import Assert::*;
import FIFO::*;
import Vector::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayMultiply::*;

typedef TDiv#(ModelDim, NormLanes) NormGroups;
typedef TMax#(1, TLog#(NormGroups)) NormGroupWidth;

typedef struct {
	Vector#(NormLanes, Int#(8)) inputs;
	Vector#(NormLanes, Int#(8)) weights;
	Vector#(NormLanes, Int#(8)) biases;
	Int#(13) sum;
	Int#(8) minimum;
	Int#(8) maximum;
} NormSelected deriving(Bits, Eq);

typedef struct {
	Vector#(NormLanes, Int#(14)) centered;
	Vector#(NormLanes, Int#(8)) weights;
	Vector#(NormLanes, Int#(8)) biases;
	Int#(14) denominator;
} NormOperands deriving(Bits, Eq);

typedef struct {
	Vector#(NormLanes, UInt#(14)) centered;
	Vector#(NormLanes, UInt#(8)) weights;
	Vector#(NormLanes, UInt#(8)) biases;
	Vector#(NormLanes, Bool) weightedNegative;
	Vector#(NormLanes, Bool) biasedNegative;
	UInt#(14) denominator;
} NormMagnitudes deriving(Bits, Eq);

typedef struct {
	Vector#(NormLanes, Bool) weightedNegative;
	Vector#(NormLanes, Bool) biasedNegative;
	UInt#(14) denominator;
} NormProductMetadata deriving(Bits, Eq);

typedef struct {
	Vector#(NormLanes, UInt#(22)) weighted;
	Vector#(NormLanes, UInt#(22)) biased;
	Vector#(NormLanes, Bool) weightedNegative;
	Vector#(NormLanes, Bool) biasedNegative;
	UInt#(14) denominator;
} NormRawProducts deriving(Bits, Eq);

typedef struct {
	Vector#(NormLanes, Int#(22)) weighted;
	Vector#(NormLanes, Int#(22)) biased;
	Int#(14) denominator;
} NormProducts deriving(Bits, Eq);

typedef struct {
	Vector#(NormLanes, Int#(32)) numerators;
	UInt#(32) denominator;
} NormNumerators deriving(Bits, Eq);

typedef struct {
	Vector#(NormLanes, UInt#(8)) quotients;
	Vector#(NormLanes, Bool) quotientHigh;
	Vector#(NormLanes, Bool) roundUp;
	Vector#(NormLanes, Bool) negative;
} NormRoundDecision deriving(Bits, Eq);

module mkSwayNorm#(Integer blockId)(NormIfc);
	Integer weightExp = blockScale(blockId, "normWeight");
	Integer biasExp = blockScale(blockId, "normBias");
	Integer outputExp = blockScale(blockId, "norm");
	Integer commonExp = min(min(weightExp, biasExp), outputExp);
	Integer weightShift = weightExp - commonExp;
	Integer biasShift = biasExp - commonExp;
	Integer outputShift = outputExp - commonExp;
	Integer groupNum = valueOf(NormGroups);

	staticAssert(valueOf(ModelDim) == 20,
		"Range-normalization shift-add requires 20 channels");
	staticAssert(valueOf(ModelDim) % valueOf(NormLanes) == 0,
		"Normalization lanes must divide the model dimension");

	// |20*x-sum| <= 4845, gamma/bias <= 128, 20*(max-min) <= 5100.
	// These bounds keep the signed numerator below 2^31 and denominator below 2^32.
	staticAssert(weightShift <= 10 && biasShift <= 10 && outputShift <= 18,
		"Range-normalization scale alignment exceeds the 32-bit divider bounds");

	FIFO#(Token#(ModelDim)) inputQ <- mkFIFO1;
	FIFO#(Token#(ModelDim)) outputQ <- mkFIFO1;
	FIFO#(Tuple2#(Int#(8), Bool)) statisticsQ <- mkFIFO;
	FIFO#(NormSelected) selectedQ <- mkFIFO1;
	FIFO#(NormOperands) operandQ <- mkFIFO1;
	FIFO#(NormMagnitudes) magnitudeQ <- mkFIFO1;
	FIFO#(NormProductMetadata) productMetadataQ <- mkSizedFIFO(8);
	FIFO#(NormRawProducts) rawProductQ <- mkFIFO1;
	FIFO#(NormProducts) productQ <- mkFIFO1;
	FIFO#(NormNumerators) numeratorQ <- mkFIFO1;
	FIFO#(NormRoundDecision) roundDecisionQ <- mkFIFO1;
	FIFO#(Vector#(NormLanes, Int#(8))) roundedQ <- mkFIFO1;
	Vector#(NormLanes, MultiplyIfc) weightedMultiplier <- replicateM(mkMultiply);
	Vector#(NormLanes, MultiplyIfc) biasedMultiplier <- replicateM(mkMultiply);
	Reg#(Vector#(ModelDim, Int#(8))) inputR <- mkRegU;
	Vector#(ModelDim, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	Reg#(Bit#(4)) indexR <- mkRegU;
	Reg#(Int#(13)) sumR <- mkRegU;
	Reg#(Int#(8)) minimumR <- mkRegU;
	Reg#(Int#(8)) maximumR <- mkRegU;
	Reg#(Bit#(5)) channelCnt <- mkReg(0);
	Reg#(Bit#(NormGroupWidth)) groupCnt <- mkReg(0);
	Reg#(Bit#(6)) divideCnt <- mkReg(0);
	Reg#(UInt#(32)) denominatorR <- mkRegU;
	Reg#(Vector#(NormLanes, UInt#(32))) quotientR <- mkRegU;
	Reg#(Vector#(NormLanes, UInt#(32))) remainderR <- mkRegU;
	Reg#(Vector#(NormLanes, Bool)) negativeR <- mkRegU;
	Reg#(Bool) activeOn <- mkReg(False);
	Reg#(Bool) statisticsIssueDone <- mkReg(False);
	Reg#(Bool) statisticsDone <- mkReg(False);
	Reg#(Bool) prepareOn <- mkReg(False);
	Reg#(Bool) divideOn <- mkReg(False);
	Reg#(Bool) roundOn <- mkReg(False);
	Reg#(Bool) collectOn <- mkReg(False);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Retain one token and collect its channel sum, minimum, and maximum.
	//------------------------------------------------------------------------------------
	rule process1 ( !activeOn );
		let value = inputQ.first;
		inputQ.deq;
		inputR <= value.data;
		indexR <= value.index;
		sumR <= 0;
		minimumR <= 127;
		maximumR <= -128;
		channelCnt <= 0;
		groupCnt <= 0;
		statisticsIssueDone <= False;
		statisticsDone <= False;
		activeOn <= True;
	endrule

	rule process2 ( activeOn && !statisticsIssueDone );
		Int#(8) value = inputR[channelCnt];
		Bool lastChannel = channelCnt == fromInteger(valueOf(ModelDim) - 1);
		statisticsQ.enq(tuple2(value, lastChannel));
		if ( lastChannel ) begin
			statisticsIssueDone <= True;
		end else begin
			channelCnt <= channelCnt + 1;
		end
	endrule

	rule process2_1 ( activeOn && !statisticsDone );
		let entry = statisticsQ.first;
		statisticsQ.deq;
		Int#(8) value = tpl_1(entry);
		sumR <= sumR + signExtend(value);
		minimumR <= value < minimumR ? value : minimumR;
		maximumR <= value > maximumR ? value : maximumR;
		if ( tpl_2(entry) ) begin
			statisticsDone <= True;
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Form the exact rational affine expression; no intermediate rounding occurs.
	// Input scale cancels in (20*x-sum)/(20*(max-min)).
	//------------------------------------------------------------------------------------
	rule process3 ( activeOn && statisticsDone && !prepareOn && !divideOn && !roundOn && !collectOn );
		Vector#(NormLanes, Int#(8)) inputs = newVector;
		Vector#(NormLanes, Int#(8)) weights = newVector;
		Vector#(NormLanes, Int#(8)) biases = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			Bit#(6) channel = zeroExtend(groupCnt) * fromInteger(valueOf(NormLanes)) + fromInteger(lane);
			inputs[lane] = inputR[channel];
			weights[lane] = normWeight(blockId, channel);
			biases[lane] = normBias(blockId, channel);
		end
		selectedQ.enq(NormSelected {
			inputs: inputs, weights: weights, biases: biases,
			sum: sumR, minimum: minimumR, maximum: maximumR
		});
		prepareOn <= True;
	endrule

	rule process3_1;
		let value = selectedQ.first;
		selectedQ.deq;
		Int#(9) span = signExtend(value.maximum) - signExtend(value.minimum);
		Int#(14) wideSpan = signExtend(span);
		Int#(14) denominator = (wideSpan << 4) + (wideSpan << 2);
		if ( span == 0 ) begin
			denominator = 1;
		end
		Vector#(NormLanes, Int#(14)) centered = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			Int#(14) inputValue = signExtend(value.inputs[lane]);
			centered[lane] = (inputValue << 4) + (inputValue << 2) - signExtend(value.sum);
		end
		operandQ.enq(NormOperands {
			centered: centered, weights: value.weights, biases: value.biases,
			denominator: denominator
		});
	endrule

	rule process3_2;
		let value = operandQ.first;
		operandQ.deq;
		Vector#(NormLanes, UInt#(14)) centered = newVector;
		Vector#(NormLanes, UInt#(8)) weights = newVector;
		Vector#(NormLanes, UInt#(8)) biases = newVector;
		Vector#(NormLanes, Bool) weightedNegative = newVector;
		Vector#(NormLanes, Bool) biasedNegative = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			Int#(14) inputValue = value.centered[lane];
			Int#(8) weight = value.weights[lane];
			Int#(8) bias = value.biases[lane];
			centered[lane] = unpack(pack(inputValue < 0 ? -inputValue : inputValue));
			weights[lane] = unpack(pack(weight < 0 ? -weight : weight));
			biases[lane] = unpack(pack(bias < 0 ? -bias : bias));
			weightedNegative[lane] = (inputValue < 0) != (weight < 0);
			biasedNegative[lane] = bias < 0;
		end
		magnitudeQ.enq(NormMagnitudes {
			centered: centered, weights: weights, biases: biases,
			weightedNegative: weightedNegative, biasedNegative: biasedNegative,
			denominator: unpack(pack(value.denominator))
		});
	endrule

	// Zero-extended magnitudes fit the registered signed 18-by-18 multiplier.
	// Metadata follows the same ordered, backpressured stream as the DSP results.
	rule process3_2_1;
		let value = magnitudeQ.first;
		magnitudeQ.deq;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			Int#(18) centered = unpack(zeroExtend(pack(value.centered[lane])));
			Int#(18) weight = unpack(zeroExtend(pack(value.weights[lane])));
			Int#(18) denominator = unpack(zeroExtend(pack(value.denominator)));
			Int#(18) bias = unpack(zeroExtend(pack(value.biases[lane])));
			weightedMultiplier[lane].put(centered, weight);
			biasedMultiplier[lane].put(denominator, bias);
		end
		productMetadataQ.enq(NormProductMetadata {
			weightedNegative: value.weightedNegative, biasedNegative: value.biasedNegative,
			denominator: value.denominator
		});
	endrule

	rule process3_2_1_1;
		let metadata = productMetadataQ.first;
		productMetadataQ.deq;
		Vector#(NormLanes, UInt#(22)) weighted = newVector;
		Vector#(NormLanes, UInt#(22)) biased = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			let weightedProduct <- weightedMultiplier[lane].get;
			let biasedProduct <- biasedMultiplier[lane].get;
			weighted[lane] = unpack(truncate(pack(weightedProduct)));
			biased[lane] = unpack(truncate(pack(biasedProduct)));
		end
		rawProductQ.enq(NormRawProducts {
			weighted: weighted, biased: biased,
			weightedNegative: metadata.weightedNegative, biasedNegative: metadata.biasedNegative,
			denominator: metadata.denominator
		});
	endrule

	rule process3_2_2;
		let value = rawProductQ.first;
		rawProductQ.deq;
		Vector#(NormLanes, Int#(22)) weighted = newVector;
		Vector#(NormLanes, Int#(22)) biased = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			Int#(22) weightedMagnitude = unpack(pack(value.weighted[lane]));
			Int#(22) biasedMagnitude = unpack(pack(value.biased[lane]));
			weighted[lane] = value.weightedNegative[lane] ? -weightedMagnitude : weightedMagnitude;
			biased[lane] = value.biasedNegative[lane] ? -biasedMagnitude : biasedMagnitude;
		end
		productQ.enq(NormProducts {
			weighted: weighted, biased: biased, denominator: unpack(pack(value.denominator))
		});
	endrule

	rule process3_3;
		let value = productQ.first;
		productQ.deq;
		Vector#(NormLanes, Int#(32)) numerators = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			numerators[lane] = (signExtend(value.weighted[lane]) << weightShift)
				+ (signExtend(value.biased[lane]) << biasShift);
		end
		UInt#(32) denominator = zeroExtend(unpack(pack(value.denominator))) << outputShift;
		numeratorQ.enq(NormNumerators { numerators: numerators, denominator: denominator });
	endrule

	rule process3_4 ( activeOn && prepareOn && !divideOn && !roundOn && !collectOn );
		let value = numeratorQ.first;
		numeratorQ.deq;
		Vector#(NormLanes, UInt#(32)) numerators = newVector;
		Vector#(NormLanes, Bool) negatives = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			Int#(32) numerator = value.numerators[lane];
			negatives[lane] = numerator < 0;
			numerators[lane] = unpack(pack(numerator < 0 ? -numerator : numerator));
		end
		denominatorR <= value.denominator;
		quotientR <= numerators;
		remainderR <= replicate(0);
		negativeR <= negatives;
		divideCnt <= 0;
		prepareOn <= False;
		divideOn <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// One unsigned restoring divider per lane, one quotient bit per cycle for 32 cycles.
	//------------------------------------------------------------------------------------
	rule process4 ( activeOn && divideOn );
		Vector#(NormLanes, UInt#(32)) quotients = newVector;
		Vector#(NormLanes, UInt#(32)) remainders = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			Bit#(32) dividendBits = pack(quotientR[lane]);
			UInt#(33) trial = (zeroExtend(remainderR[lane]) << 1) | zeroExtend(unpack(dividendBits[31:31]));
			UInt#(32) quotient = quotientR[lane] << 1;
			if ( trial >= zeroExtend(denominatorR) ) begin
				trial = trial - zeroExtend(denominatorR);
				quotient = quotient | 1;
			end
			quotients[lane] = quotient;
			remainders[lane] = truncate(trial);
		end
		quotientR <= quotients;
		remainderR <= remainders;
		if ( divideCnt == 31 ) begin
			divideOn <= False;
			roundOn <= True;
		end else begin
			divideCnt <= divideCnt + 1;
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 4]
	// Round the full rational result once, ties to even, then clamp to signed INT8.
	//------------------------------------------------------------------------------------
	rule process5 ( activeOn && roundOn && !divideOn && !collectOn );
		Vector#(NormLanes, UInt#(8)) quotients = newVector;
		Vector#(NormLanes, Bool) quotientHigh = newVector;
		Vector#(NormLanes, Bool) roundUp = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			UInt#(33) twiceRemainder = zeroExtend(remainderR[lane]) << 1;
			Bit#(32) quotientBits = pack(quotientR[lane]);
			quotients[lane] = unpack(quotientBits[7:0]);
			quotientHigh[lane] = quotientBits[31:8] != 0;
			roundUp[lane] = twiceRemainder > zeroExtend(denominatorR)
				|| (twiceRemainder == zeroExtend(denominatorR) && quotientBits[0] == 1);
		end
		roundDecisionQ.enq(NormRoundDecision {
			quotients: quotients, quotientHigh: quotientHigh,
			roundUp: roundUp, negative: negativeR
		});
		roundOn <= False;
		collectOn <= True;
	endrule

	rule process5_1;
		let value = roundDecisionQ.first;
		roundDecisionQ.deq;
		Vector#(NormLanes, Int#(8)) laneResults = newVector;
		for ( Integer lane = 0; lane < valueOf(NormLanes); lane = lane + 1 ) begin
			UInt#(9) magnitude = zeroExtend(value.quotients[lane]);
			if ( value.roundUp[lane] ) begin
				magnitude = magnitude + 1;
			end
			Int#(8) clipped = value.negative[lane] ? -128 : 127;
			UInt#(9) limit = value.negative[lane] ? 128 : 127;
			// Any nonzero upper quotient bit already exceeds either INT8 limit.
			if ( !value.quotientHigh[lane] && magnitude <= limit ) begin
				Int#(9) signedResult = unpack(pack(magnitude));
				if ( value.negative[lane] ) begin
					signedResult = -signedResult;
				end
				clipped = truncate(signedResult);
			end
			laneResults[lane] = clipped;
		end
		roundedQ.enq(laneResults);
	endrule

	rule process5_2 ( activeOn && collectOn );
		let laneResults = roundedQ.first;
		roundedQ.deq;
		Vector#(ModelDim, Int#(8)) result = newVector;
		for ( Integer channel = 0; channel < valueOf(ModelDim); channel = channel + 1 ) begin
			Integer group = channel / valueOf(NormLanes);
			Integer lane = channel % valueOf(NormLanes);
			if ( groupCnt == fromInteger(group) ) begin
				outputR[channel] <= laneResults[lane];
			end
			// Forward the final group before its register writes take effect.
			if ( group == groupNum - 1 ) begin
				result[channel] = laneResults[lane];
			end else begin
				result[channel] = outputR[channel];
			end
		end
		collectOn <= False;
		if ( groupCnt == fromInteger(groupNum - 1) ) begin
			outputQ.enq(Token { index: indexR, data: result });
			activeOn <= False;
		end else begin
			groupCnt <= groupCnt + 1;
		end
	endrule

	//------------------------------------------------------------------------------------
	// Interface
	//------------------------------------------------------------------------------------
	method Action put(Token#(ModelDim) value);
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(ModelDim)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

endpackage
