#!/usr/bin/env python3
"""Verify unstalled Sway kernel simulations, then summarize transaction cycles."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from statistics import fmean, pstdev

INPUT_WORDS = 320
OUTPUT_WORDS = 57
STREAM_FRAMES = 64
TRIM_FRAMES = 8
DRAIN_CYCLES = 2048


def read_hex(path: Path) -> list[int]:
    values = []
    for word in path.read_text().splitlines():
        word = word.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{2}", word):
            raise ValueError(f"Invalid INT8 fixture word in {path}: {word!r}")
        value = int(word, 16)
        values.append(value - 256 if value >= 128 else value)
    if not values:
        raise ValueError(f"Empty fixture: {path}")
    return values


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_log(text: str, stderr: str, expected: list[int], fixture_frames: int,
              frames: int) -> dict:
    if frames <= 0 or fixture_frames <= 0 or len(expected) != fixture_frames * OUTPUT_WORDS:
        raise ValueError("Invalid frame count or expected fixture length")
    if re.search(r"SWAY_PERF_FAIL|SWAY_FAIL|FATAL:|Error:|\bERROR\b", text + "\n" + stderr):
        raise AssertionError("Simulator reported a failure")

    arity = {"BEGIN": 2, "INPUT_FRAME": 3, "OUTPUT": 3, "FRAME": 3, "PASS": 4}
    events = {name: [] for name in arity}
    seen_pass = False
    for line in text.splitlines():
        if not line.startswith("SWAY_PERF"):
            if line.startswith("SWAY_"):
                raise AssertionError("Stress-test records are not performance evidence")
            continue
        fields = line.split(",")
        name = fields[0].removeprefix("SWAY_PERF_")
        if name not in arity or len(fields) != arity[name] + 1:
            raise AssertionError(f"Unknown or malformed performance record: {line}")
        if seen_pass:
            raise AssertionError("Unexpected record after PASS")
        if not events["BEGIN"] and name != "BEGIN":
            raise AssertionError("Missing initial BEGIN record")
        if not all(re.fullmatch(r"-?\d+", field) for field in fields[1:]):
            raise AssertionError(f"Non-integer performance record: {line}")
        events[name].append(tuple(int(field) for field in fields[1:]))
        seen_pass = name == "PASS"

    if events["BEGIN"] != [(frames, fixture_frames)] or len(events["PASS"]) != 1:
        raise AssertionError("Missing, duplicate, or incorrect BEGIN/PASS")
    passed_frames, passed_outputs, finish_cycle, drain_cycles = events["PASS"][0]
    if passed_frames != frames or passed_outputs != frames * OUTPUT_WORDS:
        raise AssertionError("PASS count disagrees with the requested test")
    for name in ("INPUT_FRAME", "FRAME"):
        if len(events[name]) != frames or [v[0] for v in events[name]] != list(range(frames)):
            raise AssertionError(f"Missing, duplicate, or reordered {name}")
    outputs = events["OUTPUT"]
    if len(outputs) != frames * OUTPUT_WORDS:
        raise AssertionError("Missing or extra scalar outputs")
    previous_cycle = -1
    for index, (actual_index, actual, cycle) in enumerate(outputs):
        if actual_index != index or actual != expected[index % len(expected)]:
            raise AssertionError(f"Incorrect, missing, or reordered scalar {index}")
        if cycle <= previous_cycle:
            raise AssertionError("Output transaction cycles must strictly increase")
        previous_cycle = cycle
    if drain_cycles < DRAIN_CYCLES or finish_cycle - outputs[-1][2] < drain_cycles:
        raise AssertionError("Incomplete trailing-output drain")

    records = []
    for frame in range(frames):
        _, input_first, input_last = events["INPUT_FRAME"][frame]
        _, output_first, output_last = events["FRAME"][frame]
        scalars = outputs[frame * OUTPUT_WORDS:(frame + 1) * OUTPUT_WORDS]
        if input_first < 0 or input_last - input_first < INPUT_WORDS - 1:
            raise AssertionError(f"Invalid input timing for frame {frame}")
        if frame and input_first <= events["INPUT_FRAME"][frame - 1][2]:
            raise AssertionError("Overlapping or reordered input frames")
        if output_first != scalars[0][2] or output_last != scalars[-1][2]:
            raise AssertionError("Frame timestamps disagree with scalar transactions")
        if output_first <= input_last:
            raise AssertionError("Full-frame prediction preceded complete input")
        records.append({
            "frame": frame,
            "fixture_frame": frame % fixture_frames,
            "input_first_cycle": input_first,
            "input_last_cycle": input_last,
            "output_first_cycle": output_first,
            "output_last_cycle": output_last,
            "first_input_to_last_output_cycles": output_last - input_first,
        })
    return {"frames_checked": frames, "scalar_outputs_checked": len(outputs),
            "finish_cycle": finish_cycle, "post_completion_drain_cycles": drain_cycles,
            "frames": records}


def summarize(single: dict, stream: dict, clock_mhz: float) -> dict:
    if len(single["frames"]) != 1 or len(stream["frames"]) != STREAM_FRAMES:
        raise ValueError("Expected one isolated frame and a 64-frame stream")
    if not 0 < clock_mhz < float("inf"):
        raise ValueError("Clock frequency must be finite and positive")
    latency = single["frames"][0]["first_input_to_last_output_cycles"]
    completions = [frame["output_last_cycle"] for frame in stream["frames"]]
    all_intervals = [b - a for a, b in zip(completions, completions[1:])]
    selected = completions[TRIM_FRAMES:-TRIM_FRAMES]
    intervals = [b - a for a, b in zip(selected, selected[1:])]
    mean_cycles = fmean(intervals)
    if min(all_intervals) <= 0:
        raise ValueError("Completion intervals must be positive")
    return {
        "single_frame_latency_cycles": latency,
        "single_frame_latency_us_at_configured_clock": latency / clock_mhz,
        "continuous_frame_intervals_cycles": all_intervals,
        "steady_window": {
            "first_completion_frame": TRIM_FRAMES,
            "last_completion_frame": STREAM_FRAMES - TRIM_FRAMES - 1,
            "interval_count": len(intervals),
            "intervals_cycles": intervals,
            "mean_cycles": mean_cycles,
            "min_cycles": min(intervals),
            "max_cycles": max(intervals),
            "stddev_cycles": pstdev(intervals),
            "constant_interval_observed": len(set(intervals)) == 1,
            "mean_interval_us_at_configured_clock": mean_cycles / clock_mhz,
            "frames_per_second_at_configured_clock": clock_mhz * 1e6 / mean_cycles,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single-log", type=Path, required=True)
    parser.add_argument("--single-stderr", type=Path, required=True)
    parser.add_argument("--stream-log", type=Path, required=True)
    parser.add_argument("--stream-stderr", type=Path, required=True)
    parser.add_argument("--input", type=Path, default=Path("generated/test_input.hex"))
    parser.add_argument("--expected", type=Path, default=Path("generated/test_expected.hex"))
    parser.add_argument("--timing", type=Path, default=Path("results/timing.json"))
    parser.add_argument("--backend", choices=["bluesim", "iverilog"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence_paths = [args.single_log, args.single_stderr, args.stream_log,
                      args.stream_stderr, args.input, args.expected, args.timing]
    if args.output.resolve() in {path.resolve() for path in evidence_paths}:
        parser.error("output must not overwrite a log, fixture, or timing report")
    # A failed rerun must never leave an earlier successful summary behind.
    args.output.unlink(missing_ok=True)
    try:
        inputs = read_hex(args.input)
        expected = read_hex(args.expected)
        if len(inputs) % INPUT_WORDS:
            raise ValueError("Input fixture must contain complete 320-byte frames")
        fixture_frames = len(inputs) // INPUT_WORDS
        timing = json.loads(args.timing.read_text())
        if timing.get("status") != "pass" or timing.get("all_reported_clocks_pass") is not True:
            raise ValueError("Configured-clock conversion requires a passing timing report")
        clock_mhz = float(timing["core_pll_derived_mhz"])
        # Never use achieved_mhz (the timing limit) as the operating clock.
        single = check_log(args.single_log.read_text(), args.single_stderr.read_text(),
                           expected, fixture_frames, 1)
        stream = check_log(args.stream_log.read_text(), args.stream_stderr.read_text(),
                           expected, fixture_frames, STREAM_FRAMES)
        metrics = summarize(single, stream, clock_mhz)
        result = {
            "status": "pass",
            "evidence": "Bluesim simulation" if args.backend == "bluesim" else "generated-Verilog/Icarus simulation",
            "scope": "Kernel transaction cycles; time and throughput derived at the configured clock, not measured board performance",
            "dut": "mkSwayBaseline",
            "source": "One byte attempted each cycle while input remains; only DUT readiness throttles acceptance",
            "sink": "A get is attempted every cycle; no artificial pauses",
            "fixture_order": "Checked-in frame sequence repeated cyclically; single-frame test uses fixture 0",
            "latency_convention": "Last output transaction cycle minus first accepted input cycle, without +1",
            "includes": "320-byte input acceptance, kernel computation, 57-byte output serialization, and internal stalls",
            "excludes": "UART, host processing, reset before first accepted input, and trailing-output drain",
            "clock_mhz": clock_mhz,
            "steady_window_policy": "Use completions 8 through 55 of 64 (47 intervals); report variation rather than assume convergence",
            "metrics": metrics,
            "single": single,
            "stream": stream,
            "evidence_sha256": {str(path): sha256(path) for path in evidence_paths},
            "source_sha256": {},
        }
        hw_dir = Path(__file__).resolve().parents[1]
        for pattern in ("bsv/*.bsv", "rtl/*.v", "generated/*.bsv", "generated/*.vh", "generated/*.hex"):
            for path in sorted(hw_dir.glob(pattern)):
                result["source_sha256"][str(path.relative_to(hw_dir))] = sha256(path)
        for name in ("sim/TbSwayPerf.bsv", "Makefile", "perf.mk", "reference/check_perf.py"):
            result["source_sha256"][name] = sha256(hw_dir / name)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
        parser.exit(1, f"SWAY_PERF_CHECK_FAIL: {error}\n")
    window = metrics["steady_window"]
    print(f"SWAY_PERF_CHECK_PASS single_outputs={single['scalar_outputs_checked']} stream_outputs={stream['scalar_outputs_checked']}")
    print(f"Single-frame latency: {metrics['single_frame_latency_cycles']} cycles")
    print(f"Frame interval: mean={window['mean_cycles']:.3f}, min={window['min_cycles']}, max={window['max_cycles']} cycles")
    print(f"Derived throughput at {clock_mhz:g} MHz: {window['frames_per_second_at_configured_clock']:.3f} frames/s (not board measured)")


if __name__ == "__main__":
    main()
