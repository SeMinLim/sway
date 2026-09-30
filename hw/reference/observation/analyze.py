#!/usr/bin/env python3
"""Check a complete baseline trace and analyze conservative multiplier sharing.

This is a fixed-cycle scheduling analysis, not an implementation of shared RTL.
All requests, results, original stage metadata, and memory accesses keep their
baseline cycles. A multiplier changes owner only after every accepted product
has been consumed, followed by one complete idle cycle.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import csv
import hashlib
import heapq
import json
from pathlib import Path
import re


FRAME_COUNT = 56
FIXTURE_COUNT = 14
INPUT_WORDS = 320
OUTPUT_WORDS = 57
SWITCH_CYCLES = 1


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_hex(path):
    values = []
    for line in Path(path).read_text().splitlines():
        require(re.fullmatch(r"[0-9a-fA-F]{2}", line) is not None,
                "Invalid signed INT8 hex input")
        value = int(line, 16)
        values.append(value if value < 128 else value - 256)
    return values


def check_kernel(path, inputs, expected):
    require(len(inputs) == FRAME_COUNT * INPUT_WORDS, "Expected 56 input frames")
    require(len(expected) == FRAME_COUNT * OUTPUT_WORDS, "Expected 3192 scalar outputs")
    for repeat in range(1, 4):
        for values, width in ((inputs, INPUT_WORDS), (expected, OUTPUT_WORDS)):
            size = FIXTURE_COUNT * width
            require(values[:size] == values[repeat * size:(repeat + 1) * size],
                    "Expected four repetitions of the same 14 fixtures")
    records = {key: [] for key in ("SWAY_OUTPUT", "SWAY_INPUT_FRAME", "SWAY_FRAME")}
    passed = []
    sway_lines = []
    for line in Path(path).read_text().splitlines():
        if not line.startswith("SWAY_"):
            require(not re.search(r"FATAL|Error:|ARITH_FAIL|%Error", line), "Simulation failure")
            continue
        sway_lines.append(line)
        require(not passed, "Kernel records after completion")
        if line.startswith("SWAY_PASS "):
            match = re.fullmatch(r"SWAY_PASS frames=(\d+) outputs=(\d+) cycles=(\d+) drain_cycles=(\d+)", line)
            require(match is not None, "Malformed kernel completion")
            passed.append(tuple(map(int, match.groups())))
        else:
            fields = line.split(",")
            require(len(fields) == 4 and fields[0] in records, "Unexpected kernel record: " + line)
            records[fields[0]].append(tuple(map(int, fields[1:])))
    require(len(passed) == 1, "Missing kernel completion")
    frames, output_count, finish, drain = passed[0]
    require((frames, output_count) == (FRAME_COUNT, len(expected)), "Incomplete kernel run")
    outputs = records["SWAY_OUTPUT"]
    require([(index, value) for index, value, cycle in outputs] == list(enumerate(expected)),
            "Kernel output differs from frozen expected values")
    require(all(a[2] < b[2] for a, b in zip(outputs, outputs[1:])), "Output cycles not increasing")
    require(drain >= 2048 and finish - outputs[-1][2] >= drain, "Missing post-completion drain")
    for key in ("SWAY_INPUT_FRAME", "SWAY_FRAME"):
        require([row[0] for row in records[key]] == list(range(FRAME_COUNT)), "Missing or duplicated frame")
    for frame in range(FRAME_COUNT):
        _, first_input, last_input = records["SWAY_INPUT_FRAME"][frame]
        _, first_output, last_output = records["SWAY_FRAME"][frame]
        scalars = outputs[frame * OUTPUT_WORDS:(frame + 1) * OUTPUT_WORDS]
        require(0 <= first_input and first_input + INPUT_WORDS - 1 <= last_input < first_output,
                "Invalid input/output dependency")
        require((first_output, last_output) == (scalars[0][2], scalars[-1][2]),
                "Frame markers disagree with scalar outputs")
        require(all(b[2] == a[2] + 1 for a, b in zip(scalars, scalars[1:])), "Output sink is stalled")
        if frame:
            require(first_input > records["SWAY_INPUT_FRAME"][frame - 1][2], "Input frames overlap")
    starts = [row[1] for row in records["SWAY_FRAME"]]
    intervals = [b - a for a, b in zip(starts, starts[1:])]
    window = (starts[14], starts[42])
    return {"frames": records["SWAY_FRAME"], "intervals": intervals,
            "window": window, "finish_cycle": finish, "sway_lines": sway_lines,
            "scalar_outputs": len(outputs), "drain_cycles": drain}


def expected_unit_names():
    suffixes = ["bProjection_multipliers_0", "cProjection_multipliers_0"]
    suffixes += ["convMultipliers_0_" + str(i) for i in range(4)]
    suffixes += ["deltaInputProjection_multipliers_0", "deltaProjection_engine_multipliers_0",
                 "gateMultipliers_0", "gateProjection_multipliers_0", "mainProjection_multipliers_0",
                 "normalization_biasedMultiplier_0", "normalization_weightedMultiplier_0",
                 "outputProjection_engine_multipliers_0", "scan_deltaAMultipliers_0",
                 "scan_deltaBMultipliers_0", "scan_directMultiplier", "scan_highMultipliers_0",
                 "scan_inputMultipliers_0", "scan_lowMultipliers_0", "scan_stateMultipliers_0"]
    names = ["dut_block" + str(block) + "_" + suffix for block in range(2) for suffix in suffixes]
    names += ["dut_" + stage + "_engine_multipliers_0" for stage in ("embedding", "headHidden", "headOutput")]
    return names


def check_metadata(units, stages):
    require([item["id"] for item in units] == list(range(45)), "Expected 45 multiplier IDs")
    require([item["name"] for item in units] == expected_unit_names(), "Unexpected multiplier inventory")
    require([item["id"] for item in stages] == list(range(25)), "Expected 25 stage IDs")
    require(len({item["name"] for item in stages}) == 25, "Duplicated stage name")
    mapping = []
    for unit in units:
        name = re.sub(r"_convMultipliers_.*$", "_convolution", unit["name"])
        name = re.sub(r"_gateMultipliers_.*$", "_gate", name)
        matches = [stage["id"] for stage in stages
                   if name == stage["name"] or name.startswith(stage["name"] + "_")]
        require(len(matches) == 1, "Missing or ambiguous multiplier stage")
        mapping.append(matches[0])
    return mapping


def check_product_counts(unit_stats):
    # Fixed baseline graph: 16 tokens/frame, model=20, inner=40, state=8,
    # delta rank=2. Each of the seven scan multipliers issues 40*8/token,
    # including directMultiplier, which repeats the direct term per state part.
    block = [16 * 40 * 8, 16 * 40 * 8] + [16 * 40] * 4
    block += [16 * 40 * 2, 16 * 2 * 40, 16 * 40,
              16 * 20 * 40, 16 * 20 * 40, 16 * 20, 16 * 20,
              16 * 40 * 20] + [16 * 40 * 8] * 7
    per_frame = block * 2 + [16 * 20 * 20, 320 * 20, 20 * 57]
    require(len(unit_stats) == len(per_frame), "Unexpected product-count inventory")
    for unit, (stats, count) in enumerate(zip(unit_stats, per_frame)):
        require(stats["puts_full_trace"] == FRAME_COUNT * count,
                "Missing/extra baseline products for unit " + str(unit))
    return sum(per_frame) * FRAME_COUNT


def overlap(start, end, window):
    """Half-open intersection length."""
    return max(0, min(end, window[1]) - max(start, window[0]))


def numeric_rows(path, header):
    with Path(path).open() as stream:
        reader = csv.reader(stream)
        require(next(reader, None) == header, "Unexpected CSV header: " + str(path))
        for row in reader:
            require(len(row) == len(header), "Incomplete CSV row: " + str(path))
            require(all(re.fullmatch(r"-?\d+", field) for field in row), "Noninteger CSV field")
            yield tuple(map(int, row))


def read_transactions(path, count, stage_mapping, stage_count, finish, window):
    queues = [deque() for _ in range(count)]
    starts = [None] * count
    burst_puts = [0] * count
    unit_stats = [{"puts_full_trace": 0, "puts_window": 0, "gets_window": 0,
                   "reserved_cycles_window": 0, "maximum_outstanding": 0} for _ in range(count)]
    stage_active = [0] * stage_count
    last_active = [-1] * stage_count
    bursts = []
    latencies = Counter()
    previous = (-1, -1)
    rows = 0
    for cycle, unit, put, get, a, b, result in numeric_rows(
            path, ["cycle", "unit_id", "put", "get", "a", "b", "result"]):
        require(0 <= unit < count and 0 <= cycle < finish, "Invalid transaction unit/cycle")
        require((cycle, unit) > previous, "Duplicated or unordered transaction")
        previous = (cycle, unit)
        require(put in (0, 1) and get in (0, 1) and put + get > 0, "Invalid transaction enables")
        require(-(1 << 17) <= a < (1 << 17) and -(1 << 17) <= b < (1 << 17), "Operand exceeds signed18")
        require(-(1 << 35) <= result < (1 << 35), "Product exceeds signed36")
        require(put or (a == 0 and b == 0), "Operands present without put")
        require(get or result == 0, "Result present without get")
        queue = queues[unit]
        # Baseline put guard uses the pre-edge outstanding count, even with get.
        require(not put or len(queue) < 8, "Baseline eight-entry capacity exceeded")
        if get:
            require(bool(queue), "Result consumed without accepted request")
            issued, product = queue.popleft()
            require(cycle - issued >= 4, "Product consumed before baseline pipeline permits")
            require(product == result, "Arithmetic result or FIFO order mismatch")
            latencies[cycle - issued] += 1
            unit_stats[unit]["gets_window"] += int(window[0] <= cycle < window[1])
        if put:
            if starts[unit] is None:
                starts[unit] = cycle
            queue.append((cycle, a * b))
            burst_puts[unit] += 1
            unit_stats[unit]["puts_full_trace"] += 1
            unit_stats[unit]["maximum_outstanding"] = max(unit_stats[unit]["maximum_outstanding"], len(queue))
            if window[0] <= cycle < window[1]:
                unit_stats[unit]["puts_window"] += 1
                stage = stage_mapping[unit]
                if last_active[stage] != cycle:
                    stage_active[stage] += 1
                    last_active[stage] = cycle
        if not queue:
            require(starts[unit] is not None, "Missing reservation start")
            bursts.append((starts[unit], cycle, unit, burst_puts[unit]))
            unit_stats[unit]["reserved_cycles_window"] += overlap(starts[unit], cycle + 1, window)
            starts[unit] = None
            burst_puts[unit] = 0
        rows += 1
    require(rows > 0 and all(not queue for queue in queues), "Trace ends with unretired requests")
    require(all(item["puts_full_trace"] > 0 for item in unit_stats), "Unobserved baseline multiplier")
    require(sum(item["puts_full_trace"] for item in unit_stats) == sum(latencies.values()), "Incomplete retirement")
    return sorted(bursts), unit_stats, stage_active, {
        "trace_rows": rows, "products_checked": sum(latencies.values()),
        "mismatches": 0, "latency_histogram_cycles": dict(sorted(latencies.items())),
        "all_requests_retired": True}


def assign_bursts(bursts):
    active = []
    free = []
    count = 0
    assignments = []
    for burst_id, (start, end, unit, products) in enumerate(bursts):
        while active and active[0][0] < start:
            _, physical = heapq.heappop(active)
            heapq.heappush(free, physical)
        if free:
            physical = heapq.heappop(free)
        else:
            physical = count
            count += 1
        assignments.append((burst_id, unit, physical, start, end, products))
        heapq.heappush(active, (end + SWITCH_CYCLES, physical))
    return count, assignments


def verify_assignments(bursts, assignments, count):
    """Independent certificate check; does not use the allocator's heaps."""
    require(len(assignments) == len(bursts) and count > 0, "Missing schedule reservation")
    seen = set()
    lanes = [[] for _ in range(count)]
    for burst_id, unit, physical, start, end, products in assignments:
        require(0 <= burst_id < len(bursts) and burst_id not in seen, "Duplicated or invalid reservation ID")
        seen.add(burst_id)
        require((start, end, unit, products) == bursts[burst_id], "Original transaction reservation changed")
        require(0 <= physical < count, "Invalid physical multiplier ID")
        lanes[physical].append((start, end, unit))
    transitions = 0
    owner_switches = 0
    for lane in lanes:
        require(bool(lane), "Unused physical multiplier in reported count")
        lane.sort()
        for previous, current in zip(lane, lane[1:]):
            require(current[0] - previous[1] - 1 >= SWITCH_CYCLES,
                    "Overlapping reservations or missing operand transition cycle")
            transitions += 1
            owner_switches += int(current[2] != previous[2])
    return {"status": "pass", "reservations_checked": len(seen),
            "reserved_transition_cycles": transitions * SWITCH_CYCLES,
            "owner_changes": owner_switches,
            "request_and_consumption_cycles_moved": 0,
            "in_flight_operations_at_owner_change": 0}


def stage_confined_schedule(bursts, stages, mapping):
    """Same trace and transition cost, with no sharing across stage boundaries."""
    allocations = []
    total = 0
    for stage in stages:
        members = [unit for unit, group in enumerate(mapping) if group == stage["id"]]
        local_bursts = [burst for burst in bursts if burst[2] in members]
        count, assignments = assign_bursts(local_bursts)
        verify_assignments(local_bursts, assignments, count)
        require(count <= len(members), "Stage-confined schedule exceeds original stage resources")
        allocations.append({"stage_id": stage["id"], "name": stage["name"],
                            "baseline_multipliers": len(members),
                            "stage_confined_multipliers": count,
                            "reclaimed_within_stage": len(members) - count})
        total += count
    return total, allocations


def read_waits(path, count, finish, window):
    previous = (-1, -1)
    states = [None] * count
    totals = [[0, 0] for _ in range(count)]
    terminal = None
    for cycle, stage, input_wait, output_wait in numeric_rows(
            path, ["cycle", "stage", "input_wait", "output_wait"]):
        require(terminal is None, "Records after wait-trace completion")
        if stage == -1:
            require(cycle in (finish, finish + 1) and input_wait == 0 and output_wait == 0,
                    "Invalid wait-trace completion")
            terminal = cycle
            require(all(state is not None for state in states), "Missing initial stage state")
            for index, (start, value_in, value_out) in enumerate(states):
                length = overlap(start, cycle, window)
                totals[index][0] += value_in * length
                totals[index][1] += value_out * length
            continue
        require(0 <= stage < count and 0 <= cycle < finish, "Invalid wait stage/cycle")
        require((cycle, stage) > previous, "Duplicated or unordered wait transition")
        previous = (cycle, stage)
        require(input_wait in (0, 1) and output_wait in (0, 1), "Invalid wait state")
        old = states[stage]
        if old is None:
            require(cycle == 0, "Stage missing cycle-zero state")
        else:
            start, value_in, value_out = old
            require((input_wait, output_wait) != (value_in, value_out), "Redundant wait transition")
            length = overlap(start, cycle, window)
            totals[stage][0] += value_in * length
            totals[stage][1] += value_out * length
        states[stage] = (cycle, input_wait, output_wait)
    require(terminal is not None, "Wait trace has no completion marker")
    return totals


def write_csv(path, header, rows):
    with Path(path).open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("transactions", "units", "stages", "waits", "control", "observed", "inputhex", "expectedhex", "outputdir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    source_paths = {name: getattr(args, name) for name in vars(args) if name != "outputdir"}
    source_paths["analyzer"] = Path(__file__).resolve()
    before = {name: file_hash(path) for name, path in source_paths.items()}
    inputs, expected = read_hex(args.inputhex), read_hex(args.expectedhex)
    control = check_kernel(args.control, inputs, expected)
    observed = check_kernel(args.observed, inputs, expected)
    require(control["sway_lines"] == observed["sway_lines"], "Observer changes baseline kernel behavior")
    units = json.loads(args.units.read_text())
    stages = json.loads(args.stages.read_text())
    mapping = check_metadata(units, stages)
    window = observed["window"]
    bursts, unit_stats, stage_active, arithmetic = read_transactions(
        args.transactions, len(units), mapping, len(stages), observed["finish_cycle"], window)
    arithmetic["fixed_graph_product_count_checked"] = check_product_counts(unit_stats)
    count, assignments = assign_bursts(bursts)
    certificate = verify_assignments(bursts, assignments, count)
    confined_count, allocations = stage_confined_schedule(bursts, stages, mapping)
    waits = read_waits(args.waits, len(stages), observed["finish_cycle"], window)
    require(count <= len(units), "Conservative schedule does not fit baseline resource count")
    window_intervals = observed["intervals"][14:42]
    require(len(window_intervals) == 28 and len(set(window_intervals)) == 1,
            "Interior output intervals are not constant; a single throughput value is invalid")
    after = {name: file_hash(path) for name, path in source_paths.items()}
    require(before == after, "Analysis inputs changed while reading")
    args.outputdir.mkdir(parents=True, exist_ok=True)
    assignment_header = ["reservation_id", "original_unit_id", "shared_unit_id", "first_put_cycle", "last_get_cycle", "products"]
    write_csv(args.outputdir / "assignments.csv", assignment_header, assignments)
    # Verify the serialized certificate, including file corruption/serialization errors.
    certificate = verify_assignments(bursts, list(numeric_rows(args.outputdir / "assignments.csv", assignment_header)), count)
    duration = window[1] - window[0]
    unit_header = ["unit_id", "name", "stage_id", *unit_stats[0], "issue_utilization_window", "reservation_utilization_window"]
    unit_rows = []
    for unit, stats in zip(units, unit_stats):
        unit_rows.append([unit["id"], unit["name"], mapping[unit["id"]], *stats.values(),
                          stats["puts_window"] / duration, stats["reserved_cycles_window"] / duration])
    write_csv(args.outputdir / "units.csv", unit_header, unit_rows)
    stage_rows = []
    for stage in stages:
        index = stage["id"]
        members = [unit for unit, group in enumerate(mapping) if group == index]
        stage_rows.append([index, stage["name"], len(members),
                           sum(unit_stats[unit]["puts_window"] for unit in members),
                           stage_active[index], waits[index][0], waits[index][1]])
    write_csv(args.outputdir / "stages.csv", ["stage_id", "name", "multipliers", "valid_products_window",
              "cycles_with_valid_multiply_window", "input_boundary_wait_cycles_window",
              "output_boundary_wait_cycles_window"], stage_rows)
    report = {
        "status": "pass", "kind": "baseline trace measurement and fixed-cycle scheduling analysis",
        "frames_checked": FRAME_COUNT, "fixture_frames": FIXTURE_COUNT, "fixture_repetitions": 4,
        "scalar_outputs_checked": observed["scalar_outputs"], "control_observer_markers_identical": True,
        "full_trace_finish_cycle": observed["finish_cycle"], "post_completion_drain_cycles": observed["drain_cycles"],
        "measurement_window": {"start_inclusive_cycle": window[0], "end_exclusive_cycle": window[1],
                               "cycles": duration, "first_output_frame": 14, "end_output_frame_exclusive": 42,
                               "output_intervals": 28},
        "baseline_multipliers": len(units), "compatible_operation": "signed 18 x 18 -> 36 multiplication",
        "feasible_shared_multipliers": count, "reclaimable_multipliers": len(units) - count,
        "reclaimable_fraction": (len(units) - count) / len(units),
        "stage_confined_total": confined_count,
        "reclaimable_within_stages": len(units) - confined_count,
        "additional_cross_stage_reclaim": confined_count - count,
        "allocation_by_stage": allocations,
        "kernel_output_interval_cycles": window_intervals[0],
        "scheduled_output_interval_cycles": window_intervals[0],
        "output_frame_start_intervals_cycles": observed["intervals"],
        "transition_cost_cycles": SWITCH_CYCLES,
        "transition_policy": "one full idle cycle after every fully drained reservation, even when owner is unchanged",
        "memory_and_dependencies": "All request/consume cycles and baseline SRAM accesses remain unchanged; no retiming, extra ports, or memory sharing",
        "metadata": "Original stage metadata remains local and ordered; owner changes occur only with zero unconsumed products",
        "wait_measurement": "Input starvation and output boundary backpressure are independent, non-additive predicates; neither implies all stage logic is idle",
        "scope": "Feasible schedule for this complete four-repetition trace, not an optimal-resource claim or implemented shared RTL",
        "physical_limits": "Shared mux/control/storage area and routed frequency are not measured; no net FPGA-area saving is claimed",
        "arithmetic": arithmetic, "schedule_certificate": certificate,
        "source_sha256": before,
        "output_sha256": {name: file_hash(args.outputdir / name) for name in ("assignments.csv", "units.csv", "stages.csv")}}
    (args.outputdir / "analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "baseline_multipliers", "feasible_shared_multipliers",
          "reclaimable_multipliers", "kernel_output_interval_cycles")}))


if __name__ == "__main__":
    main()
