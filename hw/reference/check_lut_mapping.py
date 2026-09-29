#!/usr/bin/env python3
"""Verify the default 17 affine LUT-ROM cones in the mapped ECP5 design."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from check_address_replicas import inspect_replicas


LUT_CELLS = {"LUT4", "PFUMX", "L6MUX21"}
STATE_BLOCK_RAM = {"main_core_block0_scan_stateR_0.arr.0.0", "main_core_block1_scan_stateR_0.arr.0.0"}
ENGINES = ["main_core_embedding_engine", "main_core_headHidden_engine", "main_core_headOutput_engine"]
for block in range(2):
    for name in ("mainProjection", "gateProjection", "deltaInputProjection", "bProjection", "cProjection",
                 "deltaProjection_engine", "outputProjection_engine"):
        ENGINES.append(f"main_core_block{block}_{name}")
ENGINES.sort()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_state_block_ram(netlist):
    instances = sorted(cell_name for module in netlist["modules"].values()
                       for cell_name, cell in module.get("cells", {}).items()
                       if cell["type"] == "DP16KD")
    return {"expected_total": len(STATE_BLOCK_RAM), "actual_total": len(instances),
            "expected_affine": 0, "actual_unapproved": len([name for name in instances if name not in STATE_BLOCK_RAM]),
            "allowed_state_instances": sorted(STATE_BLOCK_RAM), "instances": instances,
            "pass": instances == sorted(STATE_BLOCK_RAM)}


def inspect_mapping(netlist, rtl, report):
    modules = netlist["modules"]
    cells = modules["mkTop"]["cells"]
    nets = modules["mkTop"]["netnames"]
    report["dp16kd"] = inspect_state_block_ram(netlist)
    if not report["dp16kd"]["pass"]:
        raise RuntimeError("Mapped DP16KD instances differ from the two allowed dynamic scan-state memories")

    # The independent RTL check fixes the tuple boundary before selecting mapped
    # bits. For one affine lane: {input[7:0], weight[7:0], last} is 17 bits.
    rtl = re.sub(r"//[^\n]*|/\*.*?\*/", "", rtl, flags=re.S)
    assignments = dict(re.findall(r"\bassign\s+(\w+)\s*=\s*(.*?);", rtl, re.S))
    actual_engines = sorted(name[:-len("_addressR_EN")] for name in assignments
                            if name.endswith("_addressR_EN"))
    if actual_engines != ENGINES:
        raise RuntimeError("Generated RTL does not contain exactly the 17 default affine engines")

    drivers = {}
    for cell_name, cell in cells.items():
        for port, direction in cell["port_directions"].items():
            if direction != "output":
                continue
            for bit in cell["connections"][port]:
                if type(bit) is int:
                    drivers.setdefault(bit, []).append((cell_name, port))

    replica_aliases, report["address_replicas"] = inspect_replicas(modules["mkTop"], rtl)
    page_copies = [name for name in cells if name.startswith("main_core_headHidden_engine_pageDecodeReplica_")]
    if page_copies:
        # The frozen address auditor predates decoder copying; its constant zero
        # metric describes only its original FF transform, not this netlist.
        report["address_replicas"].pop("coefficient_cells_duplicated", None)
        report["address_replicas"]["scope"] = "Same-cycle address FF identity, startup and coefficient observation boundary"
        report["page_decoder_copies"] = {"actual_cells": len(page_copies), "expected_cells": 513,
                                          "independent_transition_proof_required": "page_decoder_audit.json"}
        if len(page_copies) != 513:
            raise RuntimeError("Unexpected page-decoder copy count")
    report["engines"] = []
    all_cone_cells = set()
    for engine in ENGINES:
        entry = {"engine": engine, "status": "fail", "operand_width": 17,
                 "weight_bit_range": [1, 8], "address_width": 13}
        report["engines"].append(entry)
        operand_name = engine + "_operandQ_D_IN"
        address_name = engine + "_addressR"
        bank_name = engine + "_bankQ_D_OUT"
        packing = (r"\{\s*" + re.escape(bank_name) + r"\[8:1\]\s*,\s*(.+)\s*,\s*"
                   + re.escape(bank_name) + r"\[0\]\s*\}")
        if not re.fullmatch(packing, assignments[operand_name].strip(), re.S):
            raise RuntimeError("Unexpected input/weight/metadata tuple packing: " + engine)
        operand = nets[operand_name]["bits"]
        address = nets[address_name]["bits"]
        if len(operand) != 17 or len(address) != 13 or len(nets[bank_name]["bits"]) != 9:
            raise RuntimeError("Unexpected default single-lane operand/address width: " + engine)
        address_bits = {bit for bit in address if type(bit) is int}
        if not address_bits:
            raise RuntimeError("Affine address register is missing: " + engine)
        for bit in address_bits:
            sources = drivers.get(bit, [])
            if len(sources) != 1 or sources[0][1] != "Q" or cells[sources[0][0]]["type"] != "TRELLIS_FF":
                raise RuntimeError("Affine address bit is not driven by its register: " + engine)

        visited = set()
        pending = set()
        cone_cells = set()
        address_leaves = set()
        constant_leaves = set()

        def visit(bit):
            if type(bit) is str:
                if bit not in {"0", "1"}:
                    raise RuntimeError(f"Unknown constant in {engine} weight cone: {bit}")
                constant_leaves.add(bit)
                return
            if replica_aliases.get(bit, bit) in address_bits:
                address_leaves.add(replica_aliases.get(bit, bit))
                return
            if bit in pending:
                raise RuntimeError("Combinational cycle in affine weight cone: " + engine)
            if bit in visited:
                return
            sources = drivers.get(bit, [])
            if len(sources) != 1:
                raise RuntimeError(f"Expected one driver for {engine} weight bit {bit}; found {sources}")
            cell_name, _ = sources[0]
            cell = cells[cell_name]
            if cell["type"] not in LUT_CELLS:
                raise RuntimeError(f"Non-LUT intermediate in {engine} weight cone: {cell_name} ({cell['type']})")
            cone_cells.add(cell_name)
            pending.add(bit)
            for port, direction in cell["port_directions"].items():
                if direction == "input":
                    for source_bit in cell["connections"][port]:
                        visit(source_bit)
                elif direction != "output":
                    raise RuntimeError("Unexpected bidirectional LUT port: " + cell_name)
            pending.remove(bit)
            visited.add(bit)

        for bit in operand[1:9]:
            visit(bit)
        counts = Counter(cells[name]["type"] for name in cone_cells)
        if not counts["LUT4"] or not address_leaves:
            raise RuntimeError("No address-dependent LUT4 coefficient cone: " + engine)
        entry.update({"status": "pass", "lut_cells": dict(sorted(counts.items())),
                      "address_bits_used": sorted(i for i, bit in enumerate(address) if bit in address_leaves),
                      "constant_leaves": sorted(constant_leaves),
                      "intermediate_registers": 0, "intermediate_memories": 0,
                      "other_engine_inputs": 0})
        all_cone_cells.update(cone_cells)
    report["engine_count"] = len(report["engines"])
    report["unique_weight_cone_cells"] = dict(sorted(Counter(cells[name]["type"] for name in all_cone_cells).items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True, help="mapped mkTop.json")
    parser.add_argument("--rtl", type=Path, required=True, help="corresponding BSC-generated mkTop.v")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"status": "fail", "checker_sha256": sha256(Path(__file__)), "top": "mkTop",
              "expected_engine_count": 17, "expected_lanes_per_engine": 1,
              "allowed_weight_cone_cells": sorted(LUT_CELLS),
              "scope": "Coefficient logic from each engine's own address register to operand FIFO input; dynamic pipeline FIFO storage is outside this cone"}
    try:
        report["netlist_sha256"] = sha256(args.netlist)
        report["rtl_sha256"] = sha256(args.rtl)
        inspect_mapping(json.loads(args.netlist.read_text()), args.rtl.read_text(), report)
        if sha256(args.netlist) != report["netlist_sha256"] or sha256(args.rtl) != report["rtl_sha256"]:
            raise RuntimeError("Mapping audit inputs changed during inspection")
        report["status"] = "pass"
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        report["error"] = str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("SWAY_LUT_MAPPING_" + report["status"].upper() + " report=" + str(args.output))
    if report["status"] != "pass":
        print(report["error"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
