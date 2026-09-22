package TbSwayDelta;

import Vector::*;

import SwayTypes::*;
import SwayLinear::*;
import SwayDelta::*;

// Exhaust every pair of signed INT8 inputs on both exported delta layers.
// Compare dedicated and shared scalar engines with the original four-lane
// affine engine, including blocked consumers and independent client progress.
(* synthesize *)
module mkTbSwayDelta(Empty);
	LinearIfc#(2, 40) reference0 <- mkSwayLinear(3);
	LinearIfc#(2, 40) reference1 <- mkSwayLinear(7);
	SwayDeltaIfc dedicated0 <- mkSwayDelta(0);
	SwayDeltaIfc dedicated1 <- mkSwayDelta(1);
	SwayDeltaIfc shared <- mkSwayDelta(2);
	Reg#(UInt#(32)) cycleCnt <- mkReg(0);
	Reg#(UInt#(17)) sent0Cnt <- mkReg(0);
	Reg#(UInt#(17)) sent1Cnt <- mkReg(0);
	Reg#(UInt#(17)) received0Cnt <- mkReg(0);
	Reg#(UInt#(17)) received1Cnt <- mkReg(0);

	rule tick;
		cycleCnt <= cycleCnt + 1;
	endrule

	rule checkProgress ( cycleCnt == 4096 );
		if ( received0Cnt != 0 || received1Cnt < 10 ) begin
			$display("SWAY_DELTA_FAIL,blocked_client_progress,%0d,%0d", received0Cnt, received1Cnt);
			$finish(1);
		end
	endrule

	rule timeout ( cycleCnt > 20000000 );
		$display("SWAY_DELTA_FAIL,timeout,%0d,%0d", received0Cnt, received1Cnt);
		$finish(1);
	endrule

	rule send0 ( sent0Cnt < 65536 && cycleCnt % 13 != 0 );
		Bit#(17) bits = pack(sent0Cnt);
		Vector#(2, Int#(8)) values = newVector;
		values[0] = unpack(bits[7:0]);
		values[1] = unpack(bits[15:8]);
		Token#(2) value = Token {index: truncate(bits), data: values};
		reference0.put(value);
		dedicated0.block0.put(value);
		shared.block0.put(value);
		sent0Cnt <= sent0Cnt + 1;
	endrule

	rule send1 ( sent1Cnt < 65536 && cycleCnt % 11 != 0 );
		Bit#(17) bits = pack(sent1Cnt);
		Vector#(2, Int#(8)) values = newVector;
		values[0] = unpack(~bits[15:8]);
		values[1] = unpack(bits[7:0]);
		Token#(2) value = Token {index: truncate(bits), data: values};
		reference1.put(value);
		dedicated1.block1.put(value);
		shared.block1.put(value);
		sent1Cnt <= sent1Cnt + 1;
	endrule

	rule receive0 ( cycleCnt > 8192 && cycleCnt % 17 != 0 );
		let expected <- reference0.get;
		let dedicated <- dedicated0.block0.get;
		let actual <- shared.block0.get;
		if ( dedicated != expected || actual != expected || expected.index != truncate(pack(received0Cnt)) ) begin
			$display("SWAY_DELTA_FAIL,block0,%0d", received0Cnt);
			$finish(1);
		end
		received0Cnt <= received0Cnt + 1;
	endrule

	rule receive1 ( cycleCnt % 19 != 0 );
		let expected <- reference1.get;
		let dedicated <- dedicated1.block1.get;
		let actual <- shared.block1.get;
		if ( dedicated != expected || actual != expected || expected.index != truncate(pack(received1Cnt)) ) begin
			$display("SWAY_DELTA_FAIL,block1,%0d", received1Cnt);
			$finish(1);
		end
		received1Cnt <= received1Cnt + 1;
	endrule

	rule done ( received0Cnt == 65536 && received1Cnt == 65536 );
		$display("SWAY_DELTA_PASS,pairs_per_layer,65536,layers,2,scalar_outputs_per_variant,5242880,cycles,%0d", cycleCnt);
		$finish(0);
	endrule
endmodule

endpackage
