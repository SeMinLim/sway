package SwayFoldedLinear;

import Assert::*;
import FIFO::*;
import Vector::*;

import SwayCoeffRom::*;
import SwayLinear::*;
import SwayParameters::*;
import SwayReset::*;
import SwayTypes::*;

// Conventional per-layer engine with a compile-time one- or two-lane allocation.
// The four original ROM banks and token boundary are unchanged. Each subgroup
// selects consecutive physical banks; only laneNum arithmetic lanes elaborate.
module mkSwayFoldedLinearEngine#(Integer layerId, Integer inputNum, Integer laneNum,
	LinearSourceIfc source)(LinearEngineIfc#(m));
	Integer outputNum = valueOf(m);
	Integer groupNum = (outputNum + laneNum - 1) / laneNum;
	Integer subgroupNum = 4 / laneNum;
	Integer productExp = layerInputScale(layerId) + layerWeightScale(layerId);
	Integer biasExp = layerBiasScale(layerId);
	Integer commonExp = layerHasBias(layerId) && biasExp < productExp ? biasExp : productExp;
	Integer outputExp = layerOutputScale(layerId);
	staticAssert(laneNum == 1 || laneNum == 2, "Folded engine requires one or two lanes");
	staticAssert(inputNum > 0 && inputNum <= 320 && outputNum > 0 && outputNum <= 80,
		"Affine dimensions exceed this fixed-model implementation");
	staticAssert(productExp - commonExp <= 6 && (!layerHasBias(layerId) || biasExp - commonExp <= 20),
		"Affine aligned sum exceeds the signed 32-bit accumulator bound");

	LocalResetIfc localReset <- mkSwayLocalReset;
	FIFO#(Token#(m)) outputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Bit#(4)) startQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Bit#(7)) groupStartQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple4#(Bit#(11), Int#(8), Bool, Bit#(2))) issueCommandQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(Tuple3#(Int#(8), Vector#(4, Int#(8)), Bool)) operandQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(Tuple3#(Vector#(4, Int#(13)), Vector#(4, Int#(12)), Bool)) partialQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(16)), Bool)) productQ <- mkSizedFIFO(2, reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(24)), Bit#(7))) sumQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple3#(Vector#(4, Int#(32)), Vector#(4, Int#(32)), Bit#(7))) biasQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(32)), Bit#(7))) affineQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(8)), Bit#(7))) resultQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple3#(Int#(8), Bool, Bit#(2))) requestMetadataQ <- mkSizedFIFO(4, reset_by localReset.rst);
	Vector#(4, SwayCoeffRomIfc) weightR = newVector;
	for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
		weightR[lane] <- mkSwayCoeffRom(layerId, lane, reset_by localReset.rst);
	end

	Reg#(Vector#(16, Int#(8))) chunkR <- mkRegU;
	Vector#(m, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	Reg#(Vector#(4, Int#(24))) sumR <- mkRegU;
	Reg#(Bit#(4)) indexR <- mkRegU;
	Reg#(Bit#(9)) inputCnt <- mkRegU;
	Reg#(Bit#(11)) addressCnt <- mkRegU;
	Reg#(Bit#(5)) chunkCnt <- mkRegU;
	Reg#(Bit#(7)) groupCnt <- mkRegU;
	Reg#(Bit#(7)) completionGroupCnt <- mkRegU;
	Reg#(Bit#(2)) bankOffsetR <- mkRegU;
	Reg#(Bool) hasNextGroupR <- mkRegU;
	Reg#(Bool) activeOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) chunkLoadOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) issueOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) emitOn <- mkReg(False, reset_by localReset.rst);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Retain one token; start the next output subgroup before the previous drains.
	//------------------------------------------------------------------------------------
	rule process1Capture ( !activeOn );
		let index <- source.getStart;
		startQ.enq(index);
	endrule

	rule process1 ( !activeOn );
		indexR <= startQ.first;
		startQ.deq;
		groupStartQ.enq(0);
		completionGroupCnt <= 0;
		sumR <= replicate(0);
		activeOn <= True;
	endrule

	rule process1Group ( activeOn && !issueOn && !chunkLoadOn );
		let group = groupStartQ.first;
		groupStartQ.deq;
		groupCnt <= group;
		hasNextGroupR <= group != fromInteger(groupNum - 1);
		Bit#(11) physicalGroup = zeroExtend(group / fromInteger(subgroupNum));
		addressCnt <= physicalGroup * fromInteger(inputNum);
		bankOffsetR <= truncate(group * fromInteger(laneNum));
		inputCnt <= 0;
		chunkCnt <= 0;
		chunkLoadOn <= True;
	endrule

	rule process2Chunk ( chunkLoadOn );
		chunkR <= source.readChunk(chunkCnt);
		chunkLoadOn <= False;
		issueOn <= True;
	endrule

	rule process2Read ( issueOn );
		Bit#(4) offset = truncate(inputCnt);
		Bool lastInput = inputCnt == fromInteger(inputNum - 1);
		issueCommandQ.enq(tuple4(addressCnt, chunkR[offset], lastInput, bankOffsetR));
		addressCnt <= addressCnt + 1;
		if ( lastInput ) begin
			issueOn <= False;
			if ( hasNextGroupR ) begin
				groupStartQ.enq(groupCnt + 1);
			end
		end else begin
			inputCnt <= inputCnt + 1;
			if ( offset == 15 ) begin
				chunkCnt <= chunkCnt + 1;
				chunkLoadOn <= True;
				issueOn <= False;
			end
		end
	endrule

	// Each request retains the bank selection with its input and last marker.
	// All four existing banks are read together, preserving their native latency.
	rule process2Dispatch;
		let command = issueCommandQ.first;
		issueCommandQ.deq;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			weightR[lane].request(tpl_1(command));
		end
		requestMetadataQ.enq(tuple3(tpl_2(command), tpl_3(command), tpl_4(command)));
	endrule

	rule process2Response;
		Vector#(4, Int#(8)) weights = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			let weight <- weightR[lane].response;
			weights[lane] = weight;
		end
		let metadata = requestMetadataQ.first;
		requestMetadataQ.deq;
		Vector#(4, Int#(8)) selected = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			Bit#(2) bank = tpl_3(metadata) + fromInteger(lane);
			selected[lane] = weights[bank];
		end
		operandQ.enq(tuple3(tpl_1(metadata), selected, tpl_2(metadata)));
	endrule

	rule process2Multiply;
		let value = operandQ.first;
		operandQ.deq;
		Bit#(8) inputBits = pack(tpl_1(value));
		Int#(5) lowInput = unpack(zeroExtend(inputBits[3:0]));
		Int#(4) highInput = unpack(inputBits[7:4]);
		Vector#(4, Int#(13)) lowProducts = replicate(0);
		Vector#(4, Int#(12)) highProducts = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			lowProducts[lane] = signExtend(lowInput) * signExtend(tpl_2(value)[lane]);
			highProducts[lane] = signExtend(highInput) * signExtend(tpl_2(value)[lane]);
		end
		partialQ.enq(tuple3(lowProducts, highProducts, tpl_3(value)));
	endrule

	rule process2Combine;
		let value = partialQ.first;
		partialQ.deq;
		Vector#(4, Int#(16)) products = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			Int#(16) highProduct = signExtend(tpl_2(value)[lane]);
			products[lane] = signExtend(tpl_1(value)[lane]) + (highProduct << 4);
		end
		productQ.enq(tuple2(products, tpl_3(value)));
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// In-order products determine completion independently of the issue group.
	//------------------------------------------------------------------------------------
	rule process3 ( activeOn && !tpl_2(productQ.first) );
		let value = productQ.first;
		productQ.deq;
		Vector#(4, Int#(24)) sums = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			sums[lane] = sumR[lane] + signExtend(tpl_1(value)[lane]);
		end
		sumR <= sums;
	endrule

	rule process3Last ( activeOn && tpl_2(productQ.first) );
		let value = productQ.first;
		productQ.deq;
		Vector#(4, Int#(24)) sums = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			sums[lane] = sumR[lane] + signExtend(tpl_1(value)[lane]);
		end
		sumQ.enq(tuple2(sums, completionGroupCnt));
		sumR <= replicate(0);
		completionGroupCnt <= completionGroupCnt + 1;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Preserve the original bias alignment, ties-to-even rounding, and saturation.
	//------------------------------------------------------------------------------------
	rule process4Bias;
		let value = sumQ.first;
		sumQ.deq;
		Vector#(4, Int#(32)) sums = replicate(0);
		Vector#(4, Int#(32)) biases = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			Bit#(9) row = zeroExtend(tpl_2(value)) * fromInteger(laneNum) + fromInteger(lane);
			sums[lane] = signExtend(tpl_1(value)[lane]) << (productExp - commonExp);
			if ( layerHasBias(layerId) ) begin
				biases[lane] = signExtend(linearBias(layerId, row)) << (biasExp - commonExp);
			end
		end
		biasQ.enq(tuple3(sums, biases, tpl_2(value)));
	endrule

	rule process5Add;
		let value = biasQ.first;
		biasQ.deq;
		Vector#(4, Int#(32)) affine = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			affine[lane] = tpl_1(value)[lane] + tpl_2(value)[lane];
		end
		affineQ.enq(tuple2(affine, tpl_3(value)));
	endrule

	rule process6Round;
		let value = affineQ.first;
		affineQ.deq;
		Vector#(4, Int#(8)) result = replicate(0);
		for ( Integer lane = 0; lane < laneNum; lane = lane + 1 ) begin
			result[lane] = requant(signExtend(tpl_1(value)[lane]), commonExp, outputExp);
		end
		resultQ.enq(tuple2(result, tpl_2(value)));
	endrule

	rule process7Collect ( activeOn && !emitOn );
		let value = resultQ.first;
		resultQ.deq;
		for ( Integer row = 0; row < outputNum; row = row + 1 ) begin
			if ( tpl_2(value) == fromInteger(row / laneNum) ) begin
				outputR[row] <= tpl_1(value)[row % laneNum];
			end
		end
		if ( tpl_2(value) == fromInteger(groupNum - 1) ) begin
			emitOn <= True;
		end
	endrule

	rule process8Emit ( activeOn && emitOn );
		outputQ.enq(Token { index: indexR, data: readVReg(outputR) });
		source.finish;
		emitOn <= False;
		activeOn <= False;
	endrule

	method ActionValue#(Token#(m)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

module mkSwayFoldedLinear#(Integer layerId, Integer laneNum)(LinearIfc#(n, m));
	Integer inputNum = valueOf(n);
	Integer chunkNum = (inputNum + 15) / 16;
	LocalResetIfc localReset <- mkSwayLocalReset;
	FIFO#(Token#(n)) inputQ <- mkFIFO1(reset_by localReset.rst);

	LinearSourceIfc source = interface LinearSourceIfc;
		method ActionValue#(Bit#(4)) getStart;
			return inputQ.first.index;
		endmethod

		method Vector#(16, Int#(8)) readChunk(Bit#(5) chunkIndex);
			Vector#(16, Int#(8)) values = replicate(0);
			for ( Integer chunk = 0; chunk < chunkNum; chunk = chunk + 1 ) begin
				if ( chunkIndex == fromInteger(chunk) ) begin
					for ( Integer lane = 0; lane < 16; lane = lane + 1 ) begin
						if ( chunk * 16 + lane < inputNum ) begin
							values[lane] = inputQ.first.data[chunk * 16 + lane];
						end
					end
				end
			end
			return values;
		endmethod

		method Action finish;
			inputQ.deq;
		endmethod
	endinterface;

	LinearEngineIfc#(m) engine <- mkSwayFoldedLinearEngine(layerId, inputNum, laneNum, source);

	method Action put(Token#(n) value) if ( localReset.ready );
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(m)) get;
		let value <- engine.get;
		return value;
	endmethod
endmodule

endpackage
