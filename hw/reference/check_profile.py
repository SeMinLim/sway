#!/usr/bin/env python3
"""Verify Sway boundary transactions and summarize their cycle intervals."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
from statistics import fmean

from check_perf import INPUT_WORDS, STREAM_FRAMES, TRIM_FRAMES, check_log, hardware_sources, read_hex, sha256, source_manifest

TOKEN_BOUNDARIES = ("patch_ready", "embedding_in", "block0_in", "block1_in", "head_in")
FRAME_BOUNDARIES = ("head_output_in", "serialize_in")
BOUNDARIES = TOKEN_BOUNDARIES + FRAME_BOUNDARIES
TOKENS = 16
CSV_FIELDS = ("stage", "frame", "token", "input_cycle", "output_cycle", "elapsed_cycles")


def distribution(values: list[int]) -> dict:
    if not values:
        raise ValueError("Cannot summarize an empty cycle distribution")
    counts = Counter(values)
    return {"count": len(values), "min": min(values), "mean": fmean(values),
            "max": max(values), "histogram": {str(k): counts[k] for k in sorted(counts)}}


def check_schedules(baseline_text: str, profile_text: str) -> dict:
    """Compare compiler predicates, blockers, and the original execution order."""
    def parse_schedule(text: str) -> tuple[dict, list[str]]:
        if text.count("Logical execution order:") != 1:
            raise AssertionError("Missing or duplicate logical execution order")
        rule_text, order_text = text.split("Logical execution order:")
        rules = {}
        first_rule = re.search(r"^Rule:", rule_text, re.MULTILINE)
        if first_rule is None:
            raise AssertionError("Missing rule schedule")
        for block in re.split(r"\n\s*\n", rule_text[first_rule.start():].strip()):
            if not block.startswith("Rule:"):
                continue
            match = re.fullmatch(r"Rule: (\S+)\nPredicate: (.*?)\nBlocking rules: (.*)", block, re.DOTALL)
            if match is None or match[1] in rules:
                raise AssertionError("Malformed or duplicate schedule rule")
            rules[match[1]] = tuple(" ".join(match[i].split()) for i in (2, 3))
        if not rules or len(rules) != len(re.findall(r"^Rule:", rule_text, re.MULTILINE)):
            raise AssertionError("Incomplete rule schedule")
        order = [name.strip() for name in order_text.strip().split("\n\n", 1)[0].split(",")]
        if len(order) != len(rules) or set(order) != set(rules):
            raise AssertionError("Execution order does not contain every rule exactly once")
        return rules, order

    baseline_rules, baseline_order = parse_schedule(baseline_text)
    profile_rules, profile_order = parse_schedule(profile_text)
    added = "test_dut_profileTick"
    if set(profile_rules) != set(baseline_rules) | {added} or added in baseline_rules:
        raise AssertionError("Profile schedule must add only test_dut_profileTick")
    for name, schedule in baseline_rules.items():
        if profile_rules[name] != schedule:
            raise AssertionError(f"Profile changed predicate or blocking rules: {name}")
    if profile_rules[added] != ("True", "(none)"):
        raise AssertionError("Profile tick must be unconditional and unblocked")
    if [name for name in profile_order if name != added] != baseline_order:
        raise AssertionError("Profile changed original logical execution order")
    return {"status": "pass", "original_rules_checked": len(baseline_rules),
            "added_rule": added, "original_predicates_and_blockers_equal": True,
            "original_logical_execution_order_equal": True}


def check_profile(text: str, stderr: str, baseline_text: str, baseline_stderr: str,
                  expected: list[int], fixture_frames: int) -> tuple[dict, list[dict]]:
    """Return a validated summary and stage spans; no source files are modified."""
    if "SWAY_PROFILE" in stderr:
        raise AssertionError("Unexpected profile record or failure on simulator stderr")
    events = {name: [] for name in BOUNDARIES}
    filtered = []
    seen_begin = False
    seen_pass = False
    previous_cycle = -1
    for line in text.splitlines():
        if "SWAY_PROFILE" not in line:
            filtered.append(line)
            if line.startswith("SWAY_PERF_BEGIN,"):
                seen_begin = True
            if line.startswith("SWAY_PERF_PASS,"):
                seen_pass = True
            continue
        match = re.fullmatch(r"SWAY_PROFILE,([a-z0-9_]+),(\d+),(\d+),(\d+)", line)
        if match is None or match[1] not in events:
            raise AssertionError(f"Unknown or malformed profile record: {line}")
        if not seen_begin or seen_pass:
            raise AssertionError("Profile transaction outside BEGIN/PASS")
        name = match[1]
        frame, token, cycle = (int(match[i]) for i in (2, 3, 4))
        width = TOKENS if name in TOKEN_BOUNDARIES else 1
        ordinal = len(events[name])
        if (frame, token) != divmod(ordinal, width) or frame >= STREAM_FRAMES:
            raise AssertionError(f"Missing, duplicate, or reordered {name} transaction")
        if cycle < previous_cycle or (events[name] and cycle <= events[name][-1][2]):
            raise AssertionError(f"Non-monotonic profile cycles at {name}")
        previous_cycle = cycle
        events[name].append((frame, token, cycle))

    # Only recognized, validated profile records are removed. The existing checker
    # still sees all other records, failures, scalar values, and simulator stderr.
    stream = check_log("\n".join(filtered), stderr, expected, fixture_frames, STREAM_FRAMES)
    baseline = check_log(baseline_text, baseline_stderr, expected, fixture_frames, STREAM_FRAMES)
    actual_perf = [line for line in filtered if line.startswith("SWAY_PERF")]
    baseline_perf = [line for line in baseline_text.splitlines() if line.startswith("SWAY_PERF")]
    if actual_perf != baseline_perf or stream != baseline:
        raise AssertionError("Profile instrumentation changed baseline performance transactions")
    for name in BOUNDARIES:
        wanted = STREAM_FRAMES * (TOKENS if name in TOKEN_BOUNDARIES else 1)
        if len(events[name]) != wanted:
            raise AssertionError(f"Expected {wanted} {name} transactions, got {len(events[name])}")

    spans = []

    def add_span(stage: str, frame: int, token: int, start: int, end: int) -> None:
        if end < start:
            raise AssertionError(f"Output preceded input: {stage}, frame {frame}, token {token}")
        spans.append(dict(zip(CSV_FIELDS, (stage, frame, token, start, end, end - start))))

    token_stages = ("patch_queue", "embedding", "block0", "block1")
    for frame in range(STREAM_FRAMES):
        perf = stream["frames"][frame]
        if events["patch_ready"][frame * TOKENS][2] <= perf["input_last_cycle"]:
            raise AssertionError(f"Patch preceded complete frame input: frame {frame}")
        for token in range(TOKENS):
            index = frame * TOKENS + token
            for stage, before, after in zip(token_stages, TOKEN_BOUNDARIES, TOKEN_BOUNDARIES[1:]):
                add_span(stage, frame, token, events[before][index][2], events[after][index][2])
        first_head = events["head_in"][frame * TOKENS][2]
        last_head = events["head_in"][(frame + 1) * TOKENS - 1][2]
        head_output = events["head_output_in"][frame][2]
        serialize = events["serialize_in"][frame][2]
        add_span("head_hidden_first_token", frame, 0, first_head, head_output)
        add_span("head_hidden_last_token", frame, 15, last_head, head_output)
        add_span("head_output", frame, 0, head_output, serialize)
        add_span("serializer", frame, 0, serialize, perf["output_last_cycle"])
        if serialize >= perf["output_first_cycle"]:
            raise AssertionError(f"Serialized output preceded serializer input: frame {frame}")

    first = TRIM_FRAMES
    last = STREAM_FRAMES - TRIM_FRAMES - 1
    stage_summary = {}
    for stage in dict.fromkeys(row["stage"] for row in spans):
        selected = [row for row in spans if row["stage"] == stage]
        stage_summary[stage] = {
            "all_frames_cycles": distribution([row["elapsed_cycles"] for row in selected]),
            "first_frame_cycles": distribution([row["elapsed_cycles"] for row in selected if row["frame"] == 0]),
            "steady_frames_cycles": distribution([row["elapsed_cycles"] for row in selected if first <= row["frame"] <= last]),
        }

    boundary_summary = {}
    for name in BOUNDARIES:
        selected = [event for event in events[name] if first <= event[0] <= last]
        width = TOKENS if name in TOKEN_BOUNDARIES else 1
        completions = [event[2] for event in selected if event[1] == width - 1]
        result = {
            "transactions": len(events[name]),
            "steady_transaction_intervals_cycles": distribution([b[2] - a[2] for a, b in zip(selected, selected[1:])]),
            "steady_frame_completion_intervals_cycles": distribution([b - a for a, b in zip(completions, completions[1:])]),
            "frame_completion_cycles": [cycle for _, token, cycle in events[name] if token == width - 1],
        }
        if width == TOKENS:
            initial = [event[2] for event in events[name] if event[0] == 0]
            result["first_frame_transaction_cycles"] = initial
            result["first_frame_token_intervals_cycles"] = distribution([b - a for a, b in zip(initial, initial[1:])])
            result["steady_within_frame_token_intervals_cycles"] = distribution([
                b[2] - a[2] for a, b in zip(selected, selected[1:]) if a[0] == b[0]])
            result["steady_between_frame_token_intervals_cycles"] = distribution([
                b[2] - a[2] for a, b in zip(selected, selected[1:]) if a[0] != b[0]])
        boundary_summary[name] = result

    completions = [row["output_last_cycle"] for row in stream["frames"]]
    steady = completions[first:last + 1]
    return {
        "status": "pass",
        "evidence": "Bluesim simulation of the baseline with boundary logging enabled",
        "scope": "Kernel boundary transaction cycles; not board measurements or pure computation times",
        "dut": "mkSwayBaseline",
        "frames_checked": STREAM_FRAMES,
        "scalar_outputs_checked": stream["scalar_outputs_checked"],
        "profile_transactions_checked": sum(len(value) for value in events.values()),
        "baseline_perf_records_exactly_equal": True,
        "cycle_convention": "Output transaction cycle minus input transaction cycle, without +1; shared root-reset cycle origin",
        "stage_span_interpretation": "Accepted-to-transferred spans include internal queues and downstream backpressure; overlapping spans must not be summed",
        "head_hidden_interpretation": "First-token span includes collection of all 16 tokens; last-token span begins at acceptance of token 15, not at internal computation start",
        "serializer_interpretation": "Acceptance of the 57-coordinate vector through the testbench's final scalar get, including output FIFO residence",
        "steady_window": {"first_frame": first, "last_frame": last,
                          "policy": "Frames 8 through 55 of 64; distributions report observed variation without assuming convergence"},
        "stage_spans": stage_summary,
        "boundaries": boundary_summary,
        "kernel_output": {"frame_completion_cycles": completions,
                          "all_frame_intervals_cycles": distribution([b - a for a, b in zip(completions, completions[1:])]),
                          "steady_frame_intervals_cycles": distribution([b - a for a, b in zip(steady, steady[1:])])},
    }, spans


def main() -> None:
    hw_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=Path("results/profile/bluesim/stream.log"))
    parser.add_argument("--stderr", type=Path, default=Path("results/profile/bluesim/stream.stderr.log"))
    parser.add_argument("--baseline-log", type=Path, default=Path("results/perf/bluesim/stream.log"))
    parser.add_argument("--baseline-stderr", type=Path, default=Path("results/perf/bluesim/stream.stderr.log"))
    parser.add_argument("--input", type=Path, default=Path("generated/test_input.hex"))
    parser.add_argument("--expected", type=Path, default=Path("generated/test_expected.hex"))
    parser.add_argument("--backend", choices=("bluesim", "iverilog"), default="bluesim")
    parser.add_argument("--linear-source", type=Path, default=hw_dir / "bsv/SwayLinear.bsv",
                        help="SwayLinear package selected by the build, for source provenance")
    parser.add_argument("--baseline-schedule", type=Path)
    parser.add_argument("--profile-schedule", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results/profile/bluesim/summary.json"))
    parser.add_argument("--csv", type=Path, default=Path("results/profile/bluesim/stages.csv"))
    args = parser.parse_args()
    if (args.baseline_schedule is None) != (args.profile_schedule is None):
        parser.error("baseline-schedule and profile-schedule must be supplied together")
    source_paths = hardware_sources(hw_dir, args.linear_source)
    for name in ("sim/TbSwayPerf.bsv", "Makefile", "perf.mk", "reference/check_perf.py", "reference/check_profile.py"):
        source_paths.append(hw_dir / name)
    evidence_paths = [args.log, args.stderr, args.baseline_log, args.baseline_stderr, args.input, args.expected]
    if args.baseline_schedule is not None:
        evidence_paths.extend((args.baseline_schedule, args.profile_schedule))
    protected = {path.resolve() for path in evidence_paths + source_paths}
    if args.output.resolve() in protected or args.csv.resolve() in protected or args.output.resolve() == args.csv.resolve():
        parser.error("output and csv must be distinct and must not overwrite sources, logs, or fixtures")
    # Failed reruns cannot leave stale successful artifacts behind.
    args.output.unlink(missing_ok=True)
    args.csv.unlink(missing_ok=True)
    try:
        inputs = read_hex(args.input)
        if len(inputs) % INPUT_WORDS:
            raise ValueError("Input fixture must contain complete 320-byte frames")
        result, spans = check_profile(args.log.read_text(), args.stderr.read_text(),
                                     args.baseline_log.read_text(), args.baseline_stderr.read_text(),
                                     read_hex(args.expected), len(inputs) // INPUT_WORDS)
        if args.backend == "iverilog":
            result["evidence"] = "Generated-Verilog/Icarus simulation of the baseline with boundary logging enabled"
        if args.baseline_schedule is not None:
            result["schedule_audit"] = check_schedules(args.baseline_schedule.read_text(), args.profile_schedule.read_text())
        else:
            result["schedule_audit"] = {"status": "not_requested"}
        result["raw_event_log"] = str(args.log)
        result["stage_spans_csv"] = str(args.csv)
        result["evidence_sha256"] = {str(path): sha256(path) for path in evidence_paths}
        result["source_sha256"] = source_manifest(hw_dir, source_paths)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(spans)
        result["stage_spans_csv_sha256"] = sha256(args.csv)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
        args.output.unlink(missing_ok=True)
        args.csv.unlink(missing_ok=True)
        parser.exit(1, f"SWAY_PROFILE_CHECK_FAIL: {error}\n")
    print(f"SWAY_PROFILE_CHECK_PASS frames={result['frames_checked']} boundaries={result['profile_transactions_checked']} outputs={result['scalar_outputs_checked']}")
    print("All baseline performance records are unchanged.")
    for stage, stats in result["stage_spans"].items():
        steady = stats["steady_frames_cycles"]
        print(f"{stage}: mean={steady['mean']:.3f}, min={steady['min']}, max={steady['max']} cycles")


if __name__ == "__main__":
    main()
