#!/usr/bin/env python3
"""Generate a passive monitor for the baseline affine accumulation circuits."""

import json
from pathlib import Path
import re


def generate(rtl, output):
    text = Path(rtl).read_text()
    output = Path(output)
    top = "observation_top.top"
    names = sorted(set(re.findall(r"\b(dut_\w+)_productQ\$D_OUT\b", text)))
    names = [name for name in names if re.search(r"reg \[23\s*:\s*0\] " + name + r"_sumR;", text)]
    if len(names) != 17:
        raise AssertionError(f"Expected 17 affine accumulation lanes, found {len(names)}")
    units = []
    for index, name in enumerate(names):
        for signal in (name + "_sumR", name + "_sumR$D_IN", name + "_sumR$EN",
                       name + "_productQ$D_OUT", name + "_productQ$DEQ",
                       name + "_sumQ$D_IN", name + "_sumQ$D_OUT", name + "_sumQ$ENQ",
                       "WILL_FIRE_RL_" + name + "_process1",
                       "WILL_FIRE_RL_" + name + "_process3",
                       "WILL_FIRE_RL_" + name + "_process3Last"):
            if signal not in text:
                raise AssertionError("Missing signal: " + signal)
        if not re.search(r"reg \[23\s*:\s*0\] " + name + r"_sumR;", text):
            raise AssertionError("Expected an INT24 sumR: " + name)
        units.append({"id": index, "name": name, "stage": name.removesuffix("_engine"),
                      "lane": 0, "sum_bits": 24, "product_bits": 16,
                      "request_cycle_equals_capture_cycle": True})
    (output / "adder_units.json").write_text(json.dumps(units, indent=2) + "\n")
    lines = ["`timescale 1ns/1ps", "module sway_adder_observer;",
             "integer trace_file, init_file, check_file;",
             "integer captured_cycle = -1;",
             "integer operation_count = 0, capture_checks = 0, state_checks = 0;",
             "reg checked_edge = 0;", "initial begin",
             '  trace_file = $fopen("../adder_transactions.csv", "w");',
             '  init_file = $fopen("../adder_initializations.csv", "w");',
             '  check_file = $fopen("../adder_checks.json", "w");',
             '  if (!trace_file || !init_file || !check_file) $fatal(1, "Adder observer output open failed");',
             '  $fwrite(trace_file, "cycle,unit_id,last,a,b,result\\n");',
             '  $fwrite(init_file, "cycle,unit_id\\n");', "end"]
    for unit in units:
        i = unit["id"]
        lines += [f"reg [23:0] state_expected_{i};",
                  f"reg [23:0] result_expected_{i};",
                  f"reg operation_{i}, last_{i};",
                  f"integer sum_wide_{i};"]
    lines += [f"always @(posedge {top}.CLK) begin", f"  if ({top}.RST_N) begin",
              f"    captured_cycle = {top}.cycleCnt;", "    checked_edge = 1;"]
    for unit in units:
        i, name = unit["id"], unit["name"]
        n = top + "." + name
        rule = top + ".WILL_FIRE_RL_" + name
        lines += [f"    operation_{i} = {rule}_process3 || {rule}_process3Last;",
                  f"    last_{i} = {rule}_process3Last;",
                  f"    state_expected_{i} = {n}_sumR$EN ? {n}_sumR$D_IN : {n}_sumR;",
                  f"    if ({rule}_process3 && {rule}_process3Last) $fatal(1, \"Adder rules overlap unit={i}\");",
                  f"    if ({rule}_process1) begin",
                  f"      if (operation_{i} || {n}_sumR$D_IN != 24'd0) $fatal(1, \"Adder initialization invalid unit={i}\");",
                  f'      $fwrite(init_file, "%0d,{i}\\n", captured_cycle);',
                  "    end",
                  f"    if ({n}_productQ$DEQ !== operation_{i}) $fatal(1, \"Adder dequeue mismatch unit={i}\");",
                  f"    if (operation_{i}) begin",
                  f"      sum_wide_{i} = $signed({n}_sumR) + $signed({n}_productQ$D_OUT[16:1]);",
                  f"      result_expected_{i} = last_{i} ? {n}_sumQ$D_IN[30:7] : {n}_sumR$D_IN;",
                  f"      if (sum_wide_{i} < -8388608 || sum_wide_{i} > 8388607) $fatal(1, \"Adder overflow unit={i}\");",
                  f"      if (result_expected_{i} !== sum_wide_{i}[23:0]) $fatal(1, \"Adder arithmetic mismatch unit={i}\");",
                  f"      if (!{n}_sumR$EN || {n}_sumQ$ENQ !== last_{i}) $fatal(1, \"Adder capture enable mismatch unit={i}\");",
                  f"      if (last_{i} && {n}_sumR$D_IN != 24'd0) $fatal(1, \"Final row did not reset sumR unit={i}\");",
                  f'      $fwrite(trace_file, "%0d,{i},%0d,%0d,%0d,%0d\\n", captured_cycle, last_{i},',
                  f"              $signed({n}_sumR), $signed({n}_productQ$D_OUT[16:1]), $signed(result_expected_{i}));",
                  "      operation_count = operation_count + 1;", "    end"]
    lines += ["  end", "end", f"always @(negedge {top}.CLK) begin",
              "  if (checked_edge) begin"]
    for unit in units:
        i, n = unit["id"], top + "." + unit["name"]
        lines += [f"    if ({n}_sumR !== state_expected_{i}) $fatal(1, \"Adder retained-state mismatch unit={i}\");",
                  "    state_checks = state_checks + 1;",
                  f"    if (operation_{i}) begin",
                  f"      if (last_{i}) begin",
                  f"        if ({n}_sumQ$D_OUT[30:7] !== result_expected_{i}) $fatal(1, \"Adder final result not captured unit={i}\");",
                  "      end else begin",
                  f"        if ({n}_sumR !== result_expected_{i}) $fatal(1, \"Adder result not captured unit={i}\");",
                  "      end", "      capture_checks = capture_checks + 1;", "    end"]
    lines += ["    checked_edge = 0;", "  end", "end", "final begin",
              '  $fwrite(check_file, "{\\\"operations\\\":%0d,\\\"capture_checks\\\":%0d,\\\"state_checks\\\":%0d,\\\"last_edge_cycle\\\":%0d}\\n", operation_count, capture_checks, state_checks, captured_cycle);',
              "  $fclose(trace_file);", "  $fclose(init_file);", "  $fclose(check_file);",
              "end", "endmodule", ""]
    path = output / "adder_observer.v"
    path.write_text("\n".join(lines))
    return path
