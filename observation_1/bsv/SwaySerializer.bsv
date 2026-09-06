package SwaySerializer;
import Vector::*;
import FIFO::*;

import SwayTypes::*;

interface SwaySerializerIfc;
	method Action put(SwayFrame#(64) frame);
	method ActionValue#(Bit#(8)) get;
endinterface

// Preserve the debug packet: flags, little-endian tag, 64 little-endian words.
// A constant head-word tap replaces index arithmetic and the 64-way read mux.
(* synthesize *)
module mkSwaySerializer(SwaySerializerIfc);
	// One retained frame, as in the old Top wrapper. Do not duplicate it
	// in a wide input FIFO merely to adapt this frame-load interface.
	Reg#(SwayFrame#(64)) frameR <- mkRegU;
	Reg#(Bit#(8)) byteCnt <- mkReg(0);
	Reg#(Bool) sendOn <- mkReg(False);
	FIFO#(Bit#(8)) outputQ <- mkFIFO;

	// Shifts below have a constant one-word distance and become wiring.
	// State advances only when the byte enters outputQ, so stalls neither
	// duplicate bytes nor skip words. The final byte stays queued on reload.
	rule process1 ( sendOn );
		Bit#(16) headWord = pack(frameR.data[0]);
		Bit#(8) value = byteCnt[0] == 1 ? headWord[7:0] : headWord[15:8];
		if ( byteCnt == 0 ) value = zeroExtend(pack(frameR.first));
		else if ( byteCnt == 1 ) value = frameR.tag[7:0];
		else if ( byteCnt == 2 ) value = frameR.tag[15:8];
		outputQ.enq(value);
		if ( byteCnt >= 3 && byteCnt[0] == 0 ) begin
			SwayFrame#(64) nextFrame = frameR;
			for ( Integer i = 0; i < 63; i = i + 1 ) begin
				nextFrame.data[i] = frameR.data[i + 1];
			end
			nextFrame.data[63] = 0;
			frameR <= nextFrame;
		end
		if ( byteCnt == 130 ) sendOn <= False;
		else byteCnt <= byteCnt + 1;
	endrule

	method Action put(SwayFrame#(64) frame) if ( !sendOn );
		frameR <= frame;
		byteCnt <= 0;
		sendOn <= True;
	endmethod
	method ActionValue#(Bit#(8)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule
endpackage
