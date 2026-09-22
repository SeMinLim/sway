package SwayClock;

import Clocks::*;
`ifdef SWAY_CORE_80MHZ
import SwayPll::*;
`elsif SWAY_CORE_60MHZ
import SwayPll::*;
`else
import PLL::*;
`endif

interface SwayClockIfc;
	interface Clock clk100;
	interface Reset rst25;
	interface Reset rst100;
endinterface

// blueyosys exports !PLL_LOCK as a Reset. Convert that active-high indication
// to BSV's active-low reset and synchronize release in each destination domain.
(* no_default_clock, no_default_reset *)
module mkSwayClock#(Clock clk25)(SwayClockIfc);
`ifdef SWAY_CORE_80MHZ
	SwayPllIfc pll <- mkSwayPll80(clk25);
	Clock coreClock = pll.clk80;
	Reset lockReset <- mkResetInverter(pll.lockReset, clocked_by coreClock);
`elsif SWAY_CORE_60MHZ
	SwayPll60Ifc pll <- mkSwayPll60(clk25);
	Clock coreClock = pll.clk60;
	Reset lockReset <- mkResetInverter(pll.lockReset, clocked_by coreClock);
`else
	PLLIfc pll <- mkPllFast(clk25);
	Clock coreClock = pll.clk_100mhz;
	Reset lockReset <- mkResetInverter(pll.rst_100mhz, clocked_by coreClock);
`endif
	Reset uartReset <- mkAsyncReset(2, lockReset, clk25);
	Reset coreReset <- mkAsyncReset(2, lockReset, coreClock);

	// Retain the existing top-level interface names for all core frequencies.
	interface clk100 = coreClock;
	interface rst25 = uartReset;
	interface rst100 = coreReset;
endmodule

endpackage
