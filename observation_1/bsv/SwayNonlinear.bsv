package SwayNonlinear;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;
import Assert::*;

import SwayTypes::*;

interface SwayLutIfc;
	method Action put(SwayValue x);
	method ActionValue#(SwayValue) get;
endinterface

module mkSwayLut#(String filename)(SwayLutIfc);
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = 4096;
	cfg.latency = 2;
	cfg.loadFormat = tagged Hex filename;
	BRAM1Port#(Bit#(12), SwayValue) tableR <- mkBRAM1Server(cfg);
	method Action put(SwayValue x);
		tableR.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: swayLutAddress(x), datain: 0});
	endmethod
	method ActionValue#(SwayValue) get;
		let y <- tableR.portA.response.get;
		return y;
	endmethod
endmodule

// Decay needs much finer resolution near zero than SiLU/softplus.
// Input Q6.10, output Q1.15; nearest 1/256 step over [-16, 0].
module mkSwayDecayLut(SwayLutIfc);
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = 4096;
	cfg.latency = 1;
	cfg.loadFormat = tagged Hex "data/exp.hex";
	BRAM1Port#(Bit#(12), SwayValue) tableR <- mkBRAM1Server(cfg);
	method Action put(SwayValue x);
		Int#(18) magnitude = -signExtend(x);
		Int#(18) rounded = (magnitude + 2) >> 2;
		if ( rounded < 0 ) rounded = 0;
		if ( rounded > 4095 ) rounded = 4095;
		Bit#(12) address = truncate(pack(rounded));
		tableR.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: address, datain: 0});
	endmethod
	method ActionValue#(SwayValue) get;
		let y <- tableR.portA.response.get;
		return y;
	endmethod
endmodule

typedef struct {
	Bit#(9) index;
	Vector#(16, SwayValue) candidates;
} SwayActivationSelect8 deriving (Bits, Eq);

typedef struct {
	Bit#(9) index;
	Vector#(4, SwayValue) candidates;
} SwayActivationSelect4 deriving (Bits, Eq);

typedef struct {
	Bit#(9) index;
	SwayValue value;
} SwayActivationValue deriving (Bits, Eq);

module mkSwayActivation#(String filename)(SwayVectorIfc#(n, n));
	staticAssert(valueOf(n) > 0 && valueOf(n) <= 128,
		"Sway activation selector supports 1..128 channels");
	FIFOF#(SwayFrame#(n)) inputQ <- mkSizedFIFOF(1);
	Reg#(Bool) outputReadyOn <- mkReg(False);
	Reg#(Bool) outputFirstR <- mkReg(False);
	Reg#(Bit#(16)) outputTagR <- mkReg(0);
	// Non-bypass selection/retirement boundaries; four outstanding read tags.
	FIFO#(SwayActivationSelect8) select8Q <- mkFIFO;
	FIFO#(SwayActivationSelect4) select4Q <- mkFIFO;
	FIFO#(SwayActivationValue) selectedQ <- mkFIFO;
	FIFO#(Bit#(9)) indexQ <- mkSizedFIFO(4);
	FIFO#(SwayActivationValue) completedQ <- mkFIFO;
	SwayLutIfc lut <- mkSwayLut(filename);
	let inputR = inputQ.first;
	Vector#(n, Reg#(SwayValue)) outputBuffer <- replicateM(mkRegU);
	Reg#(Bit#(9)) issueCnt <- mkReg(0);
	Reg#(Bool) computeOn <- mkReg(False);
	Reg#(Bit#(64)) cycleCnt <- mkReg(0);
	Reg#(Bit#(64)) busyCnt <- mkReg(0);
	Reg#(Bit#(64)) emptyCnt <- mkReg(0);
	Reg#(Bit#(64)) blockedCnt <- mkReg(0);
	rule profile;
		cycleCnt <= cycleCnt + 1;
		if ( computeOn ) busyCnt <= busyCnt + 1;
		if ( !computeOn && !inputQ.notEmpty ) emptyCnt <= emptyCnt + 1;
		if ( outputReadyOn ) blockedCnt <= blockedCnt + 1;
	endrule
	rule process1 ( !computeOn && !outputReadyOn && inputQ.notEmpty );
		issueCnt <= 0;
		computeOn <= True;
	endrule
	// [STAGE 2] Registered 8:1 selection within static channel groups.
	rule process2 ( computeOn && issueCnt < fromInteger(valueOf(n)) );
		Bit#(3) lowIndex = issueCnt[2:0];
		Vector#(16, SwayValue) candidates = newVector;
		for ( Integer groupIdx = 0; groupIdx < 16; groupIdx = groupIdx + 1 ) begin
			Vector#(8, SwayValue) bank = replicate(0);
			for ( Integer lane = 0; lane < 8; lane = lane + 1 ) begin
				if ( groupIdx * 8 + lane < valueOf(n) ) begin
					bank[lane] = inputR.data[groupIdx * 8 + lane];
				end
			end
			candidates[groupIdx] = bank[lowIndex];
		end
		select8Q.enq(SwayActivationSelect8 {index: issueCnt, candidates: candidates});
		issueCnt <= issueCnt + 1;
	endrule

	// [STAGE 3] Four registered candidates remain.
	rule process3;
		let item = select8Q.first;
		select8Q.deq;
		Bit#(2) middleIndex = item.index[4:3];
		Vector#(4, SwayValue) candidates = newVector;
		for ( Integer groupIdx = 0; groupIdx < 4; groupIdx = groupIdx + 1 ) begin
			Vector#(4, SwayValue) bank = newVector;
			for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
				bank[lane] = item.candidates[groupIdx * 4 + lane];
			end
			candidates[groupIdx] = bank[middleIndex];
		end
		select4Q.enq(SwayActivationSelect4 {index: item.index, candidates: candidates});
	endrule

	// [STAGE 4] Final selection ends at a register, not the SRAM address.
	rule process4;
		let item = select4Q.first;
		select4Q.deq;
		Bit#(2) highIndex = item.index[6:5];
		selectedQ.enq(SwayActivationValue {index: item.index,
			value: item.candidates[highIndex]});
	endrule

	// [STAGE 5] LUT address generation uses the registered scalar operand.
	rule process5;
		let item = selectedQ.first;
		selectedQ.deq;
		lut.put(item.value);
		indexQ.enq(item.index);
	endrule

	// [STAGE 6] Capture the SRAM response before the output write fanout.
	rule process6;
		let y <- lut.get;
		let idx = indexQ.first;
		indexQ.deq;
		completedQ.enq(SwayActivationValue {index: idx, value: y});
	endrule

	// [STAGE 7] Keep the input frame until the last registered result retires.
	rule process7 ( computeOn );
		let item = completedQ.first;
		completedQ.deq;
		for ( Integer i = 0; i < valueOf(n); i = i + 1 ) begin
			if ( item.index == fromInteger(i) ) outputBuffer[i] <= item.value;
		end
		if ( item.index == fromInteger(valueOf(n) - 1) ) begin
			inputQ.deq;
			outputFirstR <= inputR.first;
			outputTagR <= inputR.tag;
			outputReadyOn <= True;
			computeOn <= False;
		end
	endrule
	method Action put(SwayFrame#(n) x);
		inputQ.enq(x);
	endmethod
	method ActionValue#(SwayFrame#(n)) get if ( outputReadyOn );
		outputReadyOn <= False;
		return SwayFrame {first: outputFirstR, tag: outputTagR, data: readVReg(outputBuffer)};
	endmethod
	method Bool busy = computeOn;
	method SwayStats stats = SwayStats {cycles: cycleCnt, busyCycles: busyCnt,
		mulCount: 0, inputEmptyCycles: emptyCnt, outputFullCycles: blockedCnt};
endmodule

(* synthesize *)
module mkSwaySoftplus(SwayVectorIfc#(128, 128));
	let engine <- mkSwayActivation("data/softplus.hex");
	return engine;
endmodule
endpackage
