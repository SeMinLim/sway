module main;
reg CLK=0;
reg RST=0;
mkTbLinearSelect top(.CLK(CLK), .RST_N(RST));
always #5 CLK=~CLK;
wire [2:0] outstanding = top.dut_engine_readAcceptedCnt - top.dut_engine_readConsumedCnt;
initial begin
  repeat(5) @(negedge CLK); RST=1;
  repeat(140) @(negedge CLK);
  if (outstanding != 3'd4) begin
    $display("WARM_FAIL expected four outstanding ROM credits"); $fatal;
  end
  $display("WARM_RESET outstanding_rom=4"); RST=0;
  repeat(5) @(negedge CLK); RST=1;
end
endmodule
