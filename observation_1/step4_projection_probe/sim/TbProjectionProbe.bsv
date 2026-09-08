package TbProjectionProbe;

import FIFOF::*;
import RegFile::*;
import Vector::*;

import QuantTypes::*;
import ProjectionProbe::*;

(* synthesize *)
module mkTbProjectionProbe(Empty);
	ProjectionProbeIfc dut <- mkProjectionProbe;
	Vector#(4, String) inputNames = newVector;
	Vector#(4, String) weightNames = newVector;
	inputNames[0] = "fixtures/input0.hex";
	inputNames[1] = "fixtures/input1.hex";
	inputNames[2] = "fixtures/input2.hex";
	inputNames[3] = "fixtures/input3.hex";
	weightNames[0] = "fixtures/weight0.hex";
	weightNames[1] = "fixtures/weight1.hex";
	weightNames[2] = "fixtures/weight2.hex";
	weightNames[3] = "fixtures/weight3.hex";
	Vector#(4, RegFile#(Bit#(10), Bit#(32))) inputFiles = newVector;
	Vector#(4, RegFile#(Bit#(10), Bit#(16))) weightFiles = newVector;
	for ( Integer i = 0; i < 4; i = i + 1 ) begin
		inputFiles[i] <- mkRegFileLoad(inputNames[i], 0, 6);
		weightFiles[i] <- mkRegFileLoad(weightNames[i], 0, 3);
	end
	RegFile#(Bit#(2), Bit#(32)) smoothFile <- mkRegFileLoad("fixtures/smooth.hex", 0, 3);
	RegFile#(Bit#(7), Bit#(32)) expectedFile <- mkRegFileLoad("fixtures/expected.hex", 0, 111);
	RegFile#(Bit#(1), Bit#(32)) controlFile <- mkRegFileLoad("fixtures/control.hex", 0, 1);
	FIFOF#(Tuple2#(Bit#(32), TokenTag)) retireQ <- mkSizedFIFOF(2);

	Reg#(Bit#(32)) cycleCnt <- mkReg(0);
	Reg#(Bit#(4)) preloadCnt <- mkReg(0);
	Reg#(Bool) startedDone <- mkReg(False);
	Reg#(Bit#(6)) outputCnt <- mkReg(0);
	Reg#(Bit#(4)) retiredCnt <- mkReg(0);
	Reg#(Bit#(32)) firstReadCycleR <- mkReg(0);

	rule tick;
		cycleCnt <= cycleCnt + 1;
		if ( cycleCnt > 20000 ) begin
			$display("FAIL,watchdog,%0d", outputCnt);
			$finish(1);
		end
	endrule

	rule preload ( !startedDone && preloadCnt < 9 );
		if ( preloadCnt < 7 ) begin
			Vector#(4, Bit#(32)) values = replicate(0);
			for ( Integer i = 0; i < 4; i = i + 1 ) begin
				values[i] = inputFiles[i].sub(zeroExtend(preloadCnt));
			end
			dut.loadInput(zeroExtend(preloadCnt), values);
		end
		if ( preloadCnt < 4 ) begin
			WeightVec values = replicate(0);
			for ( Integer i = 0; i < 4; i = i + 1 ) begin
				values[i] = weightFiles[i].sub(zeroExtend(preloadCnt));
			end
			dut.loadWeight(zeroExtend(preloadCnt), values);
		end
		if ( preloadCnt == 0 ) begin
			Vector#(4, Bit#(32)) values = replicate(0);
			for ( Integer i = 0; i < 4; i = i + 1 ) begin
				values[i] = smoothFile.sub(fromInteger(i));
			end
			dut.configureSmoothing(values);
		end
		preloadCnt <= preloadCnt + 1;
	endrule

	rule beginTest ( preloadCnt == 9 && !startedDone );
		dut.start;
		startedDone <= True;
		firstReadCycleR <= cycleCnt + 1;
	endrule

	// Periodic sink stalls are a functional backpressure test, not model work.
	rule checkResult ( startedDone && outputCnt < 28 &&
		(controlFile.sub(0) == 0 || cycleCnt[2:0] >= 3) );
		let result <- dut.getPartial;
		Bit#(16) expectedToken = zeroExtend(outputCnt >> 2);
		Bit#(2) expectedTile = truncate(outputCnt);
		if ( result.tag.token != expectedToken || result.tile != expectedTile ) begin
			$display("FAIL,ordering,%0d,%0d,%0d", outputCnt, result.tag.token, result.tile);
			$finish(1);
		end
		for ( Integer i = 0; i < 4; i = i + 1 ) begin
			Bit#(7) address = (zeroExtend(outputCnt) << 2) + fromInteger(i);
			Int#(32) expected = unpack(expectedFile.sub(address));
			if ( result.sums[i] != expected ) begin
				$display("FAIL,value,%0d,%0d,%0d,%0d", outputCnt, i, result.sums[i], expected);
				$finish(1);
			end
		end
		$display("OUT,%0d,%0d,%0d,%0d", cycleCnt, result.tag.token, result.tag.slot, result.tile);
		if ( result.tile == 3 ) begin
			// Caller models downstream completion explicitly. This is NOT full
			// RadMamba dequantization/softplus timing and is never used as such.
			Bit#(32) retireAt = cycleCnt + controlFile.sub(1);
			retireQ.enq(tuple2(retireAt, result.tag));
		end
		outputCnt <= outputCnt + 1;
	endrule

	rule retireToken ( startedDone );
		match {.retireAt, .tag} = retireQ.first;
		if ( cycleCnt >= retireAt ) begin
			retireQ.deq;
			dut.retire(tag);
			retiredCnt <= retiredCnt + 1;
		end
	endrule

	rule completeTest ( outputCnt == 28 && retiredCnt == 7 );
		$display("PASS,projection_partial_values,112,tokens,7");
		$display("SLICE_ELAPSED,%0d", cycleCnt - firstReadCycleR);
		$finish(0);
	endrule
endmodule

endpackage
