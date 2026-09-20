// Checks the BSIM implementation against the two-FF production leaf latency.
// Two root edges plus two leaf edges give four edges from PLL-lock release;
// this test starts from the root reset and checks the two additional edges.
package TbLocalReset;
import SwayReset::*;
module mkTbLocalReset(Empty);
	LocalResetIfc leaf <- mkSwayLocalReset;
	Reg#(UInt#(8)) cycleCnt <- mkReg(0);
	rule verify;
		if ( leaf.ready != (cycleCnt >= 2) ) begin
			$display("SWAY_RESET_FAIL local cycle=%0d ready=%0d", cycleCnt, leaf.ready);
			$finish(1);
		end
		if ( cycleCnt == 5 ) begin
			$display("SWAY_RESET_BSIM_PASS leaf_edges=2 ready=direct");
			$finish(0);
		end
		cycleCnt <= cycleCnt + 1;
	endrule
endmodule
endpackage
