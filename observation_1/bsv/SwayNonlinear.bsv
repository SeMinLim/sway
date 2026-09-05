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

module mkSwayActivation#(String filename)(SwayVectorIfc#(n, n));
	FIFOF#(SwayFrame#(n)) inputQ <- mkSizedFIFOF(1);
	FIFOF#(SwayFrame#(n)) outputQ <- mkSizedFIFOF(1);
	FIFO#(Bit#(9)) indexQ <- mkSizedFIFO(4);
	SwayLutIfc lut <- mkSwayLut(filename);
	Reg#(SwayFrame#(n)) inputR <- mkRegU;
	Reg#(Vector#(n, SwayValue)) outputR <- mkRegU;
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
		if ( outputQ.notEmpty ) blockedCnt <= blockedCnt + 1;
	endrule
	rule process1 ( !computeOn );
		inputR <= inputQ.first;
		inputQ.deq;
		issueCnt <= 0;
		computeOn <= True;
	endrule
	rule process2 ( computeOn && issueCnt < fromInteger(valueOf(n)) );
		lut.put(inputR.data[issueCnt]);
		indexQ.enq(issueCnt);
		issueCnt <= issueCnt + 1;
	endrule
	rule process3;
		let y <- lut.get;
		let idx = indexQ.first;
		indexQ.deq;
		Vector#(n, SwayValue) nextOutput = outputR;
		nextOutput[idx] = y;
		outputR <= nextOutput;
		if ( idx == fromInteger(valueOf(n) - 1) ) begin
			outputQ.enq(SwayFrame {first: inputR.first, tag: inputR.tag, data: nextOutput});
			computeOn <= False;
		end
	endrule
	method Action put(SwayFrame#(n) x);
		inputQ.enq(x);
	endmethod
	method ActionValue#(SwayFrame#(n)) get;
		let y = outputQ.first;
		outputQ.deq;
		return y;
	endmethod
	method Bool busy = computeOn;
	method SwayStats stats = SwayStats {cycles: cycleCnt, busyCycles: busyCnt,
		mulCount: 0, inputEmptyCycles: emptyCnt, outputFullCycles: blockedCnt};
endmodule
endpackage
