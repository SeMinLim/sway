`timescale 1ns/1ps
module main;
  reg CLK = 0;
  reg RST_N = 0;
  always #5 CLK = ~CLK;
  mkTbSwayKernel top(.CLK(CLK), .RST_N(RST_N));

  initial begin
    repeat (5) @(negedge CLK);
    RST_N = 1;
    repeat (40000) @(negedge CLK);
    $display("RESET_TEST_BEFORE sent=%0d received=%0d cycles=%0d", top.sentCnt, top.receivedCnt, top.cycleCnt);
    if (top.sentCnt == 0 || top.sentCnt * 57 <= top.receivedCnt * 320 || top.finishOn !== 0) begin
      $fatal(1, "RESET_TEST_FAIL no in-flight work");
    end
    RST_N = 0;
    repeat (5) @(negedge CLK);
    if (top.sentCnt !== 0 || top.receivedCnt !== 0 || top.cycleCnt !== 0 ||
        top.drainCnt !== 0 || top.finishOn !== 0) begin
      $fatal(1, "RESET_TEST_FAIL testbench counters did not reset");
    end
    $display("RESET_TEST_COUNTERS_CLEAR");
    RST_N = 1;
    $display("RESET_TEST_RESTART");
  end
endmodule
