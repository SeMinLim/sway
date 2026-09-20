package SwayLinear;

import Assert::*;
import FIFO::*;
import SwayCoeffRom::*;
import Vector::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayReset::*;

interface LinearSourceIfc;
	method ActionValue#(Bit#(4)) getStart;
	method Vector#(16, Int#(8)) readChunk(Bit#(5) chunk);
	method Action finish;
endinterface

interface LinearEngineIfc#(numeric type m);
	method ActionValue#(Token#(m)) get;
endinterface

module mkSwayLinearEngine#(Integer layerId, Integer inputNum, LinearSourceIfc source)(LinearEngineIfc#(m));
	staticAssert(valueOf(LinearLanes) == 4, "Generated weight banks require four linear lanes");
	Integer outputNum = valueOf(m);
	Integer groupNum = (outputNum + 3) / 4;
	Integer productExp = layerInputScale(layerId) + layerWeightScale(layerId);
	Integer biasExp = layerBiasScale(layerId);
	Integer commonExp = layerHasBias(layerId) && biasExp < productExp ? biasExp : productExp;
	Integer outputExp = layerOutputScale(layerId);
	staticAssert(inputNum > 0 && inputNum <= 320 && outputNum > 0 && outputNum <= 80,
		"Affine dimensions exceed this fixed-model implementation");
	staticAssert(productExp - commonExp <= 6 && (!layerHasBias(layerId) || biasExp - commonExp <= 20),
		"Affine aligned sum exceeds the signed 32-bit accumulator bound");

	LocalResetIfc localReset <- mkSwayLocalReset;

	FIFO#(Token#(m)) outputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Bit#(4)) startQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Bit#(5)) groupStartQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Bit#(5), Bool)) groupDoneQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple3#(Bit#(11), Int#(8), Bool)) issueCommandQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple3#(Int#(8), Vector#(4, Int#(8)), Bool)) operandQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple3#(Vector#(4, Int#(13)), Vector#(4, Int#(12)), Bool)) partialQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(16)), Bool)) productQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(24)), Bit#(5))) sumQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple3#(Vector#(4, Int#(32)), Vector#(4, Int#(32)), Bit#(5))) biasQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(32)), Bit#(5))) affineQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Vector#(4, Int#(8)), Bit#(5))) resultQ <- mkFIFO1(reset_by localReset.rst);

	// The metadata slot reserves the selected input and final-product marker
	// for the same four atomic coefficient requests, including native ROM latency.
	FIFO#(Tuple2#(Int#(8), Bool)) requestMetadataQ <- mkSizedFIFO(4, reset_by localReset.rst);
	Vector#(4, SwayCoeffRomIfc) weightR = newVector;
	for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
		weightR[lane] <- mkSwayCoeffRom(layerId, lane, reset_by localReset.rst);
	end
	// Validity flags and FIFO occupancy reset; each data register is written before use.
	Reg#(Vector#(16, Int#(8))) chunkR <- mkRegU;
	Vector#(m, Reg#(Int#(8))) outputR <- replicateM(mkRegU);
	Reg#(Vector#(4, Int#(24))) sumR <- mkRegU;
	Reg#(Bit#(4)) indexR <- mkRegU;
	Reg#(Bit#(9)) inputCnt <- mkRegU;
	Reg#(Bit#(13)) addressCnt <- mkRegU;
	Reg#(Bit#(5)) chunkCnt <- mkRegU;
	Reg#(Bit#(5)) groupCnt <- mkRegU;
	Reg#(Bool) hasNextGroupR <- mkRegU;
	Reg#(Bool) activeOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) chunkLoadOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) issueOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) emitOn <- mkReg(False, reset_by localReset.rst);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// The source backend retains the token and provides one 16-byte chunk per read.
	//------------------------------------------------------------------------------------
	// A retained source token is sampled once while idle. FIFO1 cannot enqueue
	// again on the cycle its pending start is consumed and activeOn is set.
	rule process1Capture ( !activeOn );
		let index <- source.getStart;
		startQ.enq(index);
	endrule

	// Source readiness and last-product backpressure stop at command registers.
	rule process1;
		indexR <= startQ.first;
		startQ.deq;
		addressCnt <= 0;
		groupStartQ.enq(0);
		sumR <= replicate(0);
		activeOn <= True;
	endrule

	rule process1Group;
		let group = groupStartQ.first;
		groupCnt <= group;
		hasNextGroupR <= group != fromInteger(groupNum - 1);
		groupStartQ.deq;
		inputCnt <= 0;
		chunkCnt <= 0;
		chunkLoadOn <= True;
	endrule

	// chunkLoadOn and issueOn are mutually exclusive throughout a group.
	rule process2Chunk ( chunkLoadOn );
		chunkR <= source.readChunk(chunkCnt);
		chunkLoadOn <= False;
		issueOn <= True;
	endrule

	// Coefficients and the selected input are registered before multiplication.
	rule process2Read ( issueOn );
		Bit#(4) offset = truncate(inputCnt);
		Bool lastInput = inputCnt == fromInteger(inputNum - 1);
		issueCommandQ.enq(tuple3(truncate(addressCnt), chunkR[offset], lastInput));
		addressCnt <= addressCnt + 1;
		if ( lastInput ) begin
			issueOn <= False;
		end else begin
			inputCnt <= inputCnt + 1;
			if ( offset == 15 ) begin
				chunkCnt <= chunkCnt + 1;
				chunkLoadOn <= True;
				issueOn <= False;
			end
		end
	endrule

	// Capacity checks for all four ROMs and metadata affect only this dequeue.
	// The selected x and last flag remain stable even if the next chunk loads.
	rule process2Dispatch;
		let command = issueCommandQ.first;
		issueCommandQ.deq;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			weightR[lane].request(tpl_1(command));
		end
		requestMetadataQ.enq(tuple2(tpl_2(command), tpl_3(command)));
	endrule

	// All four responses advance together with their queued x/last metadata.
	rule process2Response;
		Vector#(4, Int#(8)) weights = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			let weight <- weightR[lane].response;
			weights[lane] = weight;
		end
		let metadata = requestMetadataQ.first;
		requestMetadataQ.deq;
		operandQ.enq(tuple3(tpl_1(metadata), weights, tpl_2(metadata)));
	endrule

	rule process2Multiply;
		let value = operandQ.first;
		operandQ.deq;
		Bit#(8) inputBits = pack(tpl_1(value));
		Int#(5) lowInput = unpack(zeroExtend(inputBits[3:0]));
		Int#(4) highInput = unpack(inputBits[7:4]);
		Vector#(4, Int#(13)) lowProducts = newVector;
		Vector#(4, Int#(12)) highProducts = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			lowProducts[lane] = signExtend(lowInput) * signExtend(tpl_2(value)[lane]);
			highProducts[lane] = signExtend(highInput) * signExtend(tpl_2(value)[lane]);
		end
		partialQ.enq(tuple3(lowProducts, highProducts, tpl_3(value)));
	endrule

	// x = unsigned(x[3:0]) + 16 * signed(x[7:4]), including negative INT8 x.
	// Register the two small products before the exact signed 16-bit addition.
	rule process2Combine;
		let value = partialQ.first;
		partialQ.deq;
		Vector#(4, Int#(16)) products = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			Int#(16) highProduct = signExtend(tpl_2(value)[lane]);
			products[lane] = signExtend(tpl_1(value)[lane]) + (highProduct << 4);
		end
		productQ.enq(tuple2(products, tpl_3(value)));
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// 320 signed INT8 products have absolute sum <=5242880, fitting signed 24 bits.
	//------------------------------------------------------------------------------------
	rule process3 ( !tpl_2(productQ.first) );
		let value = productQ.first;
		productQ.deq;
		Vector#(4, Int#(24)) sums = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			sums[lane] = sumR[lane] + signExtend(tpl_1(value)[lane]);
		end
		sumR <= sums;
	endrule

	// A returned last product proves the complete group was issued and drained.
	rule process3Last ( tpl_2(productQ.first) );
		let value = productQ.first;
		productQ.deq;
		Vector#(4, Int#(24)) sums = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			sums[lane] = sumR[lane] + signExtend(tpl_1(value)[lane]);
		end
		sumQ.enq(tuple2(sums, groupCnt));
		sumR <= replicate(0);
		groupDoneQ.enq(tuple2(groupCnt + 1, hasNextGroupR));
	endrule

	// Group comparison is registered when the group starts. Neither it nor
	// start-command arbitration lies on the final accumulation/clear rule.
	rule process3Restart;
		let completed = groupDoneQ.first;
		groupDoneQ.deq;
		if ( tpl_2(completed) ) begin
			groupStartQ.enq(tpl_1(completed));
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3]
	// Bias read, aligned addition, and final INT8 rounding have separate registers.
	//------------------------------------------------------------------------------------
	rule process4Bias;
		let value = sumQ.first;
		sumQ.deq;
		Vector#(4, Int#(32)) sums = newVector;
		Vector#(4, Int#(32)) biases = replicate(0);
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			Bit#(9) row = zeroExtend(tpl_2(value)) * 4 + fromInteger(lane);
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
		Vector#(4, Int#(32)) affine = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			affine[lane] = tpl_1(value)[lane] + tpl_2(value)[lane];
		end
		affineQ.enq(tuple2(affine, tpl_3(value)));
	endrule

	rule process6Round;
		let value = affineQ.first;
		affineQ.deq;
		Vector#(4, Int#(8)) result = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			result[lane] = requant(signExtend(tpl_1(value)[lane]), commonExp, outputExp);
		end
		resultQ.enq(tuple2(result, tpl_2(value)));
	endrule

	// Static row enables replace four cascaded dynamic writes to one vector register.
	rule process7Collect ( activeOn && !emitOn );
		let value = resultQ.first;
		resultQ.deq;
		for ( Integer row = 0; row < outputNum; row = row + 1 ) begin
			if ( tpl_2(value) == fromInteger(row / 4) ) begin
				outputR[row] <= tpl_1(value)[row % 4];
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

// The ordinary affine wrapper retains a single wide token. The engine and its
// coefficient/MAC/rounding pipeline are also used by the streamed head wrapper.
module mkSwayLinear#(Integer layerId)(LinearIfc#(n, m));
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

	// The engine derives its own leaf from root reset, with the same release delay.
	LinearEngineIfc#(m) engine <- mkSwayLinearEngine(layerId, inputNum, source);

	method Action put(Token#(n) value) if ( localReset.ready );
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(m)) get;
		let value <- engine.get;
		return value;
	endmethod
endmodule

endpackage
