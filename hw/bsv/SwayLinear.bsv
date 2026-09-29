package SwayLinear;

import Assert::*;
import FIFO::*;
import Vector::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayMultiply::*;

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
		"Affine LUT-ROM address exceeds thirteen bits");
	staticAssert(inputNum > 0 && inputNum <= valueOf(HeadInputDim) && outputNum > 0 && outputNum <= valueOf(ExpandedDim),
		"Affine dimensions exceed this fixed-model implementation");

	FIFO#(Token#(n)) inputQ <- mkFIFO1;
	FIFO#(Token#(m)) outputQ <- mkFIFO1;
	FIFO#(Tuple3#(Vector#(TDiv#(TAdd#(n, 15), 16), Int#(8)), Bit#(5), Bool)) selectQ <- mkLFIFO;
	FIFO#(Tuple2#(Int#(8), Bool)) bankQ <- mkFIFO;
	// Registered entries cut the sized FIFO's output-bypass mux from the
	// combinational LUT lookup; both operands and metadata remain aligned.
	FIFO#(Tuple3#(Int#(8), Vector#(LinearLanes, Int#(8)), Bool)) operandQ <- mkFIFO;
	FIFO#(Bool) productMetaQ <- mkSizedFIFO(8);
	// Two registered entries sustain one product per cycle while cutting the
	// accumulator's dequeue readiness from DSP-result and metadata controls.
	FIFO#(Tuple2#(Vector#(LinearLanes, Int#(16)), Bool)) productQ <- mkFIFO;
	// Row sums are separated by a complete MAC row. Registered readiness
	// prevents affine backpressure from reaching the row-completion controls.
	FIFO#(Tuple2#(Vector#(LinearLanes, Int#(24)), Bit#(7))) sumQ <- mkFIFO1;
	FIFO#(Tuple3#(Vector#(LinearLanes, Int#(24)), Bit#(7), Bool)) affineQ <- mkFIFO;
	FIFO#(Bool) rowStartQ <- mkFIFO1;
	Vector#(LinearLanes, MultiplyIfc) multipliers <- replicateM(mkMultiply);

	Reg#(Vector#(n, Int#(8))) inputR <- mkRegU;
	Vector#(m, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	Reg#(Vector#(LinearLanes, Int#(24))) sumR <- mkRegU;
	Reg#(Bit#(4)) indexR <- mkRegU;
	Reg#(Bit#(13)) addressR <- mkRegU;
	Reg#(Bit#(9)) inputCnt <- mkReg(0);
	Reg#(Bit#(7)) groupCnt <- mkReg(0);
	// Predecode the terminal row outside the product FIFO retirement controls.
	Reg#(Bool) lastGroupR <- mkReg(groupNum == 1);
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
		addressR <= 0;
		sumR <= replicate(0);
		activeOn <= True;
		issueOn <= True;
	endrule

	// Select within sixteen-column banks before selecting the requested bank.
	// The largest affine input has twenty banks instead of one 320-way mux.
	rule process2 ( activeOn && issueOn );
		Bit#(4) column = truncate(inputCnt);
		Bit#(5) bank = truncate(inputCnt >> 4);
		Vector#(TDiv#(TAdd#(n, 15), 16), Int#(8)) selected = newVector;
		for ( Integer i = 0; i < (inputNum + 15) / 16; i = i + 1 ) begin
			Vector#(16, Int#(8)) values = replicate(0);
			for ( Integer j = 0; j < 16; j = j + 1 ) begin
				if ( i * 16 + j < inputNum ) begin
					values[j] = inputR[i * 16 + j];
				end
			end
			selected[i] = values[column];
		end
		Bool lastInput = inputCnt == fromInteger(inputNum - 1);
		selectQ.enq(tuple3(selected, bank, lastInput));
		if ( lastInput ) begin
			issueOn <= False;
		end else begin
			inputCnt <= inputCnt + 1;
		end
	endrule

	// Register the bank selection before the operand FIFO.
	rule process2_bank ( activeOn );
		let value = selectQ.first;
		selectQ.deq;
		bankQ.enq(tuple2(tpl_1(value)[tpl_2(value)], tpl_3(value)));
	endrule

	// Each lane reads its fixed LUT truth table combinationally. Input, weight
	// and final-column metadata enter the same FIFO only when it can accept them.
	rule process2_1 ( activeOn );
		let value = bankQ.first;
		bankQ.deq;
		Vector#(LinearLanes, Int#(8)) weights = newVector;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			weights[lane] = linearWeight(valueOf(LinearLanes), layerId, rowOffset, outputNum, lane, addressR);
		end
		operandQ.enq(tuple3(tpl_1(value), weights, tpl_2(value)));
		addressR <= addressR + 1;
	endrule

	rule process2_3 ( activeOn );
		let value = operandQ.first;
		operandQ.deq;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			multipliers[lane].put(signExtend(tpl_1(value)), signExtend(tpl_2(value)[lane]));
		end
		productMetaQ.enq(tpl_3(value));
	endrule

	// Each lane's registered DSP retains its result until the ordered collection.
	rule process2_4 ( activeOn );
		let lastInput = productMetaQ.first;
		productMetaQ.deq;
		Vector#(LinearLanes, Int#(16)) products = newVector;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			let product <- multipliers[lane].get;
			products[lane] = truncate(product);
		end
		productQ.enq(tuple2(products, lastInput));
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

	// Register each non-final row restart so MAC readiness cannot reach the
	// input counter or issue-enable registers in the same cycle.
	rule process3Last ( activeOn && !issueOn && tpl_2(productQ.first) );
		let value = productQ.first;
		productQ.deq;
		Vector#(LinearLanes, Int#(24)) sums = newVector;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			sums[lane] = sumR[lane] + signExtend(tpl_1(value)[lane]);
		end
		sumQ.enq(tuple2(sums, groupCnt));
		sumR <= replicate(0);
		if ( !lastGroupR ) begin
			groupCnt <= groupCnt + 1;
			lastGroupR <= groupCnt == fromInteger(groupNum > 1 ? groupNum - 2 : 0);
			rowStartQ.enq(True);
		end else begin
			// sumQ retains the final row index; the next token starts from zero.
			groupCnt <= 0;
			lastGroupR <= (groupNum == 1);
		end
	endrule

	// Only this rule restarts issuance. The guards are disjoint from process1
	// and process2; mkFIFO1 has no bypass from the final-product rule.
	rule processRestart ( activeOn && !issueOn );
		rowStartQ.deq;
		inputCnt <= 0;
		issueOn <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Align product and bias exactly before the requantization stage.
	//------------------------------------------------------------------------------------
	rule process4 ( activeOn );
		let value = sumQ.first;
		sumQ.deq;
		Vector#(LinearLanes, Int#(24)) aligned = newVector;
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			Bit#(9) row = zeroExtend(tpl_2(value)) * fromInteger(valueOf(LinearLanes)) + fromInteger(lane);
			Int#(24) affine = tpl_1(value)[lane];
			affine = affine << (productExp - commonExp);
			if ( layerHasBias(layerId) ) begin
				Int#(24) bias = signExtend(linearBias(layerId, row + fromInteger(rowOffset)));
				affine = affine + (bias << (biasExp - commonExp));
			end
			aligned[lane] = affine;
		end
		// Keep completion metadata with its affine row across downstream stalls.
		Bool lastGroup = tpl_2(value) == fromInteger(groupNum - 1);
		affineQ.enq(tuple3(aligned, tpl_2(value), lastGroup));
	endrule

	// One ties-to-even INT8 requantization follows the registered affine sum.
	rule process4_2 ( activeOn );
		let value = affineQ.first;
		affineQ.deq;
		Vector#(LinearLanes, Int#(8)) laneResults = replicate(0);
		for ( Integer lane = 0; lane < valueOf(LinearLanes); lane = lane + 1 ) begin
			Bit#(9) row = zeroExtend(tpl_2(value)) * fromInteger(valueOf(LinearLanes)) + fromInteger(lane);
			if ( row < fromInteger(outputNum) ) begin
				laneResults[lane] = requantN(tpl_1(value)[lane], commonExp, outputExp);
			end
		end
		Vector#(m, Int#(8)) result = newVector;
		for ( Integer row = 0; row < outputNum; row = row + 1 ) begin
			Integer group = row / valueOf(LinearLanes);
			Integer lane = row % valueOf(LinearLanes);
			if ( tpl_2(value) == fromInteger(group) ) begin
				outputR[row] <= laneResults[lane];
			end
			// The final group enters outputQ in this cycle, before register writes take effect.
			if ( group == groupNum - 1 ) begin
				result[row] = laneResults[lane];
			end else begin
				result[row] = outputR[row];
			end
		end
		if ( tpl_3(value) ) begin
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
