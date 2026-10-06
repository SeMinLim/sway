#!/usr/bin/env python3
"""Read-only observation of affine accumulation and selected requantization."""

import json
from pathlib import Path
import re


TOP = "observation_top.top"


def function_body(text, name):
    match = re.search(r"function [^\n]+\b" + name + r"\([^;]+;(.*?)endfunction", text, re.S)
    if not match:
        raise AssertionError("Missing parameter function: " + name)
    return match.group(1)


def layer_parameters(text, name):
    return {int(layer): int(value) for layer, value in re.findall(
        r"if \( layerId == (\d+) \) result = (-?\d+);", function_body(text, name))}


def build_units(rtl_text, parameters_path):
    parameters = Path(parameters_path).read_text()
    layer_input = layer_parameters(parameters, "layerInputScale")
    layer_weight = layer_parameters(parameters, "layerWeightScale")
    layer_output = layer_parameters(parameters, "layerOutputScale")
    layer_bias = layer_parameters(parameters, "layerBiasScale")
    has_bias = {int(layer) for layer in re.findall(
        r"if \( layerId == (\d+) \) result = True;", function_body(parameters, "layerHasBias"))}
    layer_sizes = layer_parameters(parameters, "layerInputSize")
    block_body = function_body(parameters, "blockScale")
    block_scales = []
    for block in range(2):
        body = re.search(r"if \( blockId == " + str(block) + r" \) begin(.*?)\n\tend", block_body, re.S)
        if not body:
            raise AssertionError("Missing block scales")
        block_scales.append({name: int(value) for name, value in re.findall(
            r'if \( suffix == "([^"]+)" \) result = (-?\d+);', body.group(1))})
    stages = sorted(stage for stage in set(re.findall(r"\b(dut_\w+)_sumR\$EN\b", rtl_text))
                    if stage + "_affineQ$DEQ" in rtl_text)
    if len(stages) != 17:
        raise AssertionError("Expected seventeen single-lane affine engines")
    affine = []
    for stage in stages:
        if stage == "dut_embedding_engine":
            layer, rows, tokens = 0, 20, 16
        elif stage == "dut_headHidden_engine":
            layer, rows, tokens = 9, 20, 1
        elif stage == "dut_headOutput_engine":
            layer, rows, tokens = 10, 57, 1
        else:
            match = re.fullmatch(r"dut_block([01])_(\w+)", stage)
            if not match:
                raise AssertionError("Unexpected affine engine: " + stage)
            block, suffix = int(match.group(1)), match.group(2)
            offset, rows = {"mainProjection": (0, 40), "gateProjection": (0, 40),
                            "deltaInputProjection": (1, 2), "bProjection": (1, 8),
                            "cProjection": (1, 8), "deltaProjection_engine": (2, 40),
                            "outputProjection_engine": (3, 20)}[suffix]
            layer, tokens = 1 + block * 4 + offset, 16
        found_rows = sorted({int(row) for row in re.findall(re.escape(stage) + r"_outputR_(\d+)\$EN", rtl_text)})
        if found_rows != list(range(rows)):
            raise AssertionError("Unexpected affine output rows: " + stage)
        product_exp = layer_input[layer] + layer_weight[layer]
        common_exp = min(product_exp, layer_bias[layer]) if layer in has_bias else product_exp
        affine.append({"stage": stage, "layer": layer, "rows": rows, "tokens": tokens,
                       "row_length": layer_sizes[layer], "from_exp": common_exp,
                       "to_exp": layer_output[layer]})
    units = []
    for engine in affine:
        stage = engine["stage"]
        units.append({"name": stage + "_accumulate", "stage": stage, "kind": "adder",
                      "input_width": 24, "operand_b_width": 16, "output_width": 24,
                      "row_length": engine["row_length"],
                      "expected_per_frame": engine["tokens"] * engine["rows"] * engine["row_length"],
                      "receiver": "register", "receipt": "next_edge_state_readback",
                      "tag": "0: sumR write; 1: final-row sumQ enqueue and sumR reset",
                      "fire_signal": stage + "_productQ$DEQ",
                      "input_a_signal": stage + "_sumR",
                      "input_b_signal": stage + "_productQ$D_OUT[16:1]",
                      "result_signal": "MUX_" + stage + "_sumR$write_1__VAL_1"})
    for engine in affine:
        stage = engine["stage"]
        assignment = re.search(r"assign " + re.escape(stage) + r"_outputR_0\$D_IN\s*=\s*(.*?);", rtl_text, re.S)
        pure = re.search(r"\?\s*([\w$]+)\s*:\s*8'd0\s*$", assignment.group(1))
        if not pure:
            raise AssertionError("Unexpected affine requantization result: " + stage)
        units.append({"name": stage + "_requant", "stage": stage, "kind": "requant",
                      "input_width": 24, "output_width": 8,
                      "from_exp": engine["from_exp"], "to_exp": engine["to_exp"],
                      "rows": engine["rows"], "expected_per_frame": engine["tokens"] * engine["rows"],
                      "receiver": "register", "receipt": "next_edge_state_readback", "tag": "output row",
                      "fire_signal": stage + "_affineQ$DEQ",
                      "input_a_signal": stage + "_affineQ$D_OUT[31:8]",
                      "result_signal": pure.group(1), "capture_signal": stage + "_outputR_0$D_IN"})
    for block in range(2):
        stage, scales = "dut_block" + str(block), block_scales[block]
        assignment = re.search(r"assign " + re.escape(stage) + r"_xR_0\$D_IN\s*=\s*([\w$]+)\s*;", rtl_text)
        if not assignment:
            raise AssertionError("Missing convolution requant result")
        units.append({"name": stage + "_conv_requant", "stage": stage + "_convolution", "kind": "requant",
                      "input_width": 18, "output_width": 8,
                      "from_exp": min(scales["convInput"] + scales["convWeight"], scales["convBias"]),
                      "to_exp": scales["conv"], "rows": 40, "expected_per_frame": 16 * 40,
                      "receiver": "register", "receipt": "next_edge_state_readback", "tag": "convolution channel",
                      "fire_signal": stage + "_convAffineQ$DEQ",
                      "input_a_signal": stage + "_convAffineQ$D_OUT[17:0]",
                      "result_signal": assignment.group(1), "capture_signal": stage + "_xR_0$D_IN"})
    for block in range(2):
        stage, scales = "dut_block" + str(block) + "_scan", block_scales[block]
        assignment = re.search(r"assign " + re.escape(stage) + r"_quantizedQ\$D_IN\s*=\s*\{(.*?)\}\s*;", rtl_text, re.S)
        pieces = [piece.strip() for piece in assignment.group(1).split(",")]
        if len(pieces) != 4:
            raise AssertionError("Unexpected quantized scan tuple layout")
        for part, source_slice, result_slice, pure in (("A", "80:65", "64:57", pieces[1]),
                                                     ("B", "64:49", "56:49", pieces[2])):
            units.append({"name": stage + "_delta" + part + "_requant", "stage": stage, "kind": "requant",
                          "input_width": 16, "output_width": 8,
                          "from_exp": scales["delta"] + scales[part],
                          "to_exp": scales["expInput" if part == "A" else "Bbar"],
                          "expected_per_frame": 16 * 40 * 8, "receiver": "fifo", "receipt": "fifo_dequeue",
                          "tag": "channel[5:0] concatenated with part[2:0]", "result_slice": result_slice,
                          "fire_signal": stage + "_quantizedQ$ENQ",
                          "input_a_signal": stage + "_deltaProductsQ$D_OUT[" + source_slice + "]",
                          "result_signal": pure,
                          "capture_signal": stage + "_quantizedQ$D_IN[" + result_slice + "]"})
    for index, unit in enumerate(units):
        unit["id"] = index
        unit["capture_latency_cycles"] = 0
        for key in ("fire_signal", "input_a_signal", "input_b_signal", "result_signal", "capture_signal"):
            if key in unit and unit[key].split("[")[0] not in rtl_text:
                raise AssertionError("Missing observed signal: " + unit[key])
    return units


def generate(rtl, directory, parameters_path):
    directory = Path(directory)
    units = build_units(Path(rtl).read_text(), parameters_path)
    (directory / "extra_units.json").write_text(json.dumps(units, indent=2) + "\n")
    lines = ["`timescale 1ns/1ps", "module sway_extra_observer;", "integer trace_file;",
             "initial begin", '  trace_file = $fopen("../extra_operations.csv", "w");',
             '  if (!trace_file) $fatal(1, "Extra observer output open failed");',
             '  $fwrite(trace_file, "cycle,unit_id,event,a,b,result,tag\\n");', "end"]
    for unit in units:
        index, stage = unit["id"], unit["stage"]
        if unit["receiver"] == "register":
            lines += [f"reg pending_{index} = 0;", f"integer pending_tag_{index} = 0;",
                      f"reg signed [{unit['output_width'] - 1}:0] received_{index};"]
            if unit["kind"] == "adder":
                lines += [f"always @* received_{index} = pending_tag_{index} == 1 ?",
                          f"  {TOP}.{stage}_sumQ$D_OUT[30:7] : {TOP}.{stage}_sumR;"]
            else:
                base = stage + "_outputR" if unit["input_width"] == 24 else stage.removesuffix("_convolution") + "_xR"
                lines += ["always @* begin", f"  case (pending_tag_{index})"]
                for row in range(unit["rows"]):
                    lines.append(f"    {row}: received_{index} = {TOP}.{base}_{row};")
                lines += [f"    default: received_{index} = 8'bx;", "  endcase", "end"]
    lines += [f"always @(posedge {TOP}.CLK) begin", f"  if ({TOP}.RST_N) begin"]
    for unit in units:
        index, stage = unit["id"], unit["stage"]
        fire = TOP + "." + unit["fire_signal"]
        a = TOP + "." + unit["input_a_signal"]
        b = TOP + "." + unit["input_b_signal"] if unit["kind"] == "adder" else "1'd0"
        if unit["kind"] == "adder":
            tag = f"{TOP}.{stage}_productQ$D_OUT[0]"
            result = f"({tag} ? {TOP}.{stage}_sumQ$D_IN[30:7] : {TOP}.{stage}_sumR$D_IN)"
        elif unit["input_width"] == 24:
            tag = f"{TOP}.{stage}_affineQ$D_OUT[7:1]"
            result = TOP + "." + unit["capture_signal"]
        elif unit["input_width"] == 18:
            tag = f"{TOP}.{stage.removesuffix('_convolution')}_convAffineQ$D_OUT[23:18]"
            result = TOP + "." + unit["capture_signal"]
        else:
            tag = f"{TOP}.{stage}_deltaProductsQ$D_OUT[89:81]"
            result = TOP + "." + unit["capture_signal"]
        lines += [f"    if ({fire})",
                  '      $fwrite(trace_file, "%0d,%0d,0,%0d,%0d,%0d,%0d\\n",',
                  f"              {TOP}.cycleCnt, {index}, $signed({a}), $signed({b}), $signed({result}), {tag});"]
        if unit["receiver"] == "register":
            lines += [f"    if (pending_{index})",
                      '      $fwrite(trace_file, "%0d,%0d,1,0,0,%0d,%0d\\n",',
                      f"              {TOP}.cycleCnt, {index}, $signed(received_{index}), pending_tag_{index});",
                      f"    pending_{index} <= {fire};", f"    if ({fire}) pending_tag_{index} <= {tag};"]
        else:
            lines += [f"    if ({TOP}.{stage}_quantizedQ$DEQ)",
                      '      $fwrite(trace_file, "%0d,%0d,1,0,0,%0d,%0d\\n",',
                      f"              {TOP}.cycleCnt, {index}, $signed({TOP}.{stage}_quantizedQ$D_OUT[{unit['result_slice']}]),",
                      f"              {TOP}.{stage}_quantizedQ$D_OUT[73:65]);"]
    lines += ["  end", "end", "final begin",
              f'  $fwrite(trace_file, "%0d,-1,2,0,0,0,0\\n", {TOP}.cycleCnt);',
              "  $fclose(trace_file);", "end", "endmodule", ""]
    path = directory / "extra_observer.v"
    path.write_text("\n".join(lines))
    return path
