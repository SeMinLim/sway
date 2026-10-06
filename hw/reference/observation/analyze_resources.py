#!/usr/bin/env python3
"""Validate joint fixed-cycle sharing of synthesized baseline arithmetic units.

Combinational operations are captured in the original local register or FIFO at
their issue edge. Readback or downstream dequeue checks that retained state; it
does not reserve the stateless arithmetic circuit. Consecutive issues from one
owner form a reservation. A complete idle cycle separates reservations.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path

import analyze


EVENT_HEADER = ["cycle", "unit_id", "event", "a", "b", "result", "tag"]
ASSIGNMENT_HEADER = ["class_id", "reservation_id", "original_unit_id", "shared_unit_id",
                     "first_capture_cycle", "last_capture_cycle", "operations"]


def signed_range(value, width):
    return -(1 << (width - 1)) <= value < (1 << (width - 1))


def requant(value, source_exp, destination_exp):
    """Independent integer reference: nearest, ties to even, then INT8 clamp."""
    shift = destination_exp - source_exp
    if shift <= 0:
        rounded = value << -shift
    else:
        quotient, remainder = divmod(abs(value), 1 << shift)
        half = 1 << (shift - 1)
        if remainder > half or (remainder == half and quotient & 1):
            quotient += 1
        rounded = -quotient if value < 0 else quotient
    return max(-128, min(127, rounded))


def compatibility(unit):
    if unit["kind"] == "adder":
        analyze.require(unit["input_width"] == 24 and unit["output_width"] == 24,
                        "Unexpected affine adder format")
        return ("adder", 24, 24)
    analyze.require(unit["kind"] == "requant" and unit["output_width"] == 8,
                    "Unexpected resource kind or output format")
    return ("requant", unit["input_width"], unit["from_exp"], unit["to_exp"], 8,
            "ties_to_even", "saturate")


def affine_shapes():
    shapes = {}
    block = {"bProjection": (40, 8), "cProjection": (40, 8),
             "deltaInputProjection": (40, 2), "deltaProjection_engine": (2, 40),
             "gateProjection": (20, 40), "mainProjection": (20, 40),
             "outputProjection_engine": (40, 20)}
    for index in range(2):
        for name, (inputs, outputs) in block.items():
            shapes[f"dut_block{index}_{name}"] = (inputs, outputs, 16)
    shapes["dut_embedding_engine"] = (20, 20, 16)
    shapes["dut_headHidden_engine"] = (320, 20, 1)
    shapes["dut_headOutput_engine"] = (20, 57, 1)
    return shapes


def check_extra_metadata(units, stages):
    analyze.require([unit["id"] for unit in units] == list(range(40)),
                    "Expected 40 observed arithmetic candidates")
    analyze.require(len({unit["name"] for unit in units}) == 40, "Duplicated candidate name")
    stage_names = {stage["name"] for stage in stages}
    shapes = affine_shapes()
    found = Counter()
    for unit in units:
        stage, kind = unit["stage"], unit["kind"]
        analyze.require(stage in stage_names, "Unrecognized original stage")
        compatibility(unit)
        if stage in shapes:
            inputs, outputs, tokens = shapes[stage]
            expected = inputs * outputs * tokens if kind == "adder" else outputs * tokens
            analyze.require(unit["input_width"] == 24, "Affine arithmetic input must be INT24")
            if kind == "adder":
                analyze.require(unit["row_length"] == inputs, "Incorrect affine row length")
            found[(stage, kind)] += 1
        elif stage.endswith("_convolution"):
            analyze.require(kind == "requant" and unit["input_width"] == 18,
                            "Unexpected convolution candidate")
            expected = 16 * 40
            found[(stage, kind)] += 1
        else:
            analyze.require(stage.endswith("_scan") and kind == "requant" and unit["input_width"] == 16,
                            "Unexpected scan candidate")
            expected = 16 * 40 * 8
            found[(stage, kind)] += 1
        analyze.require(unit["expected_per_frame"] == expected, "Incorrect fixed-graph operation count")
        analyze.require(unit["receipt"] in ("next_edge_state_readback", "fifo_dequeue"),
                        "Unknown result receipt semantics")
    expected_found = Counter({(stage, kind): 1 for stage in shapes for kind in ("adder", "requant")})
    for index in range(2):
        expected_found[(f"dut_block{index}_convolution", "requant")] = 1
        expected_found[(f"dut_block{index}_scan", "requant")] = 2
    analyze.require(found == expected_found, "Missing or duplicated observed baseline operation")


def checked_inventory(document, units):
    """Require synthesis evidence for every source-level observation candidate."""
    analyze.require(document.get("status") == "pass", "Synthesis inventory is not verified")
    resources = document["resources"]
    analyze.require(sorted(item["unit_id"] for item in resources) == list(range(len(units))),
                    "Synthesis inventory does not cover every observation candidate")
    by_id = {item["unit_id"]: item for item in resources}
    retained, excluded = [], []
    for unit in units:
        item = by_id[unit["id"]]
        analyze.require(all(item[key] == unit[key] for key in ("name", "stage", "kind")),
                        "Synthesis and trace inventory identity differ")
        analyze.require(tuple(item["compatibility_key"]) == compatibility(unit),
                        "Synthesis and trace formats differ")
        analyze.require(item.get("functional_check", {}).get("status") == "pass",
                        "Mapped arithmetic function proof is missing")
        if item["constant"] or item["merged_with"] is not None:
            analyze.require(not item["verified_present"], "Excluded candidate counted as independent hardware")
            excluded.append(unit["id"])
        else:
            analyze.require(item["verified_present"] is True, "Unverified arithmetic candidate")
            analyze.require(item.get("cone_cells", 0), "Arithmetic candidate lacks nonconstant mapped cells")
            retained.append(unit["id"])
    for unit_id in excluded:
        target = by_id[unit_id]["merged_with"]
        if target is not None:
            analyze.require(target in retained and compatibility(units[target]) == compatibility(units[unit_id]),
                            "Invalid merged arithmetic representative")
    return retained, excluded


def check_provenance(inventory, validation, hashes):
    analyze.require(validation.get("status") == "pass" and validation.get("baseline_sources_unchanged") is True
                    and validation.get("extra_resources") is True,
                    "A completed unchanged-baseline joint observation is required")
    source_hashes = inventory["source_sha256"]
    analyze.require(source_hashes and source_hashes == validation["source_sha256"],
                    "Physical inventory and simulated baseline sources differ")
    for prefix in ("bsv/", "generated/", "rtl/"):
        analyze.require(any(name.startswith(prefix) for name in source_hashes),
                        "Incomplete common baseline source provenance")
    analyze.require(inventory["parallelism_divisor"] == validation["parallelism_divisor"] == 4,
                    "Physical and observed parallelism settings differ")
    analyze.require(inventory["tool_versions"]["bsc"].strip() == validation["tool_versions"]["bsc"].strip(),
                    "Physical and observed Bluespec compilers differ")
    analyze.require(inventory["input_sha256"]["units"] == hashes["extra_units"],
                    "Physical function catalog differs from observed units")
    artifacts = {"transactions": "transactions.csv", "units": "units.json", "stages": "stages.json",
                 "waits": "stage_waits.csv", "control": "control.log", "observed": "observed.log",
                 "operations": "extra_operations.csv", "extra_units": "extra_units.json"}
    for key, name in artifacts.items():
        analyze.require(validation["artifact_sha256"][name] == hashes[key],
                        "Analyzed artifact differs from the completed joint run: " + name)
    for key, name in (("inputhex", "test_input.hex"), ("expectedhex", "test_expected.hex")):
        matches = [value for path, value in validation["compiled_input_sha256"].items() if Path(path).name == name]
        analyze.require(matches == [hashes[key]], "Frozen simulation fixture differs: " + name)
    return {"status": "pass", "common_baseline_sources": len(source_hashes),
            "bsc_version_identical": True, "parallelism_divisor": 4,
            "physical_top": "mkTop", "simulation_top": "mkTbSwayKernel",
            "catalog_and_run_artifact_hashes_identical": True}


def read_operations(path, units, finish, window, frames=analyze.FRAME_COUNT):
    queues = [deque() for _ in units]
    stats = [{"operations": 0, "receipts": 0, "operations_window": 0,
              "maximum_pending_receipts": 0} for _ in units]
    feedback = [0] * len(units)
    positions = [0] * len(units)
    open_bursts = [None] * len(units)
    bursts = []
    receipt_latencies = [Counter() for _ in units]
    request_hashes = [hashlib.sha256() for _ in units]
    previous = (-1, -1, -1)
    terminal = None
    for cycle, unit_id, event, a, b, result, tag in analyze.numeric_rows(path, EVENT_HEADER):
        analyze.require(terminal is None, "Arithmetic records after terminal marker")
        if event == 2:
            analyze.require(unit_id == -1 and cycle in (finish, finish + 1) and (a, b, result, tag) == (0, 0, 0, 0),
                            "Invalid arithmetic trace terminal marker")
            terminal = cycle
            continue
        analyze.require(0 <= unit_id < len(units) and 0 <= cycle < finish and event in (0, 1),
                        "Invalid arithmetic event")
        analyze.require((cycle, unit_id, event) > previous, "Duplicated or unordered arithmetic event")
        previous = (cycle, unit_id, event)
        unit, queue, stat = units[unit_id], queues[unit_id], stats[unit_id]
        analyze.require(signed_range(result, unit["output_width"]), "Arithmetic result exceeds output width")
        if event == 0:
            analyze.require(signed_range(a, unit["input_width"]), "Arithmetic input exceeds declared width")
            if unit["kind"] == "adder":
                analyze.require(signed_range(b, 16), "Affine product exceeds signed INT16")
                analyze.require(tag in (0, 1), "Invalid affine row-final flag")
                analyze.require(a == feedback[unit_id], "Affine local sumR feedback changed")
                positions[unit_id] += 1
                analyze.require(tag == int(positions[unit_id] == unit["row_length"]),
                                "Affine row termination or product count changed")
                calculated = a + b
                analyze.require(signed_range(calculated, 24), "Proven affine INT24 bound exceeded")
                feedback[unit_id] = 0 if tag else calculated
                if tag:
                    positions[unit_id] = 0
            else:
                analyze.require(b == 0, "Requantization has a spurious second operand")
                tag_period = 40 * 8 if unit["stage"].endswith("_scan") else unit["rows"]
                analyze.require(tag == stat["operations"] % tag_period,
                                "Requantization row/channel/part order changed")
                calculated = requant(a, unit["from_exp"], unit["to_exp"])
            analyze.require(result == calculated, "Arithmetic result mismatch")
            request_hashes[unit_id].update(f"{cycle},{a},{b},{result}\n".encode())
            queue.append((cycle, result, tag))
            stat["operations"] += 1
            stat["operations_window"] += int(window[0] <= cycle < window[1])
            current = open_bursts[unit_id]
            if current is None:
                open_bursts[unit_id] = (cycle, cycle, unit_id, 1)
            elif cycle == current[1] + 1:
                open_bursts[unit_id] = (current[0], cycle, unit_id, current[3] + 1)
            else:
                bursts.append(current)
                open_bursts[unit_id] = (cycle, cycle, unit_id, 1)
        else:
            analyze.require(a == 0 and b == 0 and queue, "Receipt without a captured arithmetic result")
            issued, calculated, source_tag = queue.popleft()
            analyze.require(cycle > issued, "Receipt precedes local result capture")
            if unit["receipt"] == "next_edge_state_readback":
                analyze.require(cycle == issued + 1, "Register result was not read back at the next edge")
            analyze.require(result == calculated and tag == source_tag, "Retained result or FIFO ordering mismatch")
            stat["receipts"] += 1
            receipt_latencies[unit_id][cycle - issued] += 1
        stat["maximum_pending_receipts"] = max(stat["maximum_pending_receipts"], len(queue))
    analyze.require(terminal is not None, "Arithmetic trace has no terminal marker")
    analyze.require(all(not queue for queue in queues), "Arithmetic trace has outstanding receipts")
    for index, unit in enumerate(units):
        stat = stats[index]
        analyze.require(stat["operations"] == stat["receipts"] == unit["expected_per_frame"] * frames,
                        "Missing or extra fixed-graph arithmetic operation: " + unit["name"])
        analyze.require(positions[index] == 0, "Incomplete affine accumulation row")
        analyze.require(open_bursts[index] is not None, "Unobserved arithmetic candidate")
        bursts.append(open_bursts[index])
        stat["receipt_latency_histogram_cycles"] = dict(sorted(receipt_latencies[index].items()))
        stat["captured_requests_sha256"] = request_hashes[index].hexdigest()
    return sorted(bursts), stats


def check_merged_requests(inventory, stats):
    # An alias is omitted only when its physical representative sees exactly the
    # same captures. Distinct uses of a merged cone need a combined-owner trace.
    for item in inventory["resources"]:
        target = item["merged_with"]
        if target is not None:
            analyze.require(stats[item["unit_id"]]["captured_requests_sha256"] ==
                            stats[target]["captured_requests_sha256"],
                            "Merged aliases have distinct capture traces; combined scheduling is required")


def allocate_classes(bursts, units, retained):
    classes = sorted({compatibility(units[index]) for index in retained})
    reports, serialized = [], []
    for class_id, key in enumerate(classes):
        members = [index for index in retained if compatibility(units[index]) == key]
        member_set = set(members)
        selected = [burst for burst in bursts if burst[2] in member_set]
        count, assignments = analyze.assign_bursts(selected)
        certificate = analyze.verify_assignments(selected, assignments, count)
        analyze.require(count <= len(members), "Shared schedule exceeds original compatible resources")
        confined, by_stage = 0, []
        for stage in sorted({units[index]["stage"] for index in members}):
            stage_members = {index for index in members if units[index]["stage"] == stage}
            stage_bursts = [burst for burst in selected if burst[2] in stage_members]
            local_count, local_assignments = analyze.assign_bursts(stage_bursts)
            analyze.verify_assignments(stage_bursts, local_assignments, local_count)
            analyze.require(local_count <= len(stage_members), "Stage schedule exceeds baseline hardware")
            confined += local_count
            by_stage.append({"stage": stage, "baseline": len(stage_members), "stage_confined": local_count})
        reports.append({"class_id": class_id, "compatibility_key": list(key), "unit_ids": members,
                        "baseline": len(members), "stage_confined": confined, "shared": count,
                        "reclaimable_within_stage": len(members) - confined,
                        "additional_cross_stage_reclaim": confined - count,
                        "reclaimable": len(members) - count,
                        "by_stage": by_stage, "certificate": certificate})
        serialized.extend((class_id, *row) for row in assignments)
    return reports, serialized


def verify_class_assignments(bursts, units, classes, assignments):
    analyze.require({row[0] for row in assignments} == {item["class_id"] for item in classes},
                    "Missing or unexpected serialized compatibility class")
    checks = []
    for item in classes:
        members = set(item["unit_ids"])
        analyze.require(all(compatibility(units[index]) == tuple(item["compatibility_key"]) for index in members),
                        "Incompatible formats share an arithmetic class")
        selected = [burst for burst in bursts if burst[2] in members]
        assigned = [row[1:] for row in assignments if row[0] == item["class_id"]]
        checks.append(analyze.verify_assignments(selected, assigned, item["shared"]))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    names = ("transactions", "units", "stages", "waits", "control", "observed", "inputhex", "expectedhex",
             "operations", "extra-units", "inventory", "validation", "outputdir")
    for name in names:
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    paths = {name: value for name, value in vars(args).items() if name != "outputdir"}
    paths["analyzer"] = Path(__file__).resolve()
    paths["multiplier_analyzer"] = Path(analyze.__file__).resolve()
    hashes = {name: analyze.file_hash(path) for name, path in paths.items()}
    inputs, expected = analyze.read_hex(args.inputhex), analyze.read_hex(args.expectedhex)
    control = analyze.check_kernel(args.control, inputs, expected)
    observed = analyze.check_kernel(args.observed, inputs, expected)
    analyze.require(control["sway_lines"] == observed["sway_lines"], "Instrumentation changed baseline execution")
    stages = json.loads(args.stages.read_text())
    multipliers = json.loads(args.units.read_text())
    mapping = analyze.check_metadata(multipliers, stages)
    units = json.loads(args.extra_units.read_text())
    check_extra_metadata(units, stages)
    inventory = json.loads(args.inventory.read_text())
    provenance = check_provenance(inventory, json.loads(args.validation.read_text()), hashes)
    physical_multipliers = sorted("main_core_" + unit["name"].removeprefix("dut_") + "_dsp.multiplier"
                                  for unit in multipliers)
    analyze.require(inventory["mapped_multiplier_count"] == len(multipliers) and
                    inventory["mapped_multiplier_cells"] == physical_multipliers,
                    "Observed multipliers differ from the same physical baseline inventory")
    retained, excluded = checked_inventory(inventory, units)
    window, finish = observed["window"], observed["finish_cycle"]
    print("Checking multiplier transactions and fixed-cycle reservations", flush=True)
    mult_bursts, mult_stats, _, mult_arithmetic = analyze.read_transactions(
        args.transactions, len(multipliers), mapping, len(stages), finish, window)
    mult_arithmetic["fixed_graph_product_count_checked"] = analyze.check_product_counts(mult_stats)
    mult_count, mult_assignments = analyze.assign_bursts(mult_bursts)
    mult_confined, _ = analyze.stage_confined_schedule(mult_bursts, stages, mapping)
    analyze.read_waits(args.waits, len(stages), finish, window)
    print("Checking additional arithmetic captures, local feedback, and receipts", flush=True)
    bursts, stats = read_operations(args.operations, units, finish, window)
    check_merged_requests(inventory, stats)
    print("Allocating exact-format arithmetic pools and checking serialized certificates", flush=True)
    classes, assignments = allocate_classes(bursts, units, retained)
    intervals = observed["intervals"][14:42]
    analyze.require(intervals == [18592] * 28, "Baseline steady-state output interval changed")
    analyze.require(hashes == {name: analyze.file_hash(path) for name, path in paths.items()},
                    "Analysis input changed while reading")
    args.outputdir.mkdir(parents=True, exist_ok=True)
    analyze.write_csv(args.outputdir / "resource_assignments.csv", ASSIGNMENT_HEADER, assignments)
    extra_checks = verify_class_assignments(bursts, units, classes, list(analyze.numeric_rows(
        args.outputdir / "resource_assignments.csv", ASSIGNMENT_HEADER)))
    mult_header = ["reservation_id", "original_unit_id", "shared_unit_id", "first_put_cycle", "last_get_cycle", "products"]
    analyze.write_csv(args.outputdir / "multiplier_assignments.csv", mult_header, mult_assignments)
    mult_check = analyze.verify_assignments(mult_bursts, list(analyze.numeric_rows(
        args.outputdir / "multiplier_assignments.csv", mult_header)), mult_count)
    totals = [{"kind": "multiplier", "baseline": len(multipliers), "stage_confined": mult_confined,
               "shared": mult_count, "reclaimable": len(multipliers) - mult_count}]
    for kind in ("adder", "requant"):
        selected = [item for item in classes if item["compatibility_key"][0] == kind]
        total = {"kind": kind}
        for key in ("baseline", "stage_confined", "shared", "reclaimable"):
            total[key] = sum(item[key] for item in selected)
        totals.append(total)
    unit_report = [{"id": unit["id"], "name": unit["name"], "stage": unit["stage"],
                    "kind": unit["kind"], "counted_in_inventory": unit["id"] in retained,
                    **stat} for unit, stat in zip(units, stats)]
    (args.outputdir / "resource_units.json").write_text(json.dumps(unit_report, indent=2) + "\n")
    report = {
        "status": "pass", "kind": "same-execution measurement and joint fixed-cycle resource scheduling analysis",
        "frames_checked": analyze.FRAME_COUNT, "scalar_outputs_checked": observed["scalar_outputs"],
        "control_observer_markers_identical": True, "full_trace_finish_cycle": finish,
        "measurement_window": {"start_inclusive_cycle": window[0], "end_exclusive_cycle": window[1],
                               "output_intervals": len(intervals)},
        "kernel_output_interval_cycles": intervals[0], "scheduled_output_interval_cycles": intervals[0],
        "resources": totals, "compatibility_classes": classes,
        "observed_candidates": len(units), "synthesis_verified_independent_candidates": len(retained),
        "excluded_constant_or_merged_candidates": excluded,
        "physical_simulation_provenance": provenance,
        "arithmetic": {"multipliers": mult_arithmetic,
                       "additional_operations_checked": sum(stat["operations"] for stat in stats),
                       "additional_receipts_checked": sum(stat["receipts"] for stat in stats),
                       "mismatches": 0, "affine_local_feedback_and_row_boundaries_checked": True},
        "joint_certificate": {"status": "pass", "multiplier_certificate": mult_check,
                              "arithmetic_certificates": extra_checks, "events_retimed": 0,
                              "all_pools_share_same_baseline_execution": True},
        "transition_cost_cycles": analyze.SWITCH_CYCLES,
        "combinational_reservations": "Consecutive same-owner operations are one burst; local result capture occurs at each original issue edge; one full idle cycle separates bursts",
        "state_and_delivery": "sumR, output registers, metadata and FIFOs remain local; next-edge readback or ordered FIFO dequeue verifies every captured additional result; downstream storage retention does not occupy a stateless arithmetic unit",
        "memory_and_dependencies": "Every arithmetic issue/capture and downstream receipt retains its baseline cycle; stage state, dependency order and SRAM access schedule are unchanged; no added memory ports or retiming",
        "scope": "Feasible jointly for this complete 56-frame execution; no implemented shared RTL or optimum resource claim",
        "physical_limits": "Counts describe synthesis-verified independent arithmetic candidates, not source calls; interconnect/control/storage cost, net FPGA area savings and shared routed frequency are not measured",
        "source_sha256": hashes,
        "output_sha256": {name: analyze.file_hash(args.outputdir / name) for name in (
            "resource_assignments.csv", "multiplier_assignments.csv", "resource_units.json")}}
    (args.outputdir / "resource_analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "resources": totals,
                      "kernel_output_interval_cycles": intervals[0]}))


if __name__ == "__main__":
    main()
