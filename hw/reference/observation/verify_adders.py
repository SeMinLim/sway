#!/usr/bin/env python3
"""Independent checker of serialized adder-sharing certificates and raw events.

This checker does not import or execute the allocator. It checks every event
against each original-unit reservation, then checks each proposed physical lane.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path


def need(condition, description):
    if not condition:
        raise AssertionError(description)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                return value.hexdigest()
            value.update(chunk)


def read_certificate(path):
    with Path(path).open() as stream:
        rows = csv.reader(stream)
        need(next(rows) == ["reservation_id", "original_unit_id", "shared_unit_id",
                            "operation_first_cycle", "capture_last_cycle", "operations"], "certificate header")
        return [tuple(int(value) for value in row) for row in rows]


def check_lanes(rows, physical_count, original_count):
    physical = [[] for _ in range(physical_count)]
    original = [[] for _ in range(original_count)]
    need([row[0] for row in rows] == list(range(len(rows))), "missing or reordered reservation IDs")
    for reservation, unit, lane, start, end, count in rows:
        need(0 <= unit < original_count and 0 <= lane < physical_count, "invalid unit/lane")
        need(start <= end and count == end - start + 1, "invalid continuous reservation")
        physical[lane].append((start, end, unit, reservation))
        original[unit].append((start, end, reservation))
    changes = 0
    minimum_gap = None
    for lane in physical:
        need(lane, "unused reported lane")
        lane.sort()
        for earlier, later in zip(lane, lane[1:]):
            gap = later[0] - earlier[1] - 1
            need(gap >= 1, "overlap or missing complete idle cycle")
            minimum_gap = gap if minimum_gap is None else min(minimum_gap, gap)
            changes += int(earlier[2] != later[2])
    for unit in original:
        need(unit, "unobserved original unit")
        unit.sort()
        for earlier, later in zip(unit, unit[1:]):
            need(later[0] >= earlier[1] + 2, "nonmaximal or overlapping original reservations")
    return original, {"reservations_checked": len(rows), "owner_changes": changes,
                      "minimum_idle_gap_cycles": minimum_gap,
                      "request_cycles_moved": 0, "capture_cycles_moved": 0}


def check_witness(rows, count, witness, guard):
    selected = witness["reservation_ids"]
    need(len(selected) == count and len(set(selected)) == count, "witness count")
    cycle = witness["cycle"]
    original = []
    for index in selected:
        need(0 <= index < len(rows), "invalid witness reservation")
        row = rows[index]
        need(row[3] <= cycle <= row[4] + guard, "witness intervals do not intersect")
        original.append(row[1])
    need(sorted(original) == witness["original_unit_ids"], "witness identities")
    need(len(set(original)) == len(original), "witness repeats original unit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--transactions", type=Path, required=True)
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.analysis.read_text())
    units = json.loads(args.units.read_text())
    need(report["status"] == "pass", "analysis failed")
    need(digest(args.transactions) == report["source_sha256"]["transactions"], "raw trace hash mismatch")
    need(digest(args.units) == report["source_sha256"]["units"], "units hash mismatch")
    rows = read_certificate(args.analysis.parent / "assignments.csv")
    need(digest(args.analysis.parent / "assignments.csv") == report["output_sha256"]["assignments.csv"],
         "serialized assignment hash mismatch")
    original, checked = check_lanes(rows, report["cross_stage_adders"], len(units))
    check_witness(rows, report["fixed_reservation_lower_bound"], report["lower_bound_witness"], 1)
    check_witness(rows, report["simultaneous_active_adders_peak"], report["active_demand_witness"], 0)
    need(report["fixed_reservation_lower_bound"] == report["cross_stage_adders"], "bound not tight")
    need(report["within_stage_adders"] == len(units), "within-stage inventory mismatch")
    window_rows = read_certificate(args.analysis.parent / "window_assignments.csv")
    need(digest(args.analysis.parent / "window_assignments.csv") == report["output_sha256"]["window_assignments.csv"],
         "serialized interior assignment hash mismatch")
    window = report["measurement_window"]
    _, window_checked = check_lanes(window_rows, window["cross_stage_adders"], len(units))
    check_witness(window_rows, window["fixed_reservation_lower_bound"], window["lower_bound_witness"], 1)
    expected_interior = sorted((max(start, window["start_inclusive"]), min(end, window["end_exclusive"] - 1), unit)
        for reservation, unit, physical, start, end, operations in rows
        if start < window["end_exclusive"] and end >= window["start_inclusive"])
    measured_interior = sorted((row[3], row[4], row[1]) for row in window_rows)
    need(measured_interior == expected_interior, "interior reservations differ from complete trace")
    cursors = [0] * len(units)
    next_cycles = [unit[0][0] for unit in original]
    state = [0] * len(units)
    additions = [0] * len(units)
    row_results = [0] * len(units)
    previous = (-1, -1)
    with args.transactions.open() as stream:
        events = csv.reader(stream)
        need(next(events) == ["cycle", "unit_id", "last", "a", "b", "result"], "raw event header")
        for event in events:
            cycle, unit, last, a, b, result = map(int, event)
            need((cycle, unit) > previous, "event duplication or order")
            previous = (cycle, unit)
            need(0 <= unit < len(units), "event unit")
            need(cursors[unit] < len(original[unit]), "event has no reservation")
            need(cycle == next_cycles[unit], "operation/capture cycle changed or missing")
            begin, end, reservation = original[unit][cursors[unit]]
            need(begin <= cycle <= end, "event outside its reservation")
            need(a == state[unit], "stage-local sumR state not retained")
            need(last in (0, 1), "row-final flag")
            need(-(1 << 23) <= a < (1 << 23) and -(1 << 15) <= b < (1 << 15), "operand widths")
            need(-(1 << 23) <= result < (1 << 23), "result width")
            need(result == a + b, "mathematical add differs; overflow or arithmetic mismatch")
            state[unit] = 0 if last else result
            additions[unit] += 1
            row_results[unit] += last
            if cycle == end:
                cursors[unit] += 1
                next_cycles[unit] = (original[unit][cursors[unit]][0]
                    if cursors[unit] < len(original[unit]) else None)
            else:
                next_cycles[unit] = cycle + 1
    need(all(cursors[i] == len(original[i]) for i in range(len(units))), "uncovered reserved cycles")
    need(all(value == 0 for value in state), "unfinished accumulator row")
    need(sum(additions) == report["arithmetic"]["additions_checked"], "operation count differs")
    need(sum(row_results) == report["arithmetic"]["row_results_checked"], "row result count differs")
    for index, stage in enumerate(report["by_stage"]):
        need(additions[index] == stage["operations"] and row_results[index] == stage["row_results"],
             "per-stage operation count differs")
    result = {"status": "pass", "checker_imports_allocator": False,
        "additions_independently_checked": sum(additions), "row_results_independently_checked": sum(row_results),
        "full_trace": checked, "interior_window": window_checked,
        "lower_bound_witnesses_checked": True, "all_reserved_cycles_have_exactly_one_original_operation": True,
        "all_original_operations_have_exactly_one_assignment": True,
        "original_sumR_states_retained": len(units), "unretired_adder_operations": 0,
        "source_sha256": {"analysis": digest(args.analysis), "transactions": digest(args.transactions),
                          "checker": digest(__file__)}}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
