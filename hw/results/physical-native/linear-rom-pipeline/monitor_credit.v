module monitor;
  integer cycle = 0;
  integer issued = 0;
  integer captured = 0;
  integer consumed = 0;
  integer max_credit = 0;
  integer full_stalls = 0;
  integer issue_run = 0;
  integer max_issue_run = 0;
  integer restart_requests = 0;
  integer restarts = 0;
  reg [2:0] credit;
  initial begin
    wait(main.RST == 1);
    repeat (100) @(negedge main.CLK);
    force main.top.dut_engine_multipliers_0_aWire$whas = 1'b0;
    repeat (80) @(negedge main.CLK);
    release main.top.dut_engine_multipliers_0_aWire$whas;
    repeat (500) @(negedge main.CLK);
    force main.top.dut_engine_multipliers_0_aWire$whas = 1'b0;
    repeat (100) @(negedge main.CLK);
    release main.top.dut_engine_multipliers_0_aWire$whas;
  end
  always @(posedge main.CLK) begin
    cycle = cycle + 1;
    if (!main.RST) begin
      issued=0; captured=0; consumed=0; issue_run=0; max_issue_run=0;
      restart_requests=0; restarts=0;
    end else begin
      credit = main.top.dut_engine_readAcceptedCnt - main.top.dut_engine_readConsumedCnt;
      if (credit > 4) begin $display("CREDIT_FAIL count=%0d",credit); $fatal; end
      if (credit > max_credit) max_credit = credit;
      if (credit == 4 && !main.top.dut_engine_multipliers_0_aWire$whas) full_stalls=full_stalls+1;
      if (main.top.WILL_FIRE_RL_dut_engine_process2_1) issued=issued+1;
      if (main.top.dut_engine_operandQ$ENQ) captured=captured+1;
      if (main.top.dut_engine_multipliers_0_aWire$whas) consumed=consumed+1;
      if (main.top.dut_engine_readValidR[1] && !main.top.dut_engine_operandQ$ENQ) begin
        $display("CREDIT_FAIL dropped capture cycle=%0d",cycle); $fatal;
      end
      if (main.top.WILL_FIRE_RL_dut_engine_process2) begin
        issue_run=issue_run+1; if(issue_run>max_issue_run)max_issue_run=issue_run;
      end else issue_run=0;
      if(main.top.dut_engine_rowStartQ$ENQ)restart_requests=restart_requests+1;
      if(main.top.WILL_FIRE_RL_dut_engine_processRestart)restarts=restarts+1;
    end
  end
  final begin
    $display("CREDIT_RESULT issued=%0d captured=%0d consumed=%0d max_credit=%0d full_stalls=%0d max_consecutive_issue=%0d restart_requests=%0d restarts=%0d",issued,captured,consumed,max_credit,full_stalls,max_issue_run,restart_requests,restarts);
  end
endmodule
