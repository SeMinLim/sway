// Observe accepted issuer transactions without changing the BSV fixture or DUT.
module monitor;
  integer issue_run = 0;
  integer max_issue_run = 0;
  integer issued = 0;
  integer restart_requests = 0;
  integer restarts = 0;
  always @(negedge main.CLK) begin
    if (main.RST) begin
      if (main.top.WILL_FIRE_RL_dut_engine_process2) begin
        issued = issued + 1;
        issue_run = issue_run + 1;
        if (issue_run > max_issue_run) max_issue_run = issue_run;
      end else issue_run = 0;
      if (main.top.dut_engine_rowStartQ$ENQ) restart_requests = restart_requests + 1;
      if (main.top.WILL_FIRE_RL_dut_engine_processRestart) restarts = restarts + 1;
    end
  end
  final begin
    $display("LINEAR_CONTROL issued=%0d max_consecutive_issue=%0d restart_requests=%0d restarts=%0d", issued, max_issue_run, restart_requests, restarts);
  end
endmodule
