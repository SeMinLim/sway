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

	Bool inputFifo2 = False;
`ifdef SWAY_INPUT_FIFO2
	inputFifo2 = layerId == 1 || layerId == 5;
`endif

	LocalResetIfc localReset <- mkSwayLocalReset;

	FIFO#(Token#(m)) outputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Bit#(4)) startQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Bit#(5)) groupStartQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple2#(Bit#(5), Bool)) groupDoneQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Tuple3#(Bit#(11), Int#(8), Bool)) issueCommandQ;
	if ( inputFifo2 ) begin
		issueCommandQ <- mkSizedFIFO(2, reset_by localReset.rst);
	end else begin
		issueCommandQ <- mkFIFO1(reset_by localReset.rst);
	end
	FIFO#(Tuple3#(Int#(8), Vector#(4, Int#(8)), Bool)) operandQ;
	if ( inputFifo2 ) begin
		operandQ <- mkSizedFIFO(2, reset_by localReset.rst);
	end else begin
		operandQ <- mkFIFO1(reset_by localReset.rst);
	end
	FIFO#(Tuple3#(Vector#(4, Int#(13)), Vector#(4, Int#(12)), Bool)) partialQ;
	if ( inputFifo2 ) begin
		partialQ <- mkSizedFIFO(2, reset_by localReset.rst);
	end else begin
		partialQ <- mkFIFO1(reset_by localReset.rst);
	end
	FIFO#(Tuple2#(Vector#(4, Int#(16)), Bool)) productQ;
	if ( inputFifo2 ) begin
		productQ <- mkSizedFIFO(2, reset_by localReset.rst);
	end else begin
		productQ <- mkFIFO1(reset_by localReset.rst);
	end
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

`ifdef SWAY_BLOCK_PROFILE

	// Simulation-only counters are written by their owning rule.
	// Detailed firings cover frame 8, token 0 after the pipeline has filled.
	Integer profileDetailOrdinal = 128;
	Reg#(UInt#(64)) profileLinearCycleCnt <- mkReg(0);
	Reg#(UInt#(32)) profileCaptureCnt <- mkReg(0);
	Reg#(UInt#(32)) profileStartCnt <- mkReg(0);
	Reg#(UInt#(32)) profileGroupCnt <- mkReg(0);
	Reg#(UInt#(32)) profileChunkCnt <- mkReg(0);
	Reg#(UInt#(32)) profileReadCnt <- mkReg(0);
	Reg#(UInt#(32)) profileDispatchCnt <- mkReg(0);
	Reg#(UInt#(32)) profileResponseCnt <- mkReg(0);
	Reg#(UInt#(32)) profileMultiplyCnt <- mkReg(0);
	Reg#(UInt#(32)) profileCombineCnt <- mkReg(0);
	Reg#(UInt#(32)) profileAccumulateCnt <- mkReg(0);
	Reg#(UInt#(32)) profileLastCnt <- mkReg(0);
	Reg#(UInt#(32)) profileRestartCnt <- mkReg(0);
	Reg#(UInt#(32)) profileBiasCnt <- mkReg(0);
	Reg#(UInt#(32)) profileAddCnt <- mkReg(0);
	Reg#(UInt#(32)) profileRoundCnt <- mkReg(0);
	Reg#(UInt#(32)) profileCollectCnt <- mkReg(0);
	Reg#(UInt#(32)) profileEmitCnt <- mkReg(0);
	Reg#(UInt#(32)) profileGetCnt <- mkReg(0);
	if ( layerId == 1 ) begin
		rule profileLinearCycle;
			profileLinearCycleCnt <= profileLinearCycleCnt + 1;
		endrule
	end
`endif

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// The source backend retains the token and provides one 16-byte chunk per read.
	//------------------------------------------------------------------------------------
	// A retained source token is sampled once while idle. FIFO1 cannot enqueue
	// again on the cycle its pending start is consumed and activeOn is set.
	rule process1Capture ( !activeOn );
		let index <- source.getStart;
		startQ.enq(index);
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileCaptureCnt <= profileCaptureCnt + 1;
			$display("SWAY_LINEAR_EVENT,capture,%0d,%0d,%0d,%0d,%0d",
				profileCaptureCnt, index, profileLinearCycleCnt, -1, -1);
		end
`endif

	endrule

	// Source readiness and last-product backpressure stop at command registers.
	rule process1;
		indexR <= startQ.first;
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileStartCnt <= profileStartCnt + 1;
			$display("SWAY_LINEAR_EVENT,start,%0d,%0d,%0d,%0d,%0d",
				profileStartCnt, startQ.first, profileLinearCycleCnt, -1, -1);
		end
`endif

		startQ.deq;
		addressCnt <= 0;
		groupStartQ.enq(0);
		sumR <= replicate(0);
		activeOn <= True;
	endrule

	rule process1Group;
		let group = groupStartQ.first;
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileGroupCnt <= profileGroupCnt + 1;
			if ( profileGroupCnt % fromInteger(groupNum) == fromInteger(groupNum - 1) ) begin
				$display("SWAY_LINEAR_COUNT,group,%0d,%0d,%0d",
					profileGroupCnt / fromInteger(groupNum), profileLinearCycleCnt, profileGroupCnt + 1);
			end
			if ( profileGroupCnt / fromInteger(groupNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,group,%0d,%0d,%0d,%0d,%0d",
					profileGroupCnt / fromInteger(groupNum), indexR, profileLinearCycleCnt, group, -1);
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileChunkCnt <= profileChunkCnt + 1;
			if ( profileChunkCnt % fromInteger(groupNum * ((inputNum + 15) / 16)) == fromInteger(groupNum * ((inputNum + 15) / 16) - 1) ) begin
				$display("SWAY_LINEAR_COUNT,chunk,%0d,%0d,%0d",
					profileChunkCnt / fromInteger(groupNum * ((inputNum + 15) / 16)), profileLinearCycleCnt, profileChunkCnt + 1);
			end
			if ( profileChunkCnt / fromInteger(groupNum * ((inputNum + 15) / 16)) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,chunk,%0d,%0d,%0d,%0d,%0d",
					profileChunkCnt / fromInteger(groupNum * ((inputNum + 15) / 16)), indexR, profileLinearCycleCnt, groupCnt, chunkCnt);
			end
		end
`endif

		chunkLoadOn <= False;
		issueOn <= True;
	endrule

	// Coefficients and the selected input are registered before multiplication.
	rule process2Read ( issueOn );
		Bit#(4) offset = truncate(inputCnt);
		Bool lastInput = inputCnt == fromInteger(inputNum - 1);
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileReadCnt <= profileReadCnt + 1;
			if ( profileReadCnt % fromInteger(groupNum * inputNum) == fromInteger(groupNum * inputNum - 1) ) begin
				$display("SWAY_LINEAR_COUNT,read,%0d,%0d,%0d",
					profileReadCnt / fromInteger(groupNum * inputNum), profileLinearCycleCnt, profileReadCnt + 1);
			end
			if ( profileReadCnt / fromInteger(groupNum * inputNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,read,%0d,%0d,%0d,%0d,%0d",
					profileReadCnt / fromInteger(groupNum * inputNum), indexR, profileLinearCycleCnt, groupCnt, inputCnt);
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileDispatchCnt <= profileDispatchCnt + 1;
			if ( profileDispatchCnt % fromInteger(groupNum * inputNum) == fromInteger(groupNum * inputNum - 1) ) begin
				$display("SWAY_LINEAR_COUNT,dispatch,%0d,%0d,%0d",
					profileDispatchCnt / fromInteger(groupNum * inputNum), profileLinearCycleCnt, profileDispatchCnt + 1);
			end
			if ( profileDispatchCnt / fromInteger(groupNum * inputNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,dispatch,%0d,%0d,%0d,%0d,%0d",
					profileDispatchCnt / fromInteger(groupNum * inputNum), indexR, profileLinearCycleCnt, tpl_1(command) / fromInteger(inputNum), tpl_1(command) % fromInteger(inputNum));
			end
		end
`endif

		issueCommandQ.deq;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			weightR[lane].request(tpl_1(command));
		end
		requestMetadataQ.enq(tuple2(tpl_2(command), tpl_3(command)));
	endrule

	// All four responses advance together with their queued x/last metadata.
	rule process2Response;
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileResponseCnt <= profileResponseCnt + 1;
			if ( profileResponseCnt % fromInteger(groupNum * inputNum) == fromInteger(groupNum * inputNum - 1) ) begin
				$display("SWAY_LINEAR_COUNT,response,%0d,%0d,%0d",
					profileResponseCnt / fromInteger(groupNum * inputNum), profileLinearCycleCnt, profileResponseCnt + 1);
			end
			if ( profileResponseCnt / fromInteger(groupNum * inputNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,response,%0d,%0d,%0d,%0d,%0d",
					profileResponseCnt / fromInteger(groupNum * inputNum), indexR, profileLinearCycleCnt, (profileResponseCnt / fromInteger(inputNum)) % fromInteger(groupNum), profileResponseCnt % fromInteger(inputNum));
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileMultiplyCnt <= profileMultiplyCnt + 1;
			if ( profileMultiplyCnt % fromInteger(groupNum * inputNum) == fromInteger(groupNum * inputNum - 1) ) begin
				$display("SWAY_LINEAR_COUNT,multiply,%0d,%0d,%0d",
					profileMultiplyCnt / fromInteger(groupNum * inputNum), profileLinearCycleCnt, profileMultiplyCnt + 1);
			end
			if ( profileMultiplyCnt / fromInteger(groupNum * inputNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,multiply,%0d,%0d,%0d,%0d,%0d",
					profileMultiplyCnt / fromInteger(groupNum * inputNum), indexR, profileLinearCycleCnt, (profileMultiplyCnt / fromInteger(inputNum)) % fromInteger(groupNum), profileMultiplyCnt % fromInteger(inputNum));
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileCombineCnt <= profileCombineCnt + 1;
			if ( profileCombineCnt % fromInteger(groupNum * inputNum) == fromInteger(groupNum * inputNum - 1) ) begin
				$display("SWAY_LINEAR_COUNT,combine,%0d,%0d,%0d",
					profileCombineCnt / fromInteger(groupNum * inputNum), profileLinearCycleCnt, profileCombineCnt + 1);
			end
			if ( profileCombineCnt / fromInteger(groupNum * inputNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,combine,%0d,%0d,%0d,%0d,%0d",
					profileCombineCnt / fromInteger(groupNum * inputNum), indexR, profileLinearCycleCnt, (profileCombineCnt / fromInteger(inputNum)) % fromInteger(groupNum), profileCombineCnt % fromInteger(inputNum));
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileAccumulateCnt <= profileAccumulateCnt + 1;
			if ( profileAccumulateCnt % fromInteger(groupNum * (inputNum - 1)) == fromInteger(groupNum * (inputNum - 1) - 1) ) begin
				$display("SWAY_LINEAR_COUNT,accumulate,%0d,%0d,%0d",
					profileAccumulateCnt / fromInteger(groupNum * (inputNum - 1)), profileLinearCycleCnt, profileAccumulateCnt + 1);
			end
			if ( profileAccumulateCnt / fromInteger(groupNum * (inputNum - 1)) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,accumulate,%0d,%0d,%0d,%0d,%0d",
					profileAccumulateCnt / fromInteger(groupNum * (inputNum - 1)), indexR, profileLinearCycleCnt, groupCnt, profileAccumulateCnt % fromInteger(inputNum - 1));
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileLastCnt <= profileLastCnt + 1;
			if ( profileLastCnt % fromInteger(groupNum) == fromInteger(groupNum - 1) ) begin
				$display("SWAY_LINEAR_COUNT,last,%0d,%0d,%0d",
					profileLastCnt / fromInteger(groupNum), profileLinearCycleCnt, profileLastCnt + 1);
			end
			if ( profileLastCnt / fromInteger(groupNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,last,%0d,%0d,%0d,%0d,%0d",
					profileLastCnt / fromInteger(groupNum), indexR, profileLinearCycleCnt, groupCnt, inputNum - 1);
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileRestartCnt <= profileRestartCnt + 1;
			if ( profileRestartCnt / fromInteger(groupNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,restart,%0d,%0d,%0d,%0d,%0d",
					profileRestartCnt / fromInteger(groupNum), indexR, profileLinearCycleCnt, profileRestartCnt % fromInteger(groupNum), -1);
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileBiasCnt <= profileBiasCnt + 1;
			if ( profileBiasCnt / fromInteger(groupNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,bias,%0d,%0d,%0d,%0d,%0d",
					profileBiasCnt / fromInteger(groupNum), indexR, profileLinearCycleCnt, profileBiasCnt % fromInteger(groupNum), -1);
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileAddCnt <= profileAddCnt + 1;
			if ( profileAddCnt / fromInteger(groupNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,add,%0d,%0d,%0d,%0d,%0d",
					profileAddCnt / fromInteger(groupNum), indexR, profileLinearCycleCnt, profileAddCnt % fromInteger(groupNum), -1);
			end
		end
`endif

		let value = biasQ.first;
		biasQ.deq;
		Vector#(4, Int#(32)) affine = newVector;
		for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
			affine[lane] = tpl_1(value)[lane] + tpl_2(value)[lane];
		end
		affineQ.enq(tuple2(affine, tpl_3(value)));
	endrule

	rule process6Round;
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileRoundCnt <= profileRoundCnt + 1;
			if ( profileRoundCnt / fromInteger(groupNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,round,%0d,%0d,%0d,%0d,%0d",
					profileRoundCnt / fromInteger(groupNum), indexR, profileLinearCycleCnt, profileRoundCnt % fromInteger(groupNum), -1);
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileCollectCnt <= profileCollectCnt + 1;
			if ( profileCollectCnt / fromInteger(groupNum) == fromInteger(profileDetailOrdinal) ) begin
				$display("SWAY_LINEAR_EVENT,collect,%0d,%0d,%0d,%0d,%0d",
					profileCollectCnt / fromInteger(groupNum), indexR, profileLinearCycleCnt, profileCollectCnt % fromInteger(groupNum), -1);
			end
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileEmitCnt <= profileEmitCnt + 1;
			$display("SWAY_LINEAR_EVENT,emit,%0d,%0d,%0d,%0d,%0d",
				profileEmitCnt, indexR, profileLinearCycleCnt, -1, -1);
		end
`endif

		outputQ.enq(Token { index: indexR, data: readVReg(outputR) });
		source.finish;
		emitOn <= False;
		activeOn <= False;
	endrule

	method ActionValue#(Token#(m)) get;
		let value = outputQ.first;
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profileGetCnt <= profileGetCnt + 1;
			$display("SWAY_LINEAR_EVENT,get,%0d,%0d,%0d,%0d,%0d",
				profileGetCnt, value.index, profileLinearCycleCnt, -1, -1);
		end
`endif

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
`ifdef SWAY_BLOCK_PROFILE
	Reg#(UInt#(64)) profileLinearPutCycleCnt <- mkReg(0);
	Reg#(UInt#(32)) profilePutCnt <- mkReg(0);
	if ( layerId == 1 ) begin
		rule profileLinearPutCycle;
			profileLinearPutCycleCnt <= profileLinearPutCycleCnt + 1;
		endrule
	end
`endif


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
`ifdef SWAY_BLOCK_PROFILE
		if ( layerId == 1 ) begin
			profilePutCnt <= profilePutCnt + 1;
			$display("SWAY_LINEAR_EVENT,put,%0d,%0d,%0d,%0d,%0d",
				profilePutCnt, value.index, profileLinearPutCycleCnt, -1, -1);
		end
`endif

	endmethod

	method ActionValue#(Token#(m)) get;
		let value <- engine.get;
		return value;
	endmethod
endmodule

endpackage
