package TbSwayPerf;

import Assert::*;
import RegFile::*;

import SwayTypes::*;
import SwayBaseline::*;
import GeneratedTestConfig::*;

// Testbench only. The DUT, numerical model, and existing stress test are unchanged.
module mkSwayPerf#(Integer frameCount)(Empty);
	Integer inputWords = valueOf(TestFrameCount) * 320;
	Integer outputWords = valueOf(TestFrameCount) * 57;
	Integer drainCycles = 2048;
	Integer watchdogCycles = frameCount * 1000000;
	staticAssert(frameCount > 0 && valueOf(TestFrameCount) > 0,
		"Performance test requires at least one complete fixture frame");

	SwayIfc dut <- mkSwayBaseline;
	RegFile#(Bit#(32), Int#(8)) inputR <- mkRegFileLoad(testInputPath(), 0, fromInteger(inputWords - 1));
	RegFile#(Bit#(32), Int#(8)) expectedR <- mkRegFileLoad(testExpectedPath(), 0, fromInteger(outputWords - 1));
	Reg#(UInt#(32)) sentCnt <- mkReg(0);
	Reg#(UInt#(32)) receivedCnt <- mkReg(0);
	Reg#(UInt#(32)) inputFixtureCnt <- mkReg(0);
	Reg#(UInt#(32)) outputFixtureCnt <- mkReg(0);
	Reg#(UInt#(64)) cycleCnt <- mkReg(0);
	Reg#(UInt#(64)) inputStartR <- mkReg(0);
	Reg#(UInt#(64)) outputStartR <- mkReg(0);
	Reg#(UInt#(32)) drainCnt <- mkReg(0);
	Reg#(Bool) startedOn <- mkReg(False);
	Reg#(Bool) finishOn <- mkReg(False);

	rule tick;
		cycleCnt <= cycleCnt + 1;
	endrule

	rule initialize ( !startedOn );
		$display("SWAY_PERF_BEGIN,%0d,%0d", frameCount, valueOf(TestFrameCount));
		startedOn <= True;
	endrule

	rule watchdog ( cycleCnt > fromInteger(watchdogCycles) );
		$display("SWAY_PERF_FAIL watchdog sent=%0d outputs=%0d cycles=%0d", sentCnt, receivedCnt, cycleCnt);
		$finish(1);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 1] Attempt a byte every cycle. Only DUT readiness can stop this rule.
	// Reuse the checked-in fixtures cyclically; do not wait for the preceding reply.
	//------------------------------------------------------------------------------------
	rule process1 ( startedOn && sentCnt < fromInteger(frameCount * 320) );
		dut.put(inputR.sub(pack(inputFixtureCnt)));
		sentCnt <= sentCnt + 1;
		inputFixtureCnt <= inputFixtureCnt == fromInteger(inputWords - 1) ? 0 : inputFixtureCnt + 1;
		if ( sentCnt % 320 == 0 ) inputStartR <= cycleCnt;
		if ( sentCnt % 320 == 319 ) begin
			$display("SWAY_PERF_INPUT_FRAME,%0d,%0d,%0d", sentCnt / 320, inputStartR, cycleCnt);
		end
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2] Always-ready sink. Verify every scalar without artificial pauses.
	// Cycle stamps belong to successful put/get transactions, not attempted calls.
	//------------------------------------------------------------------------------------
	rule process2 ( startedOn && !finishOn );
		let value <- dut.get;
		let expected = expectedR.sub(pack(outputFixtureCnt));
		if ( value != expected ) begin
			$display("SWAY_PERF_FAIL mismatch frame=%0d coordinate=%0d expected=%0d actual=%0d cycles=%0d",
				receivedCnt / 57, receivedCnt % 57, expected, value, cycleCnt);
			$finish(1);
		end
		$display("SWAY_PERF_OUTPUT,%0d,%0d,%0d", receivedCnt, value, cycleCnt);
		receivedCnt <= receivedCnt + 1;
		outputFixtureCnt <= outputFixtureCnt == fromInteger(outputWords - 1) ? 0 : outputFixtureCnt + 1;
		if ( receivedCnt % 57 == 0 ) outputStartR <= cycleCnt;
		if ( receivedCnt % 57 == 56 ) begin
			$display("SWAY_PERF_FRAME,%0d,%0d,%0d", receivedCnt / 57, outputStartR, cycleCnt);
		end
		if ( receivedCnt + 1 == fromInteger(frameCount * 57) ) finishOn <= True;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3] Detect trailing output. Drain time is never part of latency/throughput.
	//------------------------------------------------------------------------------------
	rule process3 ( finishOn );
		let value <- dut.get;
		$display("SWAY_PERF_FAIL extra_output actual=%0d cycles=%0d", value, cycleCnt);
		$finish(1);
	endrule

	rule process4 ( finishOn && drainCnt < fromInteger(drainCycles) );
		drainCnt <= drainCnt + 1;
	endrule

	rule process5 ( finishOn && drainCnt == fromInteger(drainCycles) );
		if ( sentCnt != fromInteger(frameCount * 320) ) begin
			$display("SWAY_PERF_FAIL premature_completion sent=%0d expected=%0d", sentCnt, frameCount * 320);
			$finish(1);
		end else begin
			$display("SWAY_PERF_PASS,%0d,%0d,%0d,%0d", frameCount, receivedCnt, cycleCnt, drainCycles);
			$finish(0);
		end
	endrule
endmodule

// Separate simulation starts with an empty DUT for single-frame latency.
module mkTbSwayPerfSingle(Empty);
	let test <- mkSwayPerf(1);
endmodule

// Saturated stream. First/last eight completions are excluded by the checker.
module mkTbSwayPerf(Empty);
	let test <- mkSwayPerf(64);
endmodule

endpackage
