#!/usr/bin/env python3
"""Gate the completed ECP5 route against the actual 100 MHz and 25 MHz clocks.

The report schema and completion markers follow nextpnr 3e53a0bf:
common/kernel/report.cc, common/kernel/command.cc, and common/route/router{1,2}.cc.
Placement timing is not used to decide post-route timing closure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path


CORE_CLOCK = "clocks_coreReset.CLK"
UART_CLOCK = "CLK_clk_25mhz"
NUMBER = r"[0-9]+(?:\.[0-9]+)?"


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a numeric MHz value")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def clock_name(name: str) -> str:
    if name.startswith("$glbnet$"):
        name = name[len("$glbnet$"):]
    if name == UART_CLOCK + "$TRELLIS_IO_IN":
        name = UART_CLOCK
    return name


def event_clock(event: object) -> str | None:
    if not isinstance(event, str):
        return None
    match = re.fullmatch(r"(?:posedge|negedge) (.+)", event)
    return clock_name(match.group(1)) if match else None


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_timing(report_path: Path, log_path: Path, bitstream_path: Path) -> dict:
    report = json.loads(report_path.read_text(), object_pairs_hook=unique_object)
    log = log_path.read_text(errors="replace")
    if not isinstance(report, dict):
        raise ValueError("nextpnr report must be a JSON object")
    if re.search(r"^\s*(?:ERROR|FATAL|Error|Fatal):", log, re.MULTILINE):
        raise AssertionError("nextpnr log contains an error or fatal diagnostic")
    if "--timing-allow-fail" in log or "--no-route" in log or "--pack-only" in log:
        raise AssertionError("Log contains an option that bypasses the required route/timing gate")

    # Router2 prints its elapsed time only after checkRoutedDesign succeeds.
    markers = list(re.finditer(
        rf"^Info:\s+(?:Routing complete\.|Router2 time {NUMBER}s)\s*$", log, re.MULTILINE))
    if not markers:
        raise AssertionError("No completed-routing marker in nextpnr log")
    route_marker = markers[-1]
    post_route = log[route_marker.end():]
    if not re.search(r"^Info:\s+Program finished normally\.\s*$", post_route, re.MULTILINE):
        raise AssertionError("nextpnr did not finish normally after routing")
    if re.search(r"\bFAIL\s+at\b|\bun-?routed\b|\bnot routed\b|Hold/min time violation",
                 post_route, re.IGNORECASE):
        raise AssertionError("Post-route log reports failed timing or incomplete routing")

    derived_clocks = re.findall(
        rf"Derived frequency constraint of ({NUMBER}) MHz for net (\S+)", log)
    core_derived = [float(frequency) for frequency, name in derived_clocks
                    if clock_name(name) == CORE_CLOCK]
    if not core_derived or any(abs(frequency - 100.0) > 1e-6 for frequency in core_derived):
        raise AssertionError("Actual core clock lacks a PLL-derived 100 MHz constraint")

    fmax = report.get("fmax")
    if not isinstance(fmax, dict) or not fmax:
        raise AssertionError("nextpnr report has no clock timing results")
    clocks = {}
    for raw_name, timing in fmax.items():
        if not isinstance(timing, dict):
            raise ValueError(f"Malformed timing entry for {raw_name}")
        name = clock_name(raw_name)
        if name in clocks:
            raise ValueError(f"Ambiguous aliases for clock {name}")
        achieved = positive_number(timing.get("achieved"), f"{raw_name}.achieved")
        constraint = positive_number(timing.get("constraint"), f"{raw_name}.constraint")
        if achieved < constraint:
            raise AssertionError(f"Clock {raw_name} failed timing: {achieved} < {constraint} MHz")
        clocks[name] = {"net": raw_name, "achieved_mhz": achieved, "constraint_mhz": constraint}
    for name, required in ((CORE_CLOCK, 100.0), (UART_CLOCK, 25.0)):
        if name not in clocks:
            raise AssertionError(f"Missing actual clock in report: {name}")
        if abs(clocks[name]["constraint_mhz"] - required) > 1e-6:
            raise AssertionError(f"Clock {name} must be constrained to exactly {required} MHz")

    # Cross-check JSON values with final log records, allowing only %.02f rounding.
    log_clocks = {}
    fmax_pattern = re.compile(
        rf"^Info:\s+Max frequency for clock\s+'([^']+)':\s+({NUMBER}) MHz "
        rf"\((PASS|FAIL) at ({NUMBER}) MHz\)\s*$", re.MULTILINE)
    for match in fmax_pattern.finditer(post_route):
        raw_name, achieved, verdict, constraint = match.groups()
        name = clock_name(raw_name)
        if name in log_clocks:
            raise AssertionError(f"Duplicate post-route timing record for {name}")
        if verdict != "PASS":
            raise AssertionError(f"Post-route timing verdict is not PASS for {name}")
        log_clocks[name] = (float(achieved), float(constraint))
    if set(log_clocks) != set(clocks):
        raise AssertionError("Final log clocks do not match JSON report clocks")
    for name, (achieved, constraint) in log_clocks.items():
        if (abs(achieved - clocks[name]["achieved_mhz"]) > 0.00501 or
                abs(constraint - clocks[name]["constraint_mhz"]) > 0.00501):
            raise AssertionError(f"Final log timing disagrees with JSON report for {name}")

    paths = report.get("critical_paths")
    if not isinstance(paths, list) or not paths:
        raise AssertionError("Missing critical-path timing evidence in JSON report")
    path_clocks = set()
    for path in paths:
        if not isinstance(path, dict):
            raise ValueError("Malformed critical-path entry")
        name = event_clock(path.get("from"))
        if name is not None and name == event_clock(path.get("to")):
            segments = path.get("path")
            if isinstance(segments, list) and segments:
                path_clocks.add(name)
    if not {CORE_CLOCK, UART_CLOCK}.issubset(path_clocks):
        raise AssertionError("Missing synchronous critical paths for core or UART clock")

    if not bitstream_path.is_file() or bitstream_path.stat().st_size == 0:
        raise AssertionError("Packed bitstream is missing or empty")
    return {
        "status": "pass",
        "evidence": "completed nextpnr ECP5 routing and static timing analysis",
        "scope": "Post-route tool timing, not measured board performance",
        "routing_completion_marker": route_marker.group(0).strip(),
        "core_pll_derived_mhz": core_derived[-1],
        "clocks": clocks,
        "all_reported_clocks_pass": True,
        "bitstream_bytes": bitstream_path.stat().st_size,
        "report_sha256": file_hash(report_path),
        "log_sha256": file_hash(log_path),
        "bitstream_sha256": file_hash(bitstream_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.report.resolve(), args.log.resolve(), args.bitstream.resolve()}:
        parser.error("--output must not replace an input artifact")
    args.output.unlink(missing_ok=True)
    try:
        result = check_timing(args.report, args.log, args.bitstream)
    except (OSError, ValueError, AssertionError) as error:
        print(f"SWAY_TIMING_FAIL {error}")
        raise SystemExit(1) from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"SWAY_TIMING_PASS core_mhz={result['clocks'][CORE_CLOCK]['achieved_mhz']:.6f} "
          f"uart_mhz={result['clocks'][UART_CLOCK]['achieved_mhz']:.6f}")


if __name__ == "__main__":
    main()
