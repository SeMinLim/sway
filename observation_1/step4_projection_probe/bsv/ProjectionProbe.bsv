package ProjectionProbe;

import BRAM::*;
import GetPut::*;
import FIFOF::*;
import Vector::*;

import QuantTypes::*;
import QuantMultiplier::*;
import Reciprocal::*;

interface ProjectionProbeIfc;
	method Action loadInput(Bit#(10) address, Vector#(LaneNum, Bit#(32)) data);
	method Action loadWeight(Bit#(10) address, WeightVec data);
	method Action configureSmoothing(Vector#(LaneNum, Bit#(32)) data);
	method Action start;
	method ActionValue#(PartialResult) getPartial;
	method Action retire(TokenTag tag);
endinterface

// Diagnostic slice only: input SRAM -> quantization -> 4x4 APoT partial sums.
// Four tiles implement the fixed 4->16 delta projection, seven input tokens.
// Downstream weight-scale / bias / softplus / output writes are not implemented.
(* synthesize *)
module mkProjectionProbe(ProjectionProbeIfc);
	BRAM_Configure sramCfg = defaultValue;
	sramCfg.memorySize = 1024;
	sramCfg.latency = 1;
	sramCfg.outFIFODepth = 2;

	Vector#(LaneNum, BRAM2Port#(Bit#(10), Bit#(32))) inputSram
		<- replicateM(mkBRAM2Server(sramCfg));
	Vector#(LaneNum, BRAM2Port#(Bit#(10), Bit#(16))) weightSram
		<- replicateM(mkBRAM2Server(sramCfg));
	QuantMultiplierIfc quantMultiplier <- mkQuantMultiplier;
	ReciprocalIfc reciprocal <- mkReciprocal;

	FIFOF#(Bit#(1)) freeSlotQ <- mkSizedFIFOF(2);
	FIFOF#(TokenTag) inputTagQ <- mkSizedFIFOF(2);
	FIFOF#(MultiplyResult) magnitudeQ <- mkSizedFIFOF(2);
	FIFOF#(Tuple2#(TokenTag, Int#(32))) scaleQ <- mkSizedFIFOF(2);
	FIFOF#(ReciprocalResult) quantRequestQ <- mkSizedFIFOF(2);
	FIFOF#(QuantToken) quantizedQ <- mkSizedFIFOF(2);
	FIFOF#(TileTag) tileQ <- mkSizedFIFOF(2);
	FIFOF#(DotPairs) dotPairsQ <- mkSizedFIFOF(2);
	FIFOF#(PartialResult) partialQ <- mkSizedFIFOF(2);

	Vector#(2, Reg#(FeatureVec)) smoothedBuffer <- replicateM(mkRegU);
	Vector#(2, Reg#(Bit#(32))) scaleR <- replicateM(mkRegU);
	Vector#(2, Reg#(Bit#(32))) reciprocalR <- replicateM(mkRegU);
	Vector#(2, Reg#(Bool)) zeroScaleR <- replicateM(mkReg(False));
	Reg#(Vector#(LaneNum, Bit#(32))) smoothingR <- mkReg(replicate(67108864));

	Reg#(Bit#(2)) initCnt <- mkReg(0);
	Reg#(Bool) startedDone <- mkReg(False);
	Reg#(Bool) inputOn <- mkReg(False);
	Reg#(Bit#(4)) inputCnt <- mkReg(0);
	Reg#(Bit#(32)) cycleCnt <- mkReg(0);
	Reg#(Bool) apotOn <- mkReg(False);
	Reg#(Bit#(2)) tileCnt <- mkReg(0);
	Reg#(QuantToken) apotTokenR <- mkRegU;

	rule countCycles;
		cycleCnt <= cycleCnt + 1;
	endrule

	rule initializeSlots ( initCnt < 2 );
		freeSlotQ.enq(truncate(initCnt));
		initCnt <= initCnt + 1;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 1] Read four features from four synchronous SRAM banks.
	//------------------------------------------------------------------------------------
	rule readToken ( inputOn );
		let slot = freeSlotQ.first;
		freeSlotQ.deq;
		TokenTag tag = TokenTag {token: zeroExtend(inputCnt), slot: slot};
		for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
			inputSram[i].portA.request.put(BRAMRequest {
				write: False, responseOnWrite: False,
				address: zeroExtend(inputCnt), datain: 0
			});
		end
		inputTagQ.enq(tag);
		if ( inputCnt == fromInteger(valueOf(TokenNum) - 1) ) begin
			inputOn <= False;
		end
		inputCnt <= inputCnt + 1;
`ifdef TRACE
		$display("EV,%0d,read,%0d,%0d", cycleCnt, tag.token, tag.slot);
`endif
	endrule

	// All input preparation multiplications use a single four-lane unit.
	// The stated rule priority is implementation-specific, not a ViM-Q proof.
	(* descending_urgency = "requestQuantization, requestScale, requestSmoothing" *)
	rule requestSmoothing;
		let tag = inputTagQ.first;
		inputTagQ.deq;
		FeatureVec values = replicate(0);
		Vector#(LaneNum, Int#(64)) factors = replicate(0);
		for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
			let value <- inputSram[i].portA.response.get;
			values[i] = unpack(value);
			factors[i] = unpack(zeroExtend(smoothingR[i]));
		end
		quantMultiplier.put(tag, SmoothOp, values, factors, 26);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2] Maximum magnitude, scale, actual reciprocal, INT8 conversion.
	//------------------------------------------------------------------------------------
	rule reduceMagnitude;
		let result = magnitudeQ.first;
		magnitudeQ.deq;
		scaleQ.enq(tuple2(result.tag, maxMagnitude(result.value)));
	endrule

	rule requestScale;
		match {.tag, .maximum} = scaleQ.first;
		scaleQ.deq;
		FeatureVec values = replicate(0);
		Vector#(LaneNum, Int#(64)) factors = replicate(0);
		values[0] = maximum;
		// Source 1/127 cast to unsigned fixed<32,14>: floor(2^18/127)=2064.
		factors[0] = 2064;
		quantMultiplier.put(tag, ScaleOp, values, factors, 18);
	endrule

	rule receiveReciprocal;
		let result <- reciprocal.get;
		reciprocalR[result.tag.slot] <= result.value;
		zeroScaleR[result.tag.slot] <= result.zeroScale;
		quantRequestQ.enq(result);
`ifdef TRACE
		$display("EV,%0d,reciprocal_done,%0d,%0d", cycleCnt,
			result.tag.token, result.tag.slot);
`endif
	endrule

	rule requestQuantization;
		let request = quantRequestQ.first;
		quantRequestQ.deq;
		FeatureVec values = smoothedBuffer[request.tag.slot];
		Int#(32) reciprocalValue = unpack(request.value);
		Vector#(LaneNum, Int#(64)) factors = replicate(signExtend(reciprocalValue));
		quantMultiplier.put(request.tag, QuantOp, values, factors, 18);
	endrule

	rule receiveMultiply;
		let result <- quantMultiplier.get;
		case ( result.op )
			SmoothOp: begin
				smoothedBuffer[result.tag.slot] <= result.value;
				magnitudeQ.enq(result);
			end
			ScaleOp: begin
				Bit#(32) scale = pack(result.value[0]);
				scaleR[result.tag.slot] <= scale;
				reciprocal.put(result.tag, scale);
`ifdef TRACE
				$display("EV,%0d,reciprocal_put,%0d,%0d", cycleCnt,
					result.tag.token, result.tag.slot);
`endif
			end
			QuantOp: begin
				QuantVec quantized = replicate(0);
				Bit#(32) packedQuantized = 0;
				for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
					// Explicit zero-scale guard. Original division by zero is not defined.
					quantized[i] = zeroScaleR[result.tag.slot] ? 0 : quantizeRounded(result.value[i]);
					Bit#(8) laneBits = pack(quantized[i]);
					packedQuantized = packedQuantized | (zeroExtend(laneBits) << (8 * i));
				end
				quantizedQ.enq(QuantToken {tag: result.tag, value: quantized});
`ifdef TRACE
				$display("EV,%0d,quant_ready,%0d,%0d", cycleCnt,
					result.tag.token, result.tag.slot);
				$display("Q,%0d,%08h,%08h,%08h,%0d", result.tag.token,
					scaleR[result.tag.slot], reciprocalR[result.tag.slot],
					packedQuantized, pack(zeroScaleR[result.tag.slot]));
`endif
			end
		endcase
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3] Four output tiles, synchronous weight reads, APoT pair/reduction.
	//------------------------------------------------------------------------------------
	rule beginProjection ( !apotOn );
		let token = quantizedQ.first;
		quantizedQ.deq;
		apotTokenR <= token;
		tileCnt <= 0;
		apotOn <= True;
	endrule

	rule issueTile ( apotOn );
		for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
			weightSram[i].portA.request.put(BRAMRequest {
				write: False, responseOnWrite: False,
				address: zeroExtend(tileCnt), datain: 0
			});
		end
		tileQ.enq(TileTag {tag: apotTokenR.tag, tile: tileCnt, value: apotTokenR.value});
		if ( tileCnt == fromInteger(valueOf(OutputTileNum) - 1) ) begin
			apotOn <= False;
		end
		tileCnt <= tileCnt + 1;
`ifdef TRACE
		$display("EV,%0d,weight_read_%0d,%0d,%0d", cycleCnt, tileCnt,
			apotTokenR.tag.token, apotTokenR.tag.slot);
`endif
	endrule

	rule computePairs;
		let tile = tileQ.first;
		tileQ.deq;
		FeatureVec left = replicate(0);
		FeatureVec right = replicate(0);
		for ( Integer o = 0; o < valueOf(LaneNum); o = o + 1 ) begin
			let word <- weightSram[o].portA.response.get;
			Vector#(LaneNum, Int#(32)) products = replicate(0);
			for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
				Bit#(4) code = truncate(word >> (4 * i));
				products[i] = signExtend(apotProduct(tile.value[i], code));
			end
			left[o] = products[0] + products[1];
			right[o] = products[2] + products[3];
		end
		dotPairsQ.enq(DotPairs {tag: tile.tag, tile: tile.tile, left: left, right: right});
`ifdef TRACE
		$display("EV,%0d,apot_issue_%0d,%0d,%0d", cycleCnt, tile.tile,
			tile.tag.token, tile.tag.slot);
`endif
	endrule

	rule reduceDot;
		let pairs = dotPairsQ.first;
		dotPairsQ.deq;
		FeatureVec result = replicate(0);
		for ( Integer o = 0; o < valueOf(LaneNum); o = o + 1 ) begin
			result[o] = pairs.left[o] + pairs.right[o];
		end
		partialQ.enq(PartialResult {tag: pairs.tag, tile: pairs.tile, sums: result});
	endrule

	// Host writes are outside the measured path and disallowed after start.
	method Action loadInput(Bit#(10) address, Vector#(LaneNum, Bit#(32)) data)
		if ( !startedDone );
		for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
			inputSram[i].portB.request.put(BRAMRequest {
				write: True, responseOnWrite: False, address: address, datain: data[i]
			});
		end
	endmethod

	method Action loadWeight(Bit#(10) address, WeightVec data) if ( !startedDone );
		for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
			weightSram[i].portB.request.put(BRAMRequest {
				write: True, responseOnWrite: False, address: address, datain: data[i]
			});
		end
	endmethod

	method Action configureSmoothing(Vector#(LaneNum, Bit#(32)) data) if ( !startedDone );
		smoothingR <= data;
	endmethod

	method Action start if ( initCnt == 2 && !startedDone );
		startedDone <= True;
		inputOn <= True;
		inputCnt <= 0;
	endmethod

	method ActionValue#(PartialResult) getPartial;
		let result = partialQ.first;
		partialQ.deq;
		return result;
	endmethod

	// Caller must retire once, only after the token's complete downstream output.
	// The diagnostic TB uses a separately disclosed synthetic consumer boundary.
	method Action retire(TokenTag tag) if ( startedDone );
		freeSlotQ.enq(tag.slot);
`ifdef TRACE
		$display("EV,%0d,retire,%0d,%0d", cycleCnt, tag.token, tag.slot);
`endif
	endmethod
endmodule

endpackage
