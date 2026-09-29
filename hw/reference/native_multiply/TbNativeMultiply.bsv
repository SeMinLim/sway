package TbNativeMultiply;

import Clocks::*;
import FIFO::*;
import RegFile::*;

import SwayMultiply::*;

interface MultiplyTestIfc;
	method Action allowDrain;
	method Bit#(32) cycle;
	method Bit#(9) accepted;
	method Bit#(9) consumed;
	method Bool done;
endinterface

module mkMultiplyTest(MultiplyTestIfc);
	MultiplyIfc multiplier <- mkMultiply;
	RegFile#(Bit#(8), Bit#(72)) vectorsR <- mkRegFileLoad("vectors.hex", 0, 255);
	// The test queue is larger than the DUT credit capacity so it cannot mask overflow.
	FIFO#(Tuple3#(Bit#(8), Int#(36), Bit#(32))) expectedQ <- mkSizedFIFO(16);
	Wire#(Bool) drainOn <- mkDWire(False);
	Reg#(Bit#(32)) cycleCnt <- mkReg(0);
	Reg#(Bit#(9)) acceptedCnt <- mkReg(0);
	Reg#(Bit#(9)) consumedCnt <- mkReg(0);

	rule tick;
		cycleCnt <= cycleCnt + 1;
	endrule

	rule checkCredits;
		if ( acceptedCnt - consumedCnt > 8 ) begin
			$display("FAIL credit overflow accepted=%0d consumed=%0d", acceptedCnt, consumedCnt);
			$finish(1);
		end
	endrule

	rule issue ( acceptedCnt < 256 && cycleCnt[1:0] != 3 );
		Bit#(8) index = truncate(acceptedCnt);
		Bit#(72) vector = vectorsR.sub(index);
		Int#(18) a = unpack(vector[71:54]);
		Int#(18) b = unpack(vector[53:36]);
		Int#(36) expected = unpack(vector[35:0]);
		multiplier.put(a, b);
		expectedQ.enq(tuple3(index, expected, cycleCnt));
		acceptedCnt <= acceptedCnt + 1;
	endrule

	rule checkFull ( cycleCnt == 32 );
		if ( acceptedCnt - consumedCnt != 8 ) begin
			$display("FAIL stall did not fill credits accepted=%0d consumed=%0d", acceptedCnt, consumedCnt);
			$finish(1);
		end
		$display("CREDIT_BOUND cycle=%0d accepted=%0d consumed=%0d", cycleCnt, acceptedCnt, consumedCnt);
	endrule

	rule collect ( drainOn && !(cycleCnt >= 8 && cycleCnt < 72) && cycleCnt[2:0] != 7 );
		let actual <- multiplier.get;
		let expected = expectedQ.first;
		expectedQ.deq;
		Bit#(32) latency = cycleCnt - tpl_3(expected);
		if ( tpl_1(expected) != truncate(consumedCnt) || actual != tpl_2(expected) || latency < 4 ) begin
			$display("FAIL result index=%0d actual=%0d expected=%0d latency=%0d", consumedCnt, actual, tpl_2(expected), latency);
			$finish(1);
		end
		if ( consumedCnt == 0 && latency != 4 ) begin
			$display("FAIL first product capture latency=%0d", latency);
			$finish(1);
		end
		$display("RESULT index=%0d value=%0d cycle=%0d issue=%0d", consumedCnt, actual, cycleCnt, tpl_3(expected));
		consumedCnt <= consumedCnt + 1;
	endrule

	method Action allowDrain;
		drainOn <= True;
	endmethod
	method Bit#(32) cycle = cycleCnt;
	method Bit#(9) accepted = acceptedCnt;
	method Bit#(9) consumed = consumedCnt;
	method Bool done = consumedCnt == 256;
endmodule

(* synthesize *)
module mkTbNativeMultiply(Empty);
	Clock clk <- exposeCurrentClock;
	MakeResetIfc restart <- mkReset(5, True, clk);
	MultiplyTestIfc test <- mkMultiplyTest(reset_by restart.new_rst);
	Reg#(Bit#(2)) resetCnt <- mkReg(0);
	Reg#(Bit#(32)) cycleCnt <- mkReg(0);

	rule tick;
		cycleCnt <= cycleCnt + 1;
		if ( cycleCnt >= 2000 ) begin
			$display("FAIL timeout");
			$finish(1);
		end
	endrule

	rule resetPipeline ( resetCnt == 0 && !restart.isAsserted && test.accepted == 6 );
		if ( test.consumed != 0 || test.cycle > 10 ) begin
			$display("FAIL pipeline reset precondition");
			$finish(1);
		end
		$display("RESET kind=pipeline cycle=%0d accepted=%0d consumed=%0d", test.cycle, test.accepted, test.consumed);
		restart.assertReset;
		resetCnt <= 1;
	endrule

	rule resetFull ( resetCnt == 1 && !restart.isAsserted && test.cycle == 36 );
		if ( test.accepted != 8 || test.consumed != 0 ) begin
			$display("FAIL full reset precondition");
			$finish(1);
		end
		$display("RESET kind=full cycle=%0d accepted=%0d consumed=%0d", test.cycle, test.accepted, test.consumed);
		restart.assertReset;
		resetCnt <= 2;
	endrule

	rule drain ( resetCnt == 2 && !restart.isAsserted );
		test.allowDrain;
	endrule

	rule finish ( resetCnt == 2 && !restart.isAsserted && test.done );
		$display("PASS native-multiply products=256 resets=2 final_cycle=%0d", test.cycle);
		$finish(0);
	endrule
endmodule

endpackage
