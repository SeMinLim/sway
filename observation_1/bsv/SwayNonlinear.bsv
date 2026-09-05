package SwayNonlinear;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;
import SwayTypes::*;

interface SwayLutIfc;
	method Action put(SwayValue x);
	method ActionValue#(SwayValue) get;
endinterface

module mkSwayLut#(String filename)(SwayLutIfc);
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = 4096;
	cfg.latency = 1;
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

module mkSwayActivation#(String filename)(SwayVectorIfc#(n, n));
	FIFOF#(SwayFrame#(n)) inputQ <- mkSizedFIFOF(1);
	Reg#(Bool) outputReadyOn <- mkReg(False);
	Reg#(Bool) outputFirstR <- mkReg(False);
	Reg#(Bit#(16)) outputTagR <- mkReg(0);
	FIFO#(Bit#(9)) indexQ <- mkSizedFIFO(4);
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
	rule process2 ( computeOn && issueCnt < fromInteger(valueOf(n)) );
		lut.put(inputR.data[issueCnt]);
		indexQ.enq(issueCnt);
		issueCnt <= issueCnt + 1;
	endrule
	rule process3 ( computeOn );
		let y <- lut.get;
		let idx = indexQ.first;
		indexQ.deq;
		for ( Integer i = 0; i < valueOf(n); i = i + 1 ) begin
			if ( idx == fromInteger(i) ) outputBuffer[i] <= y;
		end
		if ( idx == fromInteger(valueOf(n) - 1) ) begin
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
