package SwayLinear;

import Assert::*;
import FIFO::*;
import Vector::*;

import SwayTypes::*;
import SwayParameters::*;

module mkSwayLinearSlice#(Integer layerId, Integer rowOffset)(LinearIfc#(n, m));
	staticAssert(valueOf(ParallelismDivisor) == 1 || valueOf(ParallelismDivisor) == 2 || valueOf(ParallelismDivisor) == 4,
		"ParallelismDivisor must be 1, 2, or 4");
	Integer inputNum = valueOf(n);
	Integer outputNum = valueOf(m);
	Integer groupNum = (outputNum + valueOf(LinearLanes) - 1) / valueOf(LinearLanes);
	Integer productExp = layerInputScale(layerId) + layerWeightScale(layerId);
	Integer biasExp = layerBiasScale(layerId);
	Integer commonExp = layerHasBias(layerId) && biasExp < productExp ? biasExp : productExp;
	Integer outputExp = layerOutputScale(layerId);
	Integer productBound = inputNum * 16384;
	Integer affineBound = productBound * (2 ** (productExp - commonExp));
	if ( layerHasBias(layerId) ) begin
		affineBound = affineBound + 128 * (2 ** (biasExp - commonExp));
	end
	staticAssert(affineBound < 8388608, "Affine alignment exceeds the proven INT24 bound");
	staticAssert(inputNum == layerInputSize(layerId) && rowOffset >= 0 && rowOffset + outputNum <= layerOutputSize(layerId),
		"Affine slice is outside the frozen parameter matrix");
	staticAssert(layerSliceSupported(layerId, rowOffset, outputNum), "Affine slice has no generated weight bank");
	staticAssert(groupNum * inputNum <= 8192,
		"Affine ROM address exceeds thirteen bits");
	staticAssert(inputNum > 0 && inputNum <= valueOf(HeadInputDim) && outputNum > 0 && outputNum <= valueOf(ExpandedDim),
		"Affine dimensions exceed this fixed-model implementation");

	FIFO#(Token#(n)) inputQ <- mkFIFO1;
	FIFO#(Token#(m)) outputQ <- mkFIFO1;
	FIFO#(Tuple2#(Vector#(LinearLanes, Int#(16)), Bool)) productQ <- mkFIFO;
	FIFO#(Tuple2#(Vector#(LinearLanes, Int#(24)), Bit#(7))) sumQ <- mkFIFO;

	Reg#(Vector#(n, Int#(8))) inputR <- mkReg(replicate(0));
	Reg#(Vector#(m, Int#(8))) outputR <- mkReg(replicate(0));
	Reg#(Vector#(LinearLanes, Int#(24))) sumR <- mkReg(replicate(0));
	Reg#(Bit#(4)) indexR <- mkReg(0);
	Reg#(Bit#(9)) inputCnt <- mkReg(0);
	Reg#(Bit#(7)) groupCnt <- mkReg(0);
	Reg#(Bool) activeOn <- mkReg(False);
	Reg#(Bool) issueOn <- mkReg(False);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Retain one token while the selected output lanes visit each row group.
	//------------------------------------------------------------------------------------
	rule process1 ( !activeOn );
		let value = inputQ.first;
		inputQ.deq;
		inputR <= value.data;
		indexR <= value.index;
		inputCnt <= 0;
		groupCnt <= 0;
		sumR <= replicate(0);
		activeOn <= True;
		issueOn <= True;
	endrule

	// Each lane owns one fixed weight ROM. The product FIFO is the multiplier register.
	rule process2 ( activeOn && issueOn );
		Bit#(13) address = zeroExtend(groupCnt) * fromInteger(inputNum) + zeroExtend(inputCnt);
		Int#(8) inputValue = inputR[inputCnt];
		Vector#(LinearLanes, Int#(16)) products = newVector;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			Int#(8) weight = linearWeight(valueOf(LinearLanes), layerId, rowOffset, outputNum, lane, address);
			products[lane] = signExtend(inputValue) * signExtend(weight);
		end
		Bool lastInput = inputCnt == fromInteger(inputNum - 1);
		productQ.enq(tuple2(products, lastInput));
		if ( lastInput ) begin
			issueOn <= False;
		end else begin
			inputCnt <= inputCnt + 1;
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// Accumulate all products, including the final queued product, before advancing.
	// At most 320 signed INT8 products have magnitude 5242880, within INT24.
	//------------------------------------------------------------------------------------
	rule process3 ( activeOn && !tpl_2(productQ.first) );
		let value = productQ.first;
		productQ.deq;
		Vector#(LinearLanes, Int#(24)) sums = newVector;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			sums[lane] = sumR[lane] + signExtend(tpl_1(value)[lane]);
		end
		sumR <= sums;
	endrule

	// Only the final-product rule restarts the issuer. Its !issueOn guard makes
	// this control update disjoint from process2; ordinary MACs overlap issue.
	rule process3Last ( activeOn && !issueOn && tpl_2(productQ.first) );
		let value = productQ.first;
		productQ.deq;
		Vector#(LinearLanes, Int#(24)) sums = newVector;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			sums[lane] = sumR[lane] + signExtend(tpl_1(value)[lane]);
		end
		sumQ.enq(tuple2(sums, groupCnt));
		sumR <= replicate(0);
		if ( groupCnt + 1 < fromInteger(groupNum) ) begin
			groupCnt <= groupCnt + 1;
			inputCnt <= 0;
			issueOn <= True;
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Align product and bias exactly, then perform one ties-to-even INT8 requantization.
	//------------------------------------------------------------------------------------
	rule process4 ( activeOn );
		let value = sumQ.first;
		sumQ.deq;
		Vector#(m, Int#(8)) result = outputR;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			Bit#(9) row = zeroExtend(tpl_2(value)) * fromInteger(valueOf(LinearLanes)) + fromInteger(lane);
			Int#(24) affine = tpl_1(value)[lane];
			affine = affine << (productExp - commonExp);
			if ( layerHasBias(layerId) ) begin
				Int#(24) bias = signExtend(linearBias(layerId, row + fromInteger(rowOffset)));
				affine = affine + (bias << (biasExp - commonExp));
			end
			if ( row < fromInteger(outputNum) ) begin
				result[row] = requantN(affine, commonExp, outputExp);
			end
		end
		outputR <= result;
		if ( tpl_2(value) == fromInteger(groupNum - 1) ) begin
			outputQ.enq(Token { index: indexR, data: result });
			activeOn <= False;
		end
	endrule

	//------------------------------------------------------------------------------------
	// Interface
	//------------------------------------------------------------------------------------
	method Action put(Token#(n) value);
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(m)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

// Whole-matrix callers keep their original interface and parameter identity.
module mkSwayLinear#(Integer layerId)(LinearIfc#(n, m));
	let engine <- mkSwayLinearSlice(layerId, 0);
	return engine;
endmodule

endpackage
