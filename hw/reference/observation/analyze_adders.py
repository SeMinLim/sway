#!/usr/bin/env python3
"""Measure affine accumulator operations and certify fixed-cycle adder sharing.

No sumR, FIFO, memory access, operation, or result-capture cycle is moved.
Adjacent operation cycles of one original adder form an indivisible reservation.
One full idle cycle follows every reservation before another reservation can use
the same physical adder. This is the prior multiplier analysis' conservative gap.
"""

import argparse
from collections import Counter, deque
import csv
import hashlib
import heapq
import importlib.util
import json
from pathlib import Path


HEADER = ["cycle", "unit_id", "last", "a", "b", "result"]
ASSIGNMENT_HEADER = ["reservation_id", "original_unit_id", "shared_unit_id",
                     "operation_first_cycle", "capture_last_cycle", "operations"]
FRAMES = 56
SWITCH_CYCLES = 1


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path, header):
    with Path(path).open() as stream:
        reader = csv.reader(stream)
        require(next(reader) == header, "CSV header mismatch: " + str(path))
        for line, row in enumerate(reader, 2):
            require(len(row) == len(header), "CSV row length mismatch at " + str(line))
            yield tuple(map(int, row))


def write_csv(path, header, rows):
    with Path(path).open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def dimensions(name):
    # Frozen SwayTypes/SwayBlock/SwayBaseline graph, LinearLanes=1.
    suffixes = {"bProjection": (40, 8, 16), "cProjection": (40, 8, 16),
                "deltaInputProjection": (40, 2, 16),
                "deltaProjection_engine": (2, 40, 16),
                "gateProjection": (20, 40, 16), "mainProjection": (20, 40, 16),
                "outputProjection_engine": (40, 20, 16)}
    for block in range(2):
        prefix = "dut_block" + str(block) + "_"
        if name.startswith(prefix):
            require(name[len(prefix):] in suffixes, "Unknown affine engine")
            return suffixes[name[len(prefix):]]
    outer = {"dut_embedding_engine": (20, 20, 16),
             "dut_headHidden_engine": (320, 20, 1),
             "dut_headOutput_engine": (20, 57, 1)}
    require(name in outer, "Unknown affine engine " + name)
    return outer[name]


def analyze_events(path, units, finish, window, multiplier_path, multiplier_units):
    count = len(units)
    states = [0] * count
    columns = [0] * count
    last_cycles = [-2] * count
    starts = [None] * count
    previous = (-1, -1)
    stats = [{"operations": 0, "row_results": 0, "operations_window": 0,
              "row_results_window": 0} for _ in units]
    mult_id = {unit["name"]: unit["id"] for unit in multiplier_units}
    mapped_mult = {mult_id[unit["name"] + "_multipliers_0"]: unit["id"] for unit in units}
    products = [deque() for _ in units]
    multiplier_rows = read_csv(multiplier_path, ["cycle", "unit_id", "put", "get", "a", "b", "result"])
    next_multiplier = next(multiplier_rows, None)
    delay_histogram = Counter()
    maximum_product_fifo = 0
    bursts = []
    total = 0
    last_pop_cycle = [-1] * count
    for cycle, unit, last, a, b, result in read_csv(path, HEADER):
        require(0 <= unit < count and 0 <= cycle < finish, "Invalid adder unit/cycle")
        require((cycle, unit) > previous, "Adder trace not strictly cycle/unit ordered")
        previous = (cycle, unit)
        require(last in (0, 1), "Invalid final-product marker")
        require(-(1 << 23) <= a < (1 << 23), "sumR input exceeds signed24")
        require(-(1 << 15) <= b < (1 << 15), "Product exceeds signed16")
        require(-(1 << 23) <= result < (1 << 23), "Adder result exceeds signed24")
        require(a == states[unit], "Original per-stage accumulator state/order mismatch")
        expected_result = ((a + b + (1 << 23)) % (1 << 24)) - (1 << 23)
        require(result == expected_result, "INT24 addition mismatch")
        require(result == a + b, "Unexpected overflow in baseline bounded affine sum")
        columns[unit] += 1
        n, m, tokens = dimensions(units[unit]["name"])
        require(last == int(columns[unit] == n), "Row boundary or product count mismatch")
        if last:
            columns[unit] = 0
            states[unit] = 0
        else:
            states[unit] = result
        # Multiplier.get enqueues productQ on the preceding or an earlier edge.
        # Only earlier cycles may feed this registered, non-bypassing FIFO.
        while next_multiplier is not None and next_multiplier[0] < cycle:
            mc, mu, put, get, ma, mb, product = next_multiplier
            if get and mu in mapped_mult:
                au = mapped_mult[mu]
                value16 = ((product + (1 << 15)) % (1 << 16)) - (1 << 15)
                products[au].append((mc, value16))
                maximum_product_fifo = max(maximum_product_fifo, len(products[au]))
                require(len(products[au]) <= 2, "Original two-entry productQ overflows")
            next_multiplier = next(multiplier_rows, None)
        require(products[unit], "Adder consumes without an earlier multiplier result")
        produced_cycle, produced = products[unit].popleft()
        require(produced == b, "Adder operand or multiplier-to-productQ order mismatch")
        require(produced_cycle < cycle, "Adder consumed before registered productQ delivery")
        delay_histogram[cycle - produced_cycle] += 1
        last_pop_cycle[unit] = cycle
        if starts[unit] is not None and cycle != last_cycles[unit] + 1:
            bursts.append((starts[unit], last_cycles[unit], unit,
                           last_cycles[unit] - starts[unit] + 1))
            starts[unit] = None
        if starts[unit] is None:
            starts[unit] = cycle
        last_cycles[unit] = cycle
        stats[unit]["operations"] += 1
        stats[unit]["row_results"] += last
        if window[0] <= cycle < window[1]:
            stats[unit]["operations_window"] += 1
            stats[unit]["row_results_window"] += last
        total += 1
    while next_multiplier is not None:
        mc, mu, put, get, ma, mb, product = next_multiplier
        require(not (get and mu in mapped_mult), "Affine multiplier result not consumed by adder")
        next_multiplier = next(multiplier_rows, None)
    require(all(not q for q in products), "Adder trace leaves productQ data pending")
    require(all(value == 0 for value in columns), "Adder trace ends mid-row")
    require(all(value == 0 for value in states), "Adder trace leaves nonzero row state")
    for unit in units:
        index = unit["id"]
        n, m, tokens = dimensions(unit["name"])
        require(stats[index]["operations"] == FRAMES * n * m * tokens,
                "Adder operation count differs from frozen graph: " + unit["name"])
        require(stats[index]["row_results"] == FRAMES * m * tokens,
                "Adder completed-row count differs from frozen graph")
        require(starts[index] is not None, "Unobserved original adder")
        bursts.append((starts[index], last_cycles[index], index,
                       last_cycles[index] - starts[index] + 1))
    return sorted(bursts), stats, {"additions_checked": total, "mismatches": 0,
        "row_results_checked": sum(item["row_results"] for item in stats),
        "original_sumR_states_retained": count, "incomplete_rows": 0,
        "multiplier_to_adder_fifo_order_checked": total,
        "multiplier_get_to_add_delay_histogram": dict(sorted(delay_histogram.items())),
        "maximum_observed_product_fifo_entries": maximum_product_fifo,
        "operation_to_register_capture_cycles": 0}


def allocate(bursts):
    active, free, assignments = [], [], []
    count = 0
    for reservation, (start, end, unit, operations) in enumerate(bursts):
        while active and active[0][0] < start:
            _, physical = heapq.heappop(active)
            heapq.heappush(free, physical)
        if free:
            physical = heapq.heappop(free)
        else:
            physical = count
            count += 1
        assignments.append((reservation, unit, physical, start, end, operations))
        heapq.heappush(active, (end + SWITCH_CYCLES, physical))
    return count, assignments


def interval_lower_bound(bursts):
    # Independent sweep of [start,end+2): busy cycles plus one idle guard.
    events = []
    for index, (start, end, unit, operations) in enumerate(bursts):
        events.append((start, 1, index))
        events.append((end + SWITCH_CYCLES + 1, -1, index))
    events.sort()  # At coincident endpoints, remove expired intervals first.
    active, maximum, witness = set(), 0, None
    for cycle, delta, index in events:
        if delta < 0:
            active.remove(index)
        else:
            active.add(index)
        if len(active) > maximum:
            maximum = len(active)
            witness = {"cycle": cycle, "reservation_ids": sorted(active),
                       "original_unit_ids": sorted(bursts[i][2] for i in active)}
    return maximum, witness


def exact_cycle_lower_bound(bursts):
    events = []
    for i, (start, end, unit, ops) in enumerate(bursts):
        events.extend(((start, 1, i), (end + 1, -1, i)))
    events.sort()
    active, peak, witness = set(), 0, None
    for cycle, delta, i in events:
        if delta < 0:
            active.remove(i)
        else:
            active.add(i)
        if len(active) > peak:
            peak = len(active)
            witness = {"cycle": cycle, "reservation_ids": sorted(active),
                       "original_unit_ids": sorted(bursts[i][2] for i in active)}
    return peak, witness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    reference_dir = Path(__file__).resolve().parent
    for name in ("transactions", "units", "multiplier-transactions", "multiplier-units",
                 "control", "observed", "inputhex", "expectedhex", "outputdir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--prior-analyzer", type=Path, default=reference_dir / "analyze.py")
    parser.add_argument("--prior-analysis", type=Path, default=reference_dir / "evidence" / "analysis.json")
    args = parser.parse_args()
    source_paths = {key: value for key, value in vars(args).items() if key != "outputdir"}
    source_paths["analyzer"] = Path(__file__).resolve()
    before = {key: sha(path) for key, path in source_paths.items()}
    spec = importlib.util.spec_from_file_location("prior_analyzer", args.prior_analyzer)
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    inputs, expected = prior.read_hex(args.inputhex), prior.read_hex(args.expectedhex)
    observed, control = (prior.check_kernel(path, inputs, expected) for path in (args.observed, args.control))
    require(observed["sway_lines"] == control["sway_lines"], "Adder observer changes kernel output/cycles")
    units = json.loads(args.units.read_text())
    require(len(units) == 17 and [u["id"] for u in units] == list(range(17)), "Wrong affine adder inventory")
    require(len(set(u["name"] for u in units)) == 17, "Duplicated affine adder")
    for unit in units:
        dimensions(unit["name"])
    multiplier_units = json.loads(args.multiplier_units.read_text())
    old = json.loads(args.prior_analysis.read_text())
    require(old["status"] == "pass" and old["feasible_shared_multipliers"] == 25,
            "Prior multiplier certificate does not certify 25 multipliers")
    require(before["multiplier_transactions"] == old["source_sha256"]["transactions"],
            "Simultaneous multiplier trace differs from prior certificate")
    require(before["multiplier_units"] == old["source_sha256"]["units"], "Multiplier inventory differs")
    window = tuple(observed["window"])
    bursts, stats, arithmetic = analyze_events(args.transactions, units, observed["finish_cycle"],
                                               window, args.multiplier_transactions, multiplier_units)
    count, assignments = allocate(bursts)
    bound, witness = interval_lower_bound(bursts)
    demand, demand_witness = exact_cycle_lower_bound(bursts)
    require(count == bound and count <= len(units), "Interval allocation/bound disagreement")
    window_bursts = [(max(start, window[0]), min(end, window[1] - 1), unit,
                      min(end, window[1] - 1) - max(start, window[0]) + 1)
                     for start, end, unit, operations in bursts if start < window[1] and end >= window[0]]
    window_count, window_assignments = allocate(window_bursts)
    window_bound, window_witness = interval_lower_bound(window_bursts)
    require(window_count == window_bound, "Interior interval bound disagreement")
    by_stage = []
    for unit in units:
        stage_bursts = [b for b in bursts if b[2] == unit["id"]]
        stage_count, _ = allocate(stage_bursts)
        require(stage_count == 1, "Single-stage adder schedule exceeds original one adder")
        by_stage.append({"stage_id": unit["id"], "name": unit["name"], "baseline_adders": 1,
                         "within_stage_adders": stage_count, **stats[unit["id"]]})
    require(len(set(observed["intervals"])) == 1 and observed["intervals"][0] == 18592,
            "Kernel interval changed")
    require(before == {key: sha(path) for key, path in source_paths.items()}, "Analysis inputs changed")
    args.outputdir.mkdir(parents=True, exist_ok=True)
    write_csv(args.outputdir / "assignments.csv", ASSIGNMENT_HEADER, assignments)
    write_csv(args.outputdir / "window_assignments.csv", ASSIGNMENT_HEADER, window_assignments)
    report = {"status": "pass", "kind": "measured baseline trace and fixed-cycle scheduling analysis",
        "frames": 56, "fixture_frames": 14, "fixture_repetitions": 4,
        "scalar_outputs_checked": observed["scalar_outputs"], "kernel_markers_identical": True,
        "finish_cycle": observed["finish_cycle"], "post_completion_drain_cycles": observed["drain_cycles"],
        "operation": "signed INT24 accumulation of sign-extended INT16 product",
        "baseline_affine_adders": len(units), "baseline_adder_count_requires_synthesis_inventory": True,
        "within_stage_adders": sum(stage["within_stage_adders"] for stage in by_stage),
        "cross_stage_adders": count, "reclaimable_adders": len(units) - count,
        "reclaimable_fraction": (len(units) - count) / len(units),
        "full_trace_reservations": len(bursts), "fixed_reservation_lower_bound": bound,
        "lower_bound_witness": witness, "simultaneous_active_adders_peak": demand,
        "active_demand_witness": demand_witness,
        "measurement_window": {"start_inclusive": window[0], "end_exclusive": window[1],
            "cycles": window[1] - window[0], "output_frame_interval_count": 28,
            "cross_stage_adders": window_count, "fixed_reservation_lower_bound": window_bound,
            "lower_bound_witness": window_witness, "reservations": len(window_bursts)},
        "original_output_interval_cycles": 18592, "scheduled_output_interval_cycles": 18592,
        "switch_idle_cycles": SWITCH_CYCLES,
        "reservation_policy": "maximal consecutive same-unit operations; one full idle cycle after every reservation",
        "time_semantics": "combinational request and result capture on the same baseline clock edge",
        "preserved": "all stage-local sumR states, original productQ/sumQ, operation/capture cycles, row order, memory access cycles",
        "by_stage": by_stage, "arithmetic": arithmetic,
        "simultaneous_multiplier_schedule": {"trace_identical_to_previous_verified_56_frame_run": True,
            "baseline_multipliers": 45, "shared_multipliers": 25,
            "multiplier_trace_sha256": before["multiplier_transactions"],
            "joint_result": "Both disjoint operator pools use their original event times in this same run; no rescheduling is introduced"},
        "claim_scope": "Minimum for measured fixed cycles and stated indivisible reservations plus gap; not a hardware-area or arbitrary-workload optimum",
        "physical_limits": "Shared routing, mux/control area and placed/routed timing are unmeasured",
        "source_sha256": before,
        "output_sha256": {name: sha(args.outputdir / name) for name in ("assignments.csv", "window_assignments.csv")}}
    (args.outputdir / "analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "baseline_affine_adders", "cross_stage_adders",
                                                "reclaimable_adders", "simultaneous_active_adders_peak")}))


if __name__ == "__main__":
    main()
