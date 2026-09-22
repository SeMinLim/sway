// Project Trellis ecppll 1.4-82-g3afe7b5: -i 25 -o 60 --internal_feedback.
// INT_OP feedback gives 25 / 5 * 12 = 60 MHz and a 600 MHz VCO.
module SwayPll60 (
	input wire clk_25mhz,
	output wire clk_60mhz,
	output wire lockedn
);
	wire clkfb;
	wire locked;
	assign lockedn = !locked;

	(* FREQUENCY_PIN_CLKI="25" *)
	(* FREQUENCY_PIN_CLKOP="60" *)
	(* ICP_CURRENT="12" *) (* LPF_RESISTOR="8" *)
	(* MFG_ENABLE_FILTEROPAMP="1" *) (* MFG_GMCREF_SEL="2" *)
	EHXPLLL #(
		.PLLRST_ENA("DISABLED"),
		.INTFB_WAKE("DISABLED"),
		.STDBY_ENABLE("DISABLED"),
		.DPHASE_SOURCE("DISABLED"),
		.OUTDIVIDER_MUXA("DIVA"),
		.OUTDIVIDER_MUXB("DIVB"),
		.OUTDIVIDER_MUXC("DIVC"),
		.OUTDIVIDER_MUXD("DIVD"),
		.CLKI_DIV(5),
		.CLKOP_ENABLE("ENABLED"),
		.CLKOP_DIV(10),
		.CLKOP_CPHASE(4),
		.CLKOP_FPHASE(0),
		.FEEDBK_PATH("INT_OP"),
		.CLKFB_DIV(12)
	) pll_i (
		.RST(1'b0),
		.STDBY(1'b0),
		.CLKI(clk_25mhz),
		.CLKOP(clk_60mhz),
		.CLKFB(clkfb),
		.CLKINTFB(clkfb),
		.PHASESEL0(1'b0),
		.PHASESEL1(1'b0),
		.PHASEDIR(1'b1),
		.PHASESTEP(1'b1),
		.PHASELOADREG(1'b1),
		.PLLWAKESYNC(1'b0),
		.ENCLKOP(1'b0),
		.LOCK(locked)
	);
endmodule
