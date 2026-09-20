package SwayReset;

import Clocks::*;

interface LocalResetIfc;
	interface Reset rst;
	method Bool ready;
endinterface

import "BVI" SwayReset =
module mkSwayResetLeaf(LocalResetIfc);
	default_clock clk(CLK);
	default_reset rootReset(RST_IN_N);
	output_reset rst(RST_OUT_N) clocked_by (clk);
	method READY ready() clocked_by (clk) reset_by (no_reset);
	schedule ready CF ready;
endmodule

// Instantiate each leaf under the same root reset. Use the returned reset only
// for that engine's registers/FIFOs, and ready to guard public input methods.
module mkSwayLocalReset(LocalResetIfc);
`ifdef BSIM
	Clock clock <- exposeCurrentClock;
	Reset rootReset <- exposeCurrentReset;
	Reset leafReset <- mkAsyncReset(2, rootReset, clock);
	ReadOnly#(Bool) asserted <- isResetAssertedDirect(reset_by leafReset);
	interface rst = leafReset;
	method Bool ready = !asserted;
`else
	LocalResetIfc leaf <- mkSwayResetLeaf;
	interface rst = leaf.rst;
	method Bool ready = leaf.ready;
`endif
endmodule

endpackage
