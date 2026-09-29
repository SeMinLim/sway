package TbLinearSelect;

import GeneratedLinearVectors::*;
import Vector::*;
import SwayTypes::*;
import SwayLinear::*;

module mkTbLinearSelect(Empty);
	LinearIfc#(320, 20) dut <- mkSwayLinear(9);
	Reg#(Bit#(4)) sentCnt <- mkReg(0);
	Reg#(Bit#(4)) receivedCnt <- mkReg(0);
	Reg#(UInt#(32)) cycleCnt <- mkReg(0);
	Reg#(UInt#(32)) drainCnt <- mkReg(0);

	rule tick;
		cycleCnt <= cycleCnt + 1;
	endrule

	rule watchdog ( cycleCnt > 100000 );
		$display("LINEAR_FAIL watchdog sent=%0d received=%0d", sentCnt, receivedCnt);
		$finish(1);
	endrule

	rule send ( sentCnt < 4 && cycleCnt % 7 != 0 );
		Vector#(320, Int#(8)) value = newVector;
		for ( Integer i = 0; i < 320; i = i + 1 ) begin
			value[i] = inputValue(sentCnt, i);
		end
		dut.put(Token { index: sentCnt, data: value });
		sentCnt <= sentCnt + 1;
	endrule

	rule receive ( receivedCnt < 4 && cycleCnt >= 15000 && cycleCnt % 31 > 7 );
		let value <- dut.get;
		if ( value.index != receivedCnt ) begin
			$display("LINEAR_FAIL token index expected=%0d actual=%0d", receivedCnt, value.index);
			$finish(1);
		end
		for ( Integer i = 0; i < 20; i = i + 1 ) begin
			Int#(8) expected = expectedValue(receivedCnt, i);
			if ( value.data[i] != expected ) begin
				$display("LINEAR_FAIL token=%0d row=%0d expected=%0d actual=%0d", receivedCnt, i, expected, value.data[i]);
				$finish(1);
			end
		end
		$display("LINEAR_TOKEN token=%0d values=20 cycles=%0d", receivedCnt, cycleCnt);
		receivedCnt <= receivedCnt + 1;
	endrule

	rule trailing ( receivedCnt == 4 );
		let value <- dut.get;
		$display("LINEAR_FAIL trailing token=%0d", value.index);
		$finish(1);
	endrule

	rule drain ( receivedCnt == 4 && drainCnt < 1024 );
		drainCnt <= drainCnt + 1;
	endrule

	rule finish ( receivedCnt == 4 && drainCnt == 1024 );
		if ( sentCnt != 4 ) begin
			$display("LINEAR_FAIL premature completion");
			$finish(1);
		end else begin
			$display("LINEAR_PASS tokens=4 values=80 cycles=%0d drain=1024", cycleCnt);
			$finish(0);
		end
	endrule
endmodule

endpackage
