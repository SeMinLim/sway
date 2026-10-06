#!/usr/bin/env python3
"""Generate a read-only monitor for the frozen baseline's multiplier interfaces."""

import json
from pathlib import Path
import re


def generate(rtl, directory, observed, extra_resources=False):
    directory = Path(directory)
    text = Path(rtl).read_text()
    names = sorted(set(re.findall(r"\b(dut_\w+)_acceptedCnt\$EN\b", text)))
    if len(names) != 45:
        raise AssertionError(f"Expected 45 baseline multipliers, found {len(names)}")
    units = [{"id": index, "name": name} for index, name in enumerate(names)]
    stages = json.loads(Path(__file__).with_name("stages.json").read_text())
    top = "observation_top.top"
    for stage in stages:
        for key in ("input_wait_expression", "output_wait_expression"):
            stage[key] = stage[key].replace("{top}", top)
            for signal in re.findall(re.escape(top) + r"\.([\w$]+)", stage[key]):
                if signal not in text:
                    raise AssertionError("Missing monitored stage signal: " + signal)
    for unit in units:
        for suffix in ("_acceptedCnt$EN", "_consumedCnt$EN", "_dsp$dataax",
                       "_dsp$dataay", "_resultQ$D_OUT"):
            if unit["name"] + suffix not in text:
                raise AssertionError("Missing multiplier interface: " + unit["name"] + suffix)
    (directory / "units.json").write_text(json.dumps(units, indent=2) + "\n")
    (directory / "stages.json").write_text(json.dumps(stages, indent=2) + "\n")
    # Match the BSC main.v reset and rising-edge sequence without its #0 delay.
    wrapper = ["`timescale 1ns/1ps", "module observation_top;",
               "reg CLK = 0;", "reg RST_N = 0;",
               "mkTbSwayKernel top(.CLK(CLK), .RST_N(RST_N));",
               "initial begin", "  #1; CLK = 1;", "  #1; RST_N = 1;",
               "  #3; CLK = 0;", "  forever begin #5; CLK = ~CLK; end", "end"]
    if observed:
        wrapper.append("sway_observer observer();")
        if extra_resources:
            wrapper.append("sway_extra_observer extra_observer();")
    wrapper.extend(["endmodule", ""])
    wrapper_path = directory / ("observed_top.v" if observed else "control_top.v")
    wrapper_path.write_text("\n".join(wrapper))
    lines = ["`timescale 1ns/1ps", "module sway_observer;",
             "integer trace_file, wait_file;", "reg [24:0] previous_input, previous_output;",
             "wire [24:0] input_wait, output_wait;", "reg initialized = 0;", "integer stage;",
             "initial begin",
             '  trace_file = $fopen("../transactions.csv", "w");',
             '  wait_file = $fopen("../stage_waits.csv", "w");',
             '  if (!trace_file || !wait_file) $fatal(1, "Observer output open failed");',
             '  $fwrite(trace_file, "cycle,unit_id,put,get,a,b,result\\n");',
             '  $fwrite(wait_file, "cycle,stage,input_wait,output_wait\\n");', "end"]
    for stage in stages:
        lines.append(f"assign input_wait[{stage['id']}] = {stage['input_wait_expression']};")
        lines.append(f"assign output_wait[{stage['id']}] = {stage['output_wait_expression']};")
    lines += [f"always @(posedge {top}.CLK) begin", f"  if ({top}.RST_N) begin"]
    for unit in units:
        name = top + "." + unit["name"]
        put, get = name + "_acceptedCnt$EN", name + "_consumedCnt$EN"
        lines += [f"    if ({put} || {get})",
                  '      $fwrite(trace_file, "%0d,%0d,%0d,%0d,%0d,%0d,%0d\\n",',
                  f"              {top}.cycleCnt, {unit['id']}, {put}, {get},",
                  f"              $signed({put} ? {name}_dsp$dataax : 18'd0),",
                  f"              $signed({put} ? {name}_dsp$dataay : 18'd0),",
                  f"              $signed({get} ? {name}_resultQ$D_OUT : 36'd0));"]
    lines += ["    for (stage = 0; stage < 25; stage = stage + 1) begin",
              "      if (!initialized || input_wait[stage] !== previous_input[stage] || output_wait[stage] !== previous_output[stage])",
              f'        $fwrite(wait_file, "%0d,%0d,%0d,%0d\\n", {top}.cycleCnt, stage, input_wait[stage], output_wait[stage]);',
              "    end", "    previous_input = input_wait;", "    previous_output = output_wait;",
              "    initialized = 1;", "  end", "end", "final begin",
              f'  $fwrite(wait_file, "%0d,-1,0,0\\n", {top}.cycleCnt);',
              "  $fclose(trace_file);", "  $fclose(wait_file);", "end", "endmodule", ""]
    observer = directory / "observer.v"
    observer.write_text("\n".join(lines))
    return wrapper_path, observer
