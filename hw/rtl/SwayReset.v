// Separate, kept leaf registers prevent synthesis from merging the reset tree.
// Ordinary fabric routing keeps every leaf-to-LSR path visible to timing analysis.
module SwayReset (
	input wire CLK,
	input wire RST_IN_N,
	output wire RST_OUT_N,
	output wire READY
);
	wire firstStage;
	(* keep = 1 *) TRELLIS_FF #(
		.CEMUX("1"), .CLKMUX("CLK"), .LSRMUX("INV"),
		.REGSET("RESET"), .SRMODE("ASYNC"), .GSR("DISABLED")
	) firstReset (
		.CLK(CLK), .CE(1'b1), .DI(1'b1), .LSR(RST_IN_N), .Q(firstStage)
	);
	(* keep = 1 *) TRELLIS_FF #(
		.CEMUX("1"), .CLKMUX("CLK"), .LSRMUX("INV"),
		.REGSET("RESET"), .SRMODE("ASYNC"), .GSR("DISABLED")
	) secondReset (
		.CLK(CLK), .CE(1'b1), .DI(firstStage), .LSR(RST_IN_N), .Q(RST_OUT_N)
	);
	assign READY = RST_OUT_N;
endmodule
