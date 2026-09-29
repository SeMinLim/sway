module sway_mult18x18d (
	output [35:0] dataout,
	input [17:0] dataax,
	input [17:0] dataay,
	input clk,
	input rstn
);
`ifdef SYNTHESIS
	wire reset;
	assign reset = ~rstn;

	// Use all internal MULT18X18D register stages for the signed INT18 path.
	// CE0 remains asserted so the DSP pipeline advances on every clock cycle.
	// Unused SRIA/SRIB cascade pins have no route from general fabric.
	// Leave them open, along with unused clock/control banks 1 through 3.
	MULT18X18D #(
		.REG_INPUTA_CLK("CLK0"),
		.REG_INPUTA_CE("CE0"),
		.REG_INPUTA_RST("RST0"),
		.REG_INPUTB_CLK("CLK0"),
		.REG_INPUTB_CE("CE0"),
		.REG_INPUTB_RST("RST0"),
		.REG_PIPELINE_CLK("CLK0"),
		.REG_PIPELINE_CE("CE0"),
		.REG_PIPELINE_RST("RST0"),
		.REG_OUTPUT_CLK("CLK0"),
		.REG_OUTPUT_CE("CE0"),
		.REG_OUTPUT_RST("RST0"),
		.GSR("ENABLED"),
		.RESETMODE("SYNC")
	) multiplier (
		.A0(dataax[0]),
		.A1(dataax[1]),
		.A2(dataax[2]),
		.A3(dataax[3]),
		.A4(dataax[4]),
		.A5(dataax[5]),
		.A6(dataax[6]),
		.A7(dataax[7]),
		.A8(dataax[8]),
		.A9(dataax[9]),
		.A10(dataax[10]),
		.A11(dataax[11]),
		.A12(dataax[12]),
		.A13(dataax[13]),
		.A14(dataax[14]),
		.A15(dataax[15]),
		.A16(dataax[16]),
		.A17(dataax[17]),
		.B0(dataay[0]),
		.B1(dataay[1]),
		.B2(dataay[2]),
		.B3(dataay[3]),
		.B4(dataay[4]),
		.B5(dataay[5]),
		.B6(dataay[6]),
		.B7(dataay[7]),
		.B8(dataay[8]),
		.B9(dataay[9]),
		.B10(dataay[10]),
		.B11(dataay[11]),
		.B12(dataay[12]),
		.B13(dataay[13]),
		.B14(dataay[14]),
		.B15(dataay[15]),
		.B16(dataay[16]),
		.B17(dataay[17]),
		.C0(1'b0),
		.C1(1'b0),
		.C2(1'b0),
		.C3(1'b0),
		.C4(1'b0),
		.C5(1'b0),
		.C6(1'b0),
		.C7(1'b0),
		.C8(1'b0),
		.C9(1'b0),
		.C10(1'b0),
		.C11(1'b0),
		.C12(1'b0),
		.C13(1'b0),
		.C14(1'b0),
		.C15(1'b0),
		.C16(1'b0),
		.C17(1'b0),
		.SIGNEDA(1'b1),
		.SIGNEDB(1'b1),
		.SOURCEA(1'b0),
		.SOURCEB(1'b0),
		.CLK0(clk),
		.CE0(1'b1),
		.RST0(reset),
		.P0(dataout[0]),
		.P1(dataout[1]),
		.P2(dataout[2]),
		.P3(dataout[3]),
		.P4(dataout[4]),
		.P5(dataout[5]),
		.P6(dataout[6]),
		.P7(dataout[7]),
		.P8(dataout[8]),
		.P9(dataout[9]),
		.P10(dataout[10]),
		.P11(dataout[11]),
		.P12(dataout[12]),
		.P13(dataout[13]),
		.P14(dataout[14]),
		.P15(dataout[15]),
		.P16(dataout[16]),
		.P17(dataout[17]),
		.P18(dataout[18]),
		.P19(dataout[19]),
		.P20(dataout[20]),
		.P21(dataout[21]),
		.P22(dataout[22]),
		.P23(dataout[23]),
		.P24(dataout[24]),
		.P25(dataout[25]),
		.P26(dataout[26]),
		.P27(dataout[27]),
		.P28(dataout[28]),
		.P29(dataout[29]),
		.P30(dataout[30]),
		.P31(dataout[31]),
		.P32(dataout[32]),
		.P33(dataout[33]),
		.P34(dataout[34]),
		.P35(dataout[35])
	);
`else
	// Fixed three-stage, CE-always-enabled configuration for Verilog simulation.
	reg signed [17:0] aR, bR;
	reg signed [35:0] productR, outputR;
	assign dataout = outputR;
	always @(posedge clk) begin
		if (!rstn) begin
			aR <= 0;
			bR <= 0;
			productR <= 0;
			outputR <= 0;
		end else begin
			aR <= $signed(dataax);
			bR <= $signed(dataay);
			productR <= aR * bR;
			outputR <= productR;
		end
	end
`endif
endmodule
