#!/usr/bin/env python3
"""Audit observed arithmetic against the unmodified, fully mapped ECP5 top."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_mapped_weights import CELL_PORTS, init_parameter, reviewed_library


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def physical_signal(signal):
    return signal.replace("dut_", "main_core_").replace("$", "_")


def signal_bits(nets, signal):
    match = re.fullmatch(r"([^\[]+)(?:\[(\d+)(?::(\d+))?\])?", signal)
    require(match is not None, "Unsupported signal expression: " + signal)
    name, high, low = match.groups()
    require(name in nets, "Mapped signal missing: " + signal)
    bits = nets[name]["bits"]
    if high is not None:
        lo, hi = int(low if low is not None else high), int(high)
        require(0 <= lo <= hi < len(bits), "Mapped signal slice out of bounds")
        bits = bits[lo:hi + 1]
    return bits


def cone(cells, drivers, outputs, boundary):
    found, visiting, used_inputs = set(), set(), set()

    def visit(bit):
        if isinstance(bit, str):
            require(bit in {"0", "1"}, "Undefined mapped constant")
            return
        if bit in boundary:
            used_inputs.add(bit)
            return
        require(bit in drivers, "Undriven mapped arithmetic bit: " + str(bit))
        name = drivers[bit]
        if name in found:
            return
        require(name not in visiting, "Combinational cycle in mapped arithmetic")
        cell = cells[name]
        require(cell["type"] in CELL_PORTS,
                "Foreign state/primitive in arithmetic cone: " + name + " " + cell["type"])
        inputs, outputs = CELL_PORTS[cell["type"]]
        require(set(cell["connections"]) == inputs | outputs,
                "Unexpected primitive port set: " + name)
        visiting.add(name)
        for port, bits in cell["connections"].items():
            require(len(bits) == 1, "Non-scalar ECP5 primitive port")
            require(cell["port_directions"][port] == ("input" if port in inputs else "output"),
                    "Unexpected primitive port direction")
            if port in inputs:
                visit(bits[0])
        visiting.remove(name)
        found.add(name)

    for bit in outputs:
        visit(bit)
    return found, used_inputs


def bit_expression(bit):
    return "1'b" + bit if isinstance(bit, str) else "n" + str(bit)


def vector_expression(bits):
    return "{" + ", ".join(bit_expression(bit) for bit in reversed(bits)) + "}"


def lut_expression(init, bits, width=16):
    return "(" + str(width) + "'h" + format(init, "x") + " >> " + vector_expression(bits) + ")"


def emit_primitive(name, index, cell):
    connections, kind = cell["connections"], cell["type"]
    bit = lambda port: connections[port][0]
    expr = lambda port: bit_expression(bit(port))
    parameters = cell.get("parameters", {})
    lines = ["// " + name]
    if kind == "LUT4":
        require(set(parameters) <= {"INIT"}, "Unknown LUT4 parameter")
        lines.append("assign " + expr("Z") + " = " +
                     lut_expression(init_parameter(parameters, "INIT"), [bit(p) for p in "ABCD"]) + ";")
    elif kind in {"PFUMX", "L6MUX21"}:
        require(not parameters, "Unknown mux parameter")
        selector, high, low = ("C0", "ALUT", "BLUT") if kind == "PFUMX" else ("SD", "D1", "D0")
        lines.append("assign " + expr("Z") + " = " + expr(selector) + " ? " + expr(high) + " : " + expr(low) + ";")
    else:
        require(kind == "CCU2C" and set(parameters) <= {"INIT0", "INIT1", "INJECT1_0", "INJECT1_1"},
                "Unknown carry primitive parameter")
        carry = expr("CIN")
        for half in range(2):
            suffix = str(half)
            prefix = "c" + str(index) + "_" + suffix
            init = init_parameter(parameters, "INIT" + suffix)
            inject = parameters.get("INJECT1_" + suffix, "YES")
            require(inject in {"YES", "NO"}, "Invalid CCU2C injection parameter")
            lines += ["wire " + prefix + "_lut4 = " + lut_expression(init, [bit(p + suffix) for p in "ABCD"]) + ";",
                      "wire " + prefix + "_lut2 = " + lut_expression(init & 15, [bit(p + suffix) for p in "AB"], 4) + ";",
                      "wire " + prefix + "_carry = (~" + prefix + "_lut4 & " +
                      ("1'b0" if inject == "YES" else prefix + "_lut2") + ") | (" + prefix + "_lut4 & " + carry + ");",
                      "assign " + expr("S" + suffix) + " = " + prefix + "_lut4 ^ " +
                      ("1'b0" if inject == "YES" else carry) + ";"]
            carry = prefix + "_carry"
        lines.append("assign " + expr("COUT") + " = " + carry + ";")
    return lines


def compatibility(unit):
    if unit["kind"] == "adder":
        return ["adder", 24, 24]
    return ["requant", unit["input_width"], unit["from_exp"], unit["to_exp"],
            8, "ties_to_even", "saturate"]


def emit_reference(unit, binding):
    index, width = unit["id"], unit["input_width"]
    prefix = "u" + str(index)
    a = vector_expression(binding["input_a_bits"])
    actual = vector_expression(binding["output_bits"])
    lines = ["wire signed [" + str(width - 1) + ":0] " + prefix + "_a = " + a + ";"]
    if unit["kind"] == "adder":
        b = vector_expression(binding["input_b_bits"])
        lines += ["wire signed [15:0] " + prefix + "_b = " + b + ";",
                  "wire [23:0] " + prefix + "_expected = " + prefix + "_a + {{8{" + prefix + "_b[15]}}, " + prefix + "_b};"]
    else:
        shift = unit["to_exp"] - unit["from_exp"]
        if shift >= width:
            lines.append("wire signed [63:0] " + prefix + "_rounded = 0;")
        elif shift > 0:
            sticky = "1'b0" if shift == 1 else "(|" + prefix + "_a[" + str(shift - 2) + ":0])"
            lines += ["wire signed [63:0] " + prefix + "_extended = " + prefix + "_a;",
                      "wire " + prefix + "_increment = " + prefix + "_a[" + str(shift - 1) + "] & (" + sticky + " | " + prefix + "_a[" + str(shift) + "]);",
                      "wire signed [63:0] " + prefix + "_rounded = (" + prefix + "_extended >>> " + str(shift) + ") + $signed({1'b0," + prefix + "_increment});"]
        else:
            require(-shift < 32, "Unexpected large left shift in selected baseline")
            lines += ["wire signed [63:0] " + prefix + "_extended = " + prefix + "_a;",
                      "wire signed [63:0] " + prefix + "_rounded = " + prefix + "_extended <<< " + str(-shift) + ";"]
        lines.append("wire [7:0] " + prefix + "_quantized = " + prefix + "_rounded > 64'sd127 ? 8'h7f : (" +
                     prefix + "_rounded < -64'sd128 ? 8'h80 : " + prefix + "_rounded[7:0]);")
        if binding["guard_bits"]:
            lines.append("wire [7:0] " + prefix + "_expected = " + vector_expression(binding["guard_bits"]) +
                         " < 7'd" + str(unit["rows"]) + " ? " + prefix + "_quantized : 8'd0;")
        else:
            lines.append("wire [7:0] " + prefix + "_expected = " + prefix + "_quantized;")
    lines.append("wire " + prefix + "_ok = " + actual + " == " + prefix + "_expected;")
    return lines


def audit(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    library_version, libraries = reviewed_library(args.cells_sim)
    version = subprocess.check_output([str(args.yosys), "-V"], text=True).strip()
    require("Yosys 0.67+24" in version and "0e82bbefe" in version, "Pinned Yosys version required")
    bsc_version = subprocess.check_output([str(args.bsc), "-v"], text=True)
    require("version 2025.07 (build 282e82e9)" in bsc_version, "Pinned BSC version required")
    require("typedef 4 ParallelismDivisor;" in (args.baseline / "bsv/SwayTypes.bsv").read_text(),
            "Expected unchanged divisor-four baseline")
    sources = sorted([*args.baseline.joinpath("bsv").glob("*.bsv"),
                      *args.baseline.joinpath("generated").glob("*.bsv"),
                      *args.baseline.joinpath("sim").glob("*.bsv"),
                      *args.baseline.joinpath("rtl").glob("*.v"),
                      args.baseline / "generated/test_input.hex", args.baseline / "generated/test_expected.hex"])
    source_hashes = {str(path.relative_to(args.baseline)): digest(path) for path in sources}
    units = json.loads(args.units.read_text())
    require([u["id"] for u in units] == list(range(40)), "Expected forty observed candidates")
    data = json.loads(args.netlist.read_text())
    require("Yosys 0.67+24" in data["creator"] and "0e82bbefe" in data["creator"], "Unexpected netlist producer")
    module = data["modules"]["mkTop"]
    nets, cells = module["netnames"], module["cells"]
    multipliers = sorted(name for name, cell in cells.items() if cell["type"] == "MULT18X18D")
    require(len(multipliers) == 45, "Expected forty-five native multiplier primitives in the same baseline")
    drivers = {}
    for name, cell in cells.items():
        for port, bits in cell["connections"].items():
            if cell["port_directions"][port] == "output":
                for bit in bits:
                    if isinstance(bit, int):
                        require(bit not in drivers, "Multiple mapped bit drivers")
                        drivers[bit] = name
    bindings, resources, all_cones, all_boundary = [], [], set(), set()
    owners, identities = defaultdict(list), {}
    for unit in units:
        a_signal = physical_signal(unit["input_a_signal"])
        a_bits = signal_bits(nets, a_signal)
        b_signal = physical_signal(unit["input_b_signal"]) if unit["kind"] == "adder" else None
        b_bits = signal_bits(nets, b_signal) if b_signal else []
        guard_signal = physical_signal(unit["stage"] + "_affineQ$D_OUT[7:1]") if unit["kind"] == "requant" and unit["input_width"] == 24 else None
        guard_bits = signal_bits(nets, guard_signal) if guard_signal else []
        result_signal = physical_signal(unit["result_signal"] if unit["kind"] == "adder" else unit["capture_signal"])
        result_bits = signal_bits(nets, result_signal)
        require(len(a_bits) == unit["input_width"] and len(result_bits) == unit["output_width"], "Mapped arithmetic width differs")
        require(all(isinstance(bit, int) for bit in a_bits + b_bits), "Candidate arithmetic input has constant bits")
        require(len(set(a_bits + b_bits)) == len(a_bits + b_bits), "Candidate arithmetic input bits already alias")
        boundary = set(a_bits + b_bits + guard_bits)
        for bit in boundary:
            if isinstance(bit, int):
                require(bit in drivers and cells[drivers[bit]]["type"] == "TRELLIS_FF", "Arithmetic input is not the original retained state bit")
        cone_cells, used = cone(cells, drivers, result_bits, boundary)
        require(cone_cells, "Candidate arithmetic collapsed to wires/constants")
        require(set(a_bits + b_bits) <= used, "Some candidate data bits disappeared from mapped cone")
        identity = tuple(result_bits)
        merged = identities.get(identity)
        if merged is None:
            identities[identity] = unit["id"]
        else:
            require(compatibility(unit) == compatibility(units[merged]), "Merged outputs have incompatible formats")
        binding = {"input_a_signal": a_signal, "input_a_bits": a_bits,
                   "input_b_signal": b_signal, "input_b_bits": b_bits,
                   "guard_signal": guard_signal, "guard_bits": guard_bits,
                   "output_signal": result_signal, "output_bits": result_bits}
        bindings.append(binding)
        resource = {"unit_id": unit["id"], "name": unit["name"], "stage": unit["stage"], "kind": unit["kind"],
                    "compatibility_key": compatibility(unit), "verified_present": merged is None,
                    "constant": False, "merged_with": merged, "synthesis_stage": "full synth_ecp5 -noabc9 before physical-netlist transformations",
                    "binding": binding, "cone_cells": sorted(cone_cells),
                    "mapped_cell_types": dict(sorted(Counter(cells[name]["type"] for name in cone_cells).items()))}
        resources.append(resource)
        all_cones.update(cone_cells)
        all_boundary.update(bit for bit in boundary if isinstance(bit, int))
        for name in cone_cells:
            owners[name].append(unit["id"])
    for resource in resources:
        resource["exclusive_cells"] = [name for name in resource["cone_cells"] if len(owners[name]) == 1]
        resource["shared_cells"] = [name for name in resource["cone_cells"] if len(owners[name]) > 1]
        require(resource["exclusive_cells"] or resource["merged_with"] is not None,
                "Candidate lacks any exclusive logic; partial overlap needs explicit decomposition")
    touched = set(all_boundary)
    for name in all_cones:
        for bits in cells[name]["connections"].values():
            touched.update(bit for bit in bits if isinstance(bit, int))
    lines = ["// Exact cells and connections extracted from the unmodified mapped top.",
             "// Inputs are symbolic original register/FIFO outputs, never keep-marked DUT ports.",
             "module resource_miter(input [" + str(len(all_boundary) - 1) + ":0] state_bits, output ok);"]
    for bit in sorted(touched):
        lines.append("wire n" + str(bit) + ";")
    for index, bit in enumerate(sorted(all_boundary)):
        lines.append("assign n" + str(bit) + " = state_bits[" + str(index) + "];")
    for index, name in enumerate(sorted(all_cones)):
        lines.extend(emit_primitive(name, index, cells[name]))
    for unit, binding in zip(units, bindings):
        lines.extend(emit_reference(unit, binding))
    lines += ["assign ok = " + " & ".join("u" + str(unit["id"]) + "_ok" for unit in units) + ";", "endmodule", ""]
    miter = output / "mapped_resource_miter.v"
    miter.write_text("\n".join(lines))
    script = output / "prove_resources.ys"
    script.write_text("read_verilog -sv mapped_resource_miter.v\nprep -top resource_miter\nflatten\nopt_clean\nsat -verify -prove ok 1 -show-ports\n")
    log = output / "resource_equivalence.log"
    with log.open("w") as stream:
        result = subprocess.run([str(args.yosys.resolve()), "-s", script.name], cwd=output,
                                stdout=stream, stderr=subprocess.STDOUT, check=False)
    require(result.returncode == 0 and "SUCCESS" in log.read_text(), "Mapped resource SAT equivalence failed; see " + str(log))
    for resource in resources:
        resource["functional_check"] = {"method": "exhaustive symbolic SAT", "status": "pass",
                                        "scope": "all input data bits and affine row indices; original stage state remains outside candidate"}
    files = {"netlist": args.netlist, "units": args.units, "generated_rtl": args.rtl,
             "inventory_script": Path(__file__), "primitive_equation_checker": Path(__file__).resolve().parents[1] / "check_mapped_weights.py",
             "miter": miter, "proof_script": script, "proof_log": log, **libraries}
    report = {"status": "pass", "yosys_version": version, "primitive_library": library_version,
              "source_sha256": source_hashes, "tool_versions": {"bsc": bsc_version, "yosys": version},
              "parallelism_divisor": 4,
              "physical_top_source_sha256": {name: digest(args.baseline / name) for name in ("Top.bsv", "HwMain.bsv")},
              "compiled_input_sha256": {path.name: digest(path) for path in sorted(args.rtl.parent.glob("*.v"))},
              "signal_mapping": "Simulation dut_ prefix maps to physical main_core_; BSC -remove-dollar maps $ to _; source bit slices are retained exactly.",
              "input_sha256": {key: digest(path) for key, path in files.items()},
              "method": "Distinct surviving combinational cones in the complete normal ECP5 synthesis; no source-call counts, isolated synthesis, keep attributes, or baseline edits.",
              "limits": ["An arithmetic resource is a verified nonconstant function cone, not a single FPGA primitive or an area measurement.",
                         "Affine requantization cones include existing row-valid gating. Sharing applies only to the data function; each stage retains its own row-valid guard, FIFO, and register state.",
                         "Cell overlap is reported without double counting; mapped cone cells are not claimed as exclusive from every other control cone in the design.",
                         "Normal physical-netlist ROM/reset/write-enable rewrites and placement/routing are not rerun for this inventory."],
              "candidate_count": len(resources), "retained_resource_count": sum(r["verified_present"] for r in resources),
              "mapped_multiplier_count": len(multipliers), "mapped_multiplier_cells": multipliers,
              "retained_by_kind": dict(Counter(r["kind"] for r in resources if r["verified_present"])),
              "distinct_mapped_cells": len(all_cones), "overlapping_mapped_cells": {name: ids for name, ids in owners.items() if len(ids) > 1},
              "mapped_cell_types": dict(sorted(Counter(cells[name]["type"] for name in all_cones).items())),
              "symbolic_state_bits": len(all_boundary), "resources": resources}
    require(source_hashes == {str(path.relative_to(args.baseline)): digest(path) for path in sources},
            "Baseline source changed during inventory audit")
    (output / "resource_inventory.json").write_text(json.dumps(report, indent=2) + "\n")
    print("SWAY_RESOURCE_INVENTORY_PASS " + json.dumps(report["retained_by_kind"], sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--rtl", type=Path, required=True)
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--cells-sim", type=Path, required=True)
    parser.add_argument("--yosys", type=Path, required=True)
    parser.add_argument("--bsc", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    audit(parser.parse_args())


if __name__ == "__main__":
    main()
