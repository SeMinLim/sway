#!/usr/bin/env python3
"""Exhaustively compare mapped affine coefficient logic with frozen INT8 bytes."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from check_address_replicas import inspect_replicas


ADDRESS_COUNT = 8192
ADDRESS_WIDTH = 13
PATTERN_MASK = (1 << ADDRESS_COUNT) - 1
CELL_LIBRARIES = {
    "23c1d1d35b5bd9ba5a07e17cf7d5c07fe7b6840e1deb6c2f746c3f430b4cec54": (
        "Yosys 0.33", {}),
    "c1ddb9de055c6c2a2225215ffd6ded48c884d12d5c7321eaf1a2f417d4f23ce0": (
        "Yosys 0.67+24 / OSS CAD Suite 2026-07-11", {
            "common_sim.vh": "3d55fcb1b659f9a99e081d0e8cd3e0a74fb8d659644103db9ad10b04a64ce75d",
            "ccu2c_sim.vh": "178b1310b2e70768114e1c795118529055799df1ad650a09d0613b3856a81ccc",
        }),
}
CELL_PORTS = {
    "LUT4": ({"A", "B", "C", "D"}, {"Z"}),
    "PFUMX": ({"ALUT", "BLUT", "C0"}, {"Z"}),
    "L6MUX21": ({"D0", "D1", "SD"}, {"Z"}),
    "CCU2C": ({"CIN", "A0", "B0", "C0", "D0", "A1", "B1", "C1", "D1"},
              {"S0", "S1", "COUT"}),
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reviewed_library(path):
    """Pin every source defining the evaluated combinational primitives."""
    library = CELL_LIBRARIES.get(sha256(path))
    if library is None:
        raise RuntimeError("Cell library differs from the reviewed Yosys primitive equations")
    version, includes = library
    inputs = {"cell_library": path}
    for name, digest in includes.items():
        source = path.parent / name
        if sha256(source) != digest:
            raise RuntimeError("Cell library include differs from reviewed equations: " + name)
        inputs["cell_library_" + name] = source
    return version, inputs


def engine_slices():
    # Independent fixed-model mapping from SwayBaseline/SwayBlock dimensions.
    # Each tuple is (engine suffix, tensor, layer, row offset, rows, columns).
    engines = [
        ("embedding_engine", "embedding.weight", 0, 0, 20, 20),
        ("headHidden_engine", "headHidden.weight", 9, 0, 20, 320),
        ("headOutput_engine", "headOutput.weight", 10, 0, 57, 20),
    ]
    for block in range(2):
        prefix = f"blocks.{block}."
        first = 1 + 4 * block
        for name, tensor, layer, offset, rows, columns in (
            ("mainProjection", "inWeight", first, 0, 40, 20),
            ("gateProjection", "inWeight", first, 40, 40, 20),
            ("deltaInputProjection", "xWeight", first + 1, 0, 2, 40),
            ("bProjection", "xWeight", first + 1, 2, 8, 40),
            ("cProjection", "xWeight", first + 1, 10, 8, 40),
            ("deltaProjection_engine", "dtWeight", first + 2, 0, 40, 2),
            ("outputProjection_engine", "outWeight", first + 3, 0, 20, 40),
        ):
            engines.append((f"block{block}_{name}", prefix + tensor, layer, offset, rows, columns))
    return sorted(("main_core_" + name, tensor, layer, offset, rows, columns)
                  for name, tensor, layer, offset, rows, columns in engines)


def truth_value(init, inputs):
    # Yosys LUT4 uses INIT[{D,C,B,A}]; each integer bit is one address pattern.
    values = [PATTERN_MASK if (init >> i) & 1 else 0 for i in range(1 << len(inputs))]
    for select in inputs:
        values = [(low & (PATTERN_MASK ^ select)) | (high & select)
                  for low, high in zip(values[0::2], values[1::2])]
    return values[0]


def init_parameter(parameters, name):
    # The pinned official LUT4/CCU2C INIT defaults are 16'h0000.
    value = parameters.get(name, "0" * 16)
    if not isinstance(value, str) or not re.fullmatch(r"[01]{1,16}", value):
        raise RuntimeError("Undefined or unsupported 16-bit INIT: " + name + "=" + repr(value))
    return int(value, 2)


def cell_values(cell_type, parameters, inputs):
    if cell_type == "LUT4":
        if set(parameters) - {"INIT"}:
            raise RuntimeError("Unexpected LUT4 parameters")
        return {"Z": truth_value(init_parameter(parameters, "INIT"), [inputs[p] for p in "ABCD"])}
    if cell_type in {"PFUMX", "L6MUX21"}:
        if parameters:
            raise RuntimeError("Unexpected mux parameters")
        if cell_type == "PFUMX":
            # Official PFUMX selects ALUT when C0=1, BLUT when C0=0.
            select, high, low = inputs["C0"], inputs["ALUT"], inputs["BLUT"]
        else:
            select, high, low = inputs["SD"], inputs["D1"], inputs["D0"]
        return {"Z": (select & high) | ((PATTERN_MASK ^ select) & low)}
    if cell_type != "CCU2C" or set(parameters) - {"INIT0", "INIT1", "INJECT1_0", "INJECT1_1"}:
        raise RuntimeError("Unsupported combinational cell or parameters: " + cell_type)
    # The reviewed 0.33 and 0.67+24 definitions are identical, including the YES
    # defaults. Carry evaluation is logical, not an assumed increment.
    result = {}
    carry = inputs["CIN"]
    for half in range(2):
        init = init_parameter(parameters, f"INIT{half}")
        inject = parameters.get(f"INJECT1_{half}", "YES")
        if inject not in {"YES", "NO"}:
            raise RuntimeError("Unsupported CCU2C carry-injection parameter: " + repr(inject))
        lut4 = truth_value(init, [inputs[p + str(half)] for p in "ABCD"])
        lut2 = truth_value(init & 15, [inputs[p + str(half)] for p in "AB"])
        gated_carry = 0 if inject == "YES" else carry
        gated_lut2 = 0 if inject == "YES" else lut2
        result[f"S{half}"] = lut4 ^ gated_carry
        carry = ((PATTERN_MASK ^ lut4) & gated_lut2) | (lut4 & carry)
    result["COUT"] = carry
    return result


def address_patterns():
    return [sum(1 << address for address in range(ADDRESS_COUNT) if (address >> bit) & 1)
            for bit in range(ADDRESS_WIDTH)]


def evaluate_engine(cells, nets, drivers, engine, patterns, replica_aliases):
    address = nets[engine + "_addressR"]["bits"]
    operand = nets[engine + "_operandQ_D_IN"]["bits"]
    if len(address) != ADDRESS_WIDTH or len(operand) != 17:
        raise RuntimeError("Unexpected single-lane address/operand width: " + engine)
    if any(type(bit) is not int for bit in address) or len(set(address)) != ADDRESS_WIDTH:
        raise RuntimeError("All 13 independent address FF bits are required: " + engine)
    for bit in address:
        sources = drivers.get(bit, [])
        if len(sources) != 1 or sources[0][1] != "Q" or cells[sources[0][0]]["type"] != "TRELLIS_FF":
            raise RuntimeError("Address input is not its register Q: " + engine)
    address_values = dict(zip(address, patterns))
    address_values.update({replica: address_values[original] for replica, original in replica_aliases.items()
                           if original in address_values})
    evaluated_cells = {}
    pending = set()
    counts = Counter()

    def evaluate(bit):
        if type(bit) is str:
            if bit not in {"0", "1"}:
                raise RuntimeError("Undefined coefficient input constant: " + repr(bit))
            return PATTERN_MASK if bit == "1" else 0
        if bit in address_values:
            return address_values[bit]
        sources = drivers.get(bit, [])
        if len(sources) != 1:
            raise RuntimeError("Expected exactly one coefficient driver: " + str(bit))
        name, output = sources[0]
        if name in pending:
            raise RuntimeError("Combinational cycle in coefficient cone: " + name)
        if name not in evaluated_cells:
            cell = cells[name]
            cell_type = cell["type"]
            if cell_type not in CELL_PORTS:
                raise RuntimeError("Non-combinational coefficient dependency: " + name + " (" + cell_type + ")")
            input_ports, output_ports = CELL_PORTS[cell_type]
            connections, directions = cell["connections"], cell["port_directions"]
            if set(connections) != input_ports | output_ports or set(directions) != set(connections):
                raise RuntimeError("Unexpected coefficient primitive ports: " + name)
            for port, bits in connections.items():
                expected = "input" if port in input_ports else "output"
                if len(bits) != 1 or directions[port] != expected:
                    raise RuntimeError("Unexpected coefficient primitive port width/direction: " + name + "." + port)
            pending.add(name)
            inputs = {port: evaluate(connections[port][0]) for port in input_ports}
            evaluated_cells[name] = cell_values(cell_type, cell.get("parameters", {}), inputs)
            pending.remove(name)
            counts[cell_type] += 1
        return evaluated_cells[name][output]

    planes = [evaluate(bit) for bit in operand[1:9]]
    return planes, dict(sorted(counts.items()))


def check(args, report):
    version, library_inputs = reviewed_library(args.cells_sim)
    inputs = {"netlist": args.netlist, "generated_rtl": args.rtl, "cell_library": args.cells_sim,
              "model_manifest": args.export / "manifest.json", "checker": Path(__file__).resolve(),
              "replica_auditor": Path(__file__).with_name("check_address_replicas.py")}
    inputs.update(library_inputs)
    report["input_sha256"] = {name: sha256(path) for name, path in inputs.items()}
    report["cell_equations"] = {"library_sha256_verified": True,
                                "reviewed_library": version,
                                "models": ["LUT4 INIT[{D,C,B,A}]", "PFUMX C0?ALUT:BLUT", "L6MUX21 SD?D1:D0",
                                           "CCU2C official two-half LUT4/LUT2 sum/carry equations"],
                                "default_parameters": {"INIT": 0, "INIT0": 0, "INIT1": 0,
                                                       "INJECT1_0": "YES", "INJECT1_1": "YES"},
                                "undefined_constants_or_init": "rejected"}
    manifest = json.loads(inputs["model_manifest"].read_text())
    tensors = {item["parameterName"]: item for item in manifest["tensors"]}
    if len(tensors) != len(manifest["tensors"]):
        raise RuntimeError("Duplicate model tensor names")
    expected_config = {"D": 20, "E": 2, "P": 2, "M": 2, "N": 8, "L": 16,
                       "input_channels": 5, "outputs": 57, "dt_rank": 2, "head_hidden": 20}
    if any(manifest["modelConfig"].get(key) != value for key, value in expected_config.items()):
        raise RuntimeError("Model dimensions differ from the independently enumerated engine slices")
    slices = engine_slices()
    rtl = re.sub(r"//[^\n]*|/\*.*?\*/", "", args.rtl.read_text(), flags=re.S)
    assignments = dict(re.findall(r"\bassign\s+(\w+)\s*=\s*(.*?);", rtl, re.S))
    actual = sorted(name[:-len("_addressR_EN")] for name in assignments if name.endswith("_addressR_EN"))
    if actual != [item[0] for item in slices]:
        raise RuntimeError("Generated RTL does not have exactly the 17 expected affine engines")
    netlist = json.loads(args.netlist.read_text())
    module = netlist["modules"]["mkTop"]
    cells, nets = module["cells"], module["netnames"]
    drivers = {}
    for name, cell in cells.items():
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    if type(bit) is int:
                        drivers.setdefault(bit, []).append((name, port))
    replica_aliases, report["address_replicas"] = inspect_replicas(module, args.rtl.read_text())
    page_copies = [name for name in cells if name.startswith("main_core_headHidden_engine_pageDecodeReplica_")]
    private_rom = [name for name in cells if name.startswith("main_core_headHidden_engine_privateRom_")]
    if page_copies or private_rom:
        # The frozen address auditor's constant zero duplication metric applies
        # only to its original FF transform, not to this additional stage.
        report["address_replicas"].pop("coefficient_cells_duplicated", None)
        report["address_replicas"]["scope"] = "Same-cycle address FF identity, startup and coefficient observation boundary"
        report["page_decoder_copies"] = {"actual_cells": len(page_copies),
                                          "independent_transition_proof_required": "page_decoder_audit.json"}
        if private_rom:
            report["page_decoder_copies"]["audit_boundary"] = "mkTop.before_private_rom.json"
            report["private_rom"] = {"actual_cells": len(private_rom),
                                      "independent_transition_proof_required": "private_rom_audit.json"}
    wre_copies = [name for name in cells if name.startswith("sway_wre_copy_block")]
    if wre_copies:
        if private_rom:
            report["private_rom"]["audit_boundary"] = "mkTop.before_wre.json"
        report["wre_copies"] = {"actual_cells": len(wre_copies),
                                 "independent_transition_proof_required": "wre_audit.json"}
    patterns = address_patterns()
    binary_hashes = {}
    report["engines"] = []
    for engine, parameter, layer, offset, rows, columns in slices:
        bank = re.escape(engine + "_bankQ_D_OUT")
        packing = r"\{\s*" + bank + r"\[8:1\]\s*,\s*(.+)\s*,\s*" + bank + r"\[0\]\s*\}"
        if not re.fullmatch(packing, assignments[engine + "_operandQ_D_IN"].strip(), re.S):
            raise RuntimeError("Unexpected input/weight/metadata tuple packing: " + engine)
        tensor = tensors[parameter]
        path = args.export / tensor["binary"]
        if path.parent.resolve() != args.export.resolve():
            raise RuntimeError("Weight binary must reside in the export directory")
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if (digest != tensor["sha256"] or len(payload) != tensor["bytes"]
                or tensor["dtype"] != "|i1" or tensor["logicalBits"] != 8 or tensor["storageBits"] != 8
                or tensor["layout"] != "C row-major, last axis contiguous" or tensor.get("zeroPoint") != 0
                or len(tensor["shape"]) != 2 or tensor["shape"][1] != columns
                or offset + rows > tensor["shape"][0] or len(payload) != tensor["shape"][0] * columns):
            raise RuntimeError("Frozen signed INT8 tensor contract failed: " + parameter)
        binary_hashes[tensor["binary"]] = digest
        weights = payload[offset * columns:(offset + rows) * columns]
        expected = weights + bytes(ADDRESS_COUNT - len(weights))
        planes, counts = evaluate_engine(cells, nets, drivers, engine, patterns, replica_aliases)
        calculated = bytes(sum(((plane >> address) & 1) << bit for bit, plane in enumerate(planes))
                           for address in range(ADDRESS_COUNT))
        mismatches = [address for address in range(ADDRESS_COUNT) if calculated[address] != expected[address]]
        entry = {"engine": engine, "tensor": parameter, "binary": tensor["binary"], "binary_sha256": digest,
                 "layer": layer, "row_offset": offset, "output_rows": rows, "input_columns": columns,
                 "addresses_checked": ADDRESS_COUNT, "valid_coefficient_addresses": len(weights),
                 "out_of_range_zero_addresses": ADDRESS_COUNT - len(weights), "padded_lane_addresses": 0,
                 "mapped_cells_evaluated": counts, "intermediate_registers": 0, "intermediate_memories": 0,
                 "other_engine_or_control_inputs": 0, "mismatched_addresses": len(mismatches),
                 "mapped_output_sha256": hashlib.sha256(calculated).hexdigest(),
                 "expected_output_sha256": hashlib.sha256(expected).hexdigest(),
                 "status": "pass" if not mismatches else "fail"}
        if mismatches:
            entry["first_mismatches"] = [{"address": address,
                                          "expected_int8": expected[address] if expected[address] < 128 else expected[address] - 256,
                                          "mapped_int8": calculated[address] if calculated[address] < 128 else calculated[address] - 256}
                                         for address in mismatches[:16]]
        report["engines"].append(entry)
    report["binary_sha256"] = dict(sorted(binary_hashes.items()))
    report["engine_count"] = len(report["engines"])
    report["coefficient_addresses_checked"] = sum(item["addresses_checked"] for item in report["engines"])
    report["valid_coefficient_addresses"] = sum(item["valid_coefficient_addresses"] for item in report["engines"])
    report["out_of_range_zero_addresses"] = sum(item["out_of_range_zero_addresses"] for item in report["engines"])
    report["mismatched_addresses"] = sum(item["mismatched_addresses"] for item in report["engines"])
    if any(sha256(path) != report["input_sha256"][name] for name, path in inputs.items()):
        raise RuntimeError("Mapped-coefficient inputs changed during inspection")
    if any(sha256(args.export / name) != digest for name, digest in binary_hashes.items()):
        raise RuntimeError("Frozen coefficient bytes changed during inspection")
    if report["engine_count"] != 17 or report["mismatched_addresses"]:
        raise RuntimeError("Mapped coefficient values differ from the direct frozen export bytes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--rtl", type=Path, required=True)
    parser.add_argument("--export", type=Path, default=Path(__file__).resolve().parents[1] / "model/export")
    parser.add_argument("--cells-sim", type=Path, default=Path("/usr/share/yosys/ecp5/cells_sim.v"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"status": "fail", "top": "mkTop", "expected_engines": 17, "lanes_per_engine": 1,
              "address_width": ADDRESS_WIDTH, "addresses_per_engine": ADDRESS_COUNT,
              "method": "8192-pattern bit-parallel combinational primitive evaluation, stopped only at each engine's own address FF Q bits or independently verified same-cycle address replicas; direct frozen row-major signed INT8 binary expectations, no generated truth tables imported",
              "scope": "All mapped coefficient outputs, including invalid-address zero padding; no timing or full-system functional PASS claim"}
    try:
        check(args, report)
        report["status"] = "pass"
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, RecursionError) as error:
        report["error"] = str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("SWAY_MAPPED_WEIGHTS_" + report["status"].upper() + " report=" + str(args.output))
    if report["status"] != "pass":
        print(report["error"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
