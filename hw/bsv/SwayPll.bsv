package SwayPll;

interface SwayPllIfc;
	interface Clock clk80;
	interface Reset lockReset;
endinterface

import "BVI" SwayPll =
module mkSwayPll80#(Clock clk25)(SwayPllIfc);
	default_clock no_clock;
	default_reset no_reset;
	input_clock (clk_25mhz) = clk25;
	output_clock clk80(clk_80mhz);
	output_reset lockReset(lockedn) clocked_by(clk80);
endmodule

interface SwayPll60Ifc;
	interface Clock clk60;
	interface Reset lockReset;
endinterface

import "BVI" SwayPll60 =
module mkSwayPll60#(Clock clk25)(SwayPll60Ifc);
	default_clock no_clock;
	default_reset no_reset;
	input_clock (clk_25mhz) = clk25;
	output_clock clk60(clk_60mhz);
	output_reset lockReset(lockedn) clocked_by(clk60);
endmodule

endpackage
