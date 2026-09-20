// Uses installed BSC SyncResetA.v and the Yosys TRELLIS_FF primitive model
// (lattice/common_sim.vh), together with production rtl/SwayReset.v.
// mkAsyncReset(2) maps to RSTDELAY=1: two root edges plus two leaf edges.
// Functional reset regression only; routed timing is checked separately.
`timescale 1ns/1ps

module TestLocalReset;
	reg clk100 = 0;
	reg clk25 = 0;
	reg lock_n = 1;
	wire root100_n, root25_n, leaf100_n, leaf25_n, ready100, ready25;
	integer repetition;

	always #5 clk100 = ~clk100;
	always #20 clk25 = ~clk25;
	SyncResetA #(.RSTDELAY(1)) root100(.IN_RST(lock_n), .CLK(clk100), .OUT_RST(root100_n));
	SyncResetA #(.RSTDELAY(1)) root25(.IN_RST(lock_n), .CLK(clk25), .OUT_RST(root25_n));
	SwayReset leaf100(.CLK(clk100), .RST_IN_N(root100_n), .RST_OUT_N(leaf100_n), .READY(ready100));
	SwayReset leaf25(.CLK(clk25), .RST_IN_N(root25_n), .RST_OUT_N(leaf25_n), .READY(ready25));

	initial begin
		#2;
		for (repetition = 0; repetition < 2; repetition = repetition + 1) begin
			lock_n = 0;
			#1;
			if (leaf100_n !== 0 || leaf25_n !== 0 || ready100 !== 0 || ready25 !== 0)
				$fatal(1, "SWAY_RESET_FAIL asynchronous assertion");
			#4;
			lock_n = 1;
			fork
				begin
					repeat (3) begin
						@(posedge clk100);
						#1;
						if (leaf100_n !== 0 || ready100 !== 0) $fatal(1, "SWAY_RESET_FAIL early core release");
					end
					@(posedge clk100);
					#1;
					if (root100_n !== 1 || leaf100_n !== 1 || ready100 !== 1)
						$fatal(1, "SWAY_RESET_FAIL late core release");
				end
				begin
					repeat (3) begin
						@(posedge clk25);
						#1;
						if (leaf25_n !== 0 || ready25 !== 0) $fatal(1, "SWAY_RESET_FAIL early UART release");
					end
					@(posedge clk25);
					#1;
					if (root25_n !== 1 || leaf25_n !== 1 || ready25 !== 1)
						$fatal(1, "SWAY_RESET_FAIL late UART release");
				end
			join
			#2;
		end
		$display("SWAY_RESET_PASS assertion=async root_edges=2 leaf_edges=2 total_edges=4 domains_mhz=100,25 repetitions=2");
		$finish(0);
	end

	initial begin
		#2000;
		$fatal(1, "SWAY_RESET_FAIL watchdog");
	end
endmodule
