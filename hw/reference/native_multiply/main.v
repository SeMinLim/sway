module main;
	reg CLK = 0;
	reg RST_N = 0;
	always #5 CLK = ~CLK;
	mkTbNativeMultiply dut(.CLK(CLK), .RST_N(RST_N));
	initial begin
		repeat (5) @(negedge CLK);
		RST_N = 1;
	end
endmodule
