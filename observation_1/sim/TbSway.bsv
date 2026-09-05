package TbSway;
import Vector::*;
import RegFile::*;
import SwayTypes::*;
import SwayBaseline::*;

module mkTbSway(Empty);
	SwayBaselineIfc dut <- mkSwayBaseline;
	RegFile#(Bit#(7), Vector#(64, SwayValue)) inputs <- mkRegFileLoad("data/input.hex", 0, 99);
	RegFile#(Bit#(7), Vector#(64, SwayValue)) expected <- mkRegFileLoad("data/expected.hex", 0, 99);
	Reg#(Bit#(16)) sentCnt <- mkReg(0);
	Reg#(Bit#(16)) receivedCnt <- mkReg(0);
	Reg#(Bit#(64)) cycleCnt <- mkReg(0);
	Reg#(Bool) overlapSeen <- mkReg(False);
	Reg#(Bool) finishOn <- mkReg(False);
	Reg#(Bit#(4)) reportCnt <- mkReg(0);
	Reg#(Bit#(64)) firstOutputCycle <- mkReg(0);

	rule clockTick;
		cycleCnt <= cycleCnt + 1;
		let active = dut.activeStages;
		if ( (active & (active - 1)) != 0 ) overlapSeen <= True;
		if ( cycleCnt > 10000000 ) begin
			$display("SWAY_FAIL watchdog sent=%0d received=%0d", sentCnt, receivedCnt);
			$finish(1);
		end
	endrule
	// Two identical sequences without a global reset test per-sequence state reset.
	// Periodic source bubbles and sink stalls test FIFO readiness propagation.
	rule sendInput ( sentCnt < 200 && cycleCnt % 13 != 0 );
		Bit#(7) index = truncate(sentCnt % 100);
		dut.put(SwayFrame {first: index == 0, tag: sentCnt, data: inputs.sub(index)});
		sentCnt <= sentCnt + 1;
	endrule
	rule checkOutput ( !finishOn && cycleCnt % 7 != 0 && !(cycleCnt >= 50000 && cycleCnt < 80000) );
		let y <- dut.get;
		Bit#(7) index = truncate(receivedCnt % 100);
		let refY = expected.sub(index);
		if ( y.tag != receivedCnt || y.first != (index == 0) ) begin
			$display("SWAY_FAIL order expected=%0d actual=%0d", receivedCnt, y.tag);
			$finish(1);
		end
		for ( Integer i = 0; i < 64; i = i + 1 ) begin
			if ( y.data[i] != refY[i] ) begin
				$display("SWAY_FAIL value token=%0d element=%0d expected=%0d actual=%0d", receivedCnt, i, refY[i], y.data[i]);
				$finish(1);
			end
		end
		if ( receivedCnt == 0 ) firstOutputCycle <= cycleCnt;
		$display("SWAY_TOKEN,%0d,%0d", receivedCnt, cycleCnt);
		receivedCnt <= receivedCnt + 1;
		if ( receivedCnt == 199 ) begin
			if ( !overlapSeen ) begin
				$display("SWAY_FAIL no token overlap");
				$finish(1);
			end
			finishOn <= True;
		end
	endrule
	rule report ( finishOn );
		if ( reportCnt < 7 ) begin
			let s = dut.stats[reportCnt];
			$display("SWAY_STAGE,%0d,%0d,%0d,%0d,%0d,%0d", reportCnt,
				s.cycles, s.busyCycles, s.mulCount, s.inputEmptyCycles, s.outputFullCycles);
			reportCnt <= reportCnt + 1;
		end else begin
			$display("SWAY_PASS tokens=200 elements=12800 overlap=1 first_output_cycle=%0d final_cycle=%0d", firstOutputCycle, cycleCnt);
			$finish(0);
		end
	endrule
endmodule
endpackage
