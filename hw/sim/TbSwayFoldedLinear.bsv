package TbSwayFoldedLinear;

import Vector::*;

import SwayFoldedLinear::*;
import SwayLinear::*;
import SwayTypes::*;

interface FoldedCheckIfc#(numeric type n, numeric type m);
	method Bool done;
endinterface

function Bit#(32) sampleBits(Bit#(32) seed);
	Bit#(32) value = seed ^ (seed << 13);
	value = value ^ (value >> 17);
	value = value ^ (value << 5);
	return value;
endfunction

// Independent old engine supplies expected fixed-point results. Cases include
// signed INT8 endpoints, alternating signs, zero, and deterministic mixed data.
// Periodic output pauses force result and source-token retention under pressure.
module mkFoldedCheck#(Integer layerId, Integer laneNum)(FoldedCheckIfc#(n, m));
	LinearIfc#(n, m) original <- mkSwayLinear(layerId);
	LinearIfc#(n, m) folded <- mkSwayFoldedLinear(layerId, laneNum);
	Reg#(Bit#(5)) sentCnt <- mkReg(0);
	Reg#(Bit#(5)) receivedCnt <- mkReg(0);
	Reg#(Bit#(32)) cycleCnt <- mkReg(0);
	Reg#(Bool) reportedOn <- mkReg(False);

	rule tick;
		cycleCnt <= cycleCnt + 1;
	endrule

	rule process1 ( sentCnt < 12 && cycleCnt % 7 != 2 );
		Vector#(n, Int#(8)) values = newVector;
		for ( Integer inputIndex = 0; inputIndex < valueOf(n); inputIndex = inputIndex + 1 ) begin
			Bit#(32) seed = (zeroExtend(sentCnt) + 1) * 32'h9e3779b9 + fromInteger(inputIndex * 7919 + layerId);
			Int#(8) value = unpack(truncate(sampleBits(seed)));
			if ( sentCnt == 0 ) value = 127;
			if ( sentCnt == 1 ) value = -128;
			if ( sentCnt == 2 ) value = inputIndex % 2 == 0 ? 127 : -128;
			if ( sentCnt == 3 ) value = 0;
			values[inputIndex] = value;
		end
		Token#(n) value = Token { index: truncate(sentCnt), data: values };
		original.put(value);
		folded.put(value);
		sentCnt <= sentCnt + 1;
	endrule

	rule process2 ( receivedCnt < 12 && cycleCnt % 4096 >= 512 );
		let expected <- original.get;
		let actual <- folded.get;
		if ( expected != actual || actual.index != truncate(receivedCnt) ) begin
			$display("SWAY_FOLDED_FAIL layer=%0d lanes=%0d sample=%0d cycles=%0d", layerId, laneNum, receivedCnt, cycleCnt);
			$display("expected=", fshow(expected));
			$display("actual=", fshow(actual));
			$finish(1);
		end
		receivedCnt <= receivedCnt + 1;
	endrule

	rule process3 ( receivedCnt == 12 && !reportedOn );
		$display("SWAY_FOLDED_LAYER_PASS layer=%0d lanes=%0d tokens=12 outputs=%0d cycles=%0d", layerId, laneNum, valueOf(m) * 12, cycleCnt);
		reportedOn <= True;
	endrule

	method Bool done = reportedOn;
endmodule

module mkTbSwayFoldedLinear(Empty);
	FoldedCheckIfc#(20, 20) embedding <- mkFoldedCheck(0, 1);
	FoldedCheckIfc#(40, 18) state0 <- mkFoldedCheck(2, 2);
	FoldedCheckIfc#(40, 20) output0 <- mkFoldedCheck(4, 2);
	FoldedCheckIfc#(40, 18) state1 <- mkFoldedCheck(6, 2);
	FoldedCheckIfc#(40, 20) output1 <- mkFoldedCheck(8, 2);
	FoldedCheckIfc#(320, 20) hidden <- mkFoldedCheck(9, 1);
	FoldedCheckIfc#(20, 57) head <- mkFoldedCheck(10, 1);
	Reg#(Bit#(32)) cycleCnt <- mkReg(0);

	rule tick;
		cycleCnt <= cycleCnt + 1;
	endrule

	rule complete ( embedding.done && state0.done && output0.done && state1.done && output1.done && hidden.done && head.done );
		$display("SWAY_FOLDED_PASS layers=7 tokens=84 scalar_outputs=2076 cycles=%0d", cycleCnt);
		$finish(0);
	endrule

	rule watchdog ( cycleCnt == 500000 );
		$display("SWAY_FOLDED_FAIL watchdog cycles=%0d", cycleCnt);
		$finish(1);
	endrule
endmodule

endpackage
