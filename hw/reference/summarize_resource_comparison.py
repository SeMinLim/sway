#!/usr/bin/env python3
"""Compare measured kernel throughput and post-route resources for fixed variants."""

import argparse
import hashlib
import json
import re
from pathlib import Path


HARDWARE_PREFIXES = ("bsv/", "rtl/", "generated/", "sim/TbSwayPerf.bsv")
REQUIRED_SOURCES = {
    "bsv/SwayBaseline.bsv", "bsv/SwayLinear.bsv", "generated/SwayParameters.bsv",
    "bsv/SwayClock.bsv", "bsv/SwayPll.bsv", "rtl/SwayPll60.v",
    "generated/test_input.hex", "generated/test_expected.hex", "sim/TbSwayPerf.bsv",
}


def read_pass(path):
    value = json.loads(path.read_text())
    if value.get("status") != "pass":
        raise ValueError(f"Missing passing evidence: {path}")
    return value


def source_hashes(value):
    """Accept the flat physical manifest and the nested simulation/build form."""
    entries = value.get("source_sha256", value)
    result = {}
    for key, digest in entries.items():
        key = key.removeprefix("hw/")
        if not key.startswith(HARDWARE_PREFIXES):
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid source hash: {key}")
        if key in result and result[key] != digest:
            raise ValueError(f"Conflicting normalized source hashes: {key}")
        result[key] = digest
    if not REQUIRED_SOURCES <= result.keys():
        raise ValueError("Incomplete hardware/fixture source manifest")
    return result


def match_hashes(label, *digests):
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for value in digests) or len(set(digests)) != 1:
        raise ValueError(f"Missing or mismatched evidence hashes: {label}")


def verify_configuration(value, name, core_mhz=60):
    expected = {"INPUT_PROJECTION_VARIANT": "B2", "RESOURCE_VARIANT": name,
                "CORE_MHZ": str(core_mhz), "CRITICAL_CONTROL_REPAIR": "0"}
    configuration = value.get("configuration", {})
    for key, wanted in expected.items():
        if str(configuration.get(key)) != wanted:
            raise ValueError(f"Unexpected {name} configuration: {key}")


def summarize(root, core_mhz=60, physical_directory="physical"):
    if core_mhz not in (60, 80):
        raise ValueError("Expected an explicitly selected 60 or 80 MHz comparison")
    rows = {}
    sources = {}
    evidence = {}
    resource_keys = None
    for name in ("buffered", "dedicated", "shared"):
        directory = root / name
        perf_directory = directory / "perf"
        physical_path = directory / physical_directory
        paths = {
            "perf": perf_directory / "summary.json",
            "timing": physical_path / "timing.json",
            "physical": physical_path / "physical_result.json",
            "native": physical_path / "native_rom.json",
            "control": physical_path / "control_replication_audit.json",
            "sources": physical_path / "source_hashes.json",
            "configuration": directory / "manifest.json",
        }
        reports = {key: read_pass(path) for key, path in paths.items()
                   if key not in ("sources", "configuration")}
        perf, timing, physical = (reports[key] for key in ("perf", "timing", "physical"))
        native, control = (reports[key] for key in ("native", "control"))
        if timing.get("all_reported_clocks_pass") is not True:
            raise ValueError(f"Not every clock passed: {name}")
        match_hashes(f"{name} raw netlist", native.get("netlist_sha256"),
                     control.get("input_sha256"), physical.get("netlist_sha256"))
        match_hashes(f"{name} replicated netlist", control.get("output_sha256"),
                     physical.get("physical_netlist_sha256"))
        for timing_key, physical_key in (("report_sha256", "nextpnr_report_sha256"),
                                         ("log_sha256", "nextpnr_log_sha256"),
                                         ("bitstream_sha256", "bitstream_sha256")):
            match_hashes(f"{name} {timing_key}", timing.get(timing_key), physical.get(physical_key))

        sources[name] = source_hashes(perf)
        physical_sources = source_hashes(json.loads(paths["sources"].read_text()))
        for key, digest in sources[name].items():
            if physical_sources.get(key) != digest:
                raise ValueError(f"Simulation/physical source mismatch: {name}/{key}")
        if name != "buffered":
            required = {"bsv/SwayDelta.bsv", "bsv/SwayFoldedLinear.bsv"}
            if not required <= sources[name].keys():
                raise ValueError(f"Missing resource-comparison engines: {name}")
        if not paths["configuration"].is_file() or (directory / "source_manifest.json").exists():
            raise ValueError(f"Missing or ambiguous build configuration manifest: {name}")
        manifest = json.loads(paths["configuration"].read_text())
        verify_configuration(manifest, name, core_mhz)
        build_sources = source_hashes(manifest)
        for key, digest in sources[name].items():
            if build_sources.get(key) != digest:
                raise ValueError(f"Configured-build source mismatch: {name}/{key}")

        stress_directory = directory / "stress"
        paths["stress_bluesim"] = stress_directory / "bluesim.json"
        paths["stress_verilator"] = stress_directory / "verilator.json"
        stress_reports = [read_pass(paths[key]) for key in ("stress_bluesim", "stress_verilator")]
        for stress in stress_reports:
            if stress.get("frames_checked") != 14 or stress.get("scalar_outputs_checked") != 798:
                raise ValueError(f"Incomplete stress output checks: {name}")
            match_hashes(f"{name} stress input", stress.get("input_sha256"),
                         sources[name]["generated/test_input.hex"])
            match_hashes(f"{name} stress reference", stress.get("expected_sha256"),
                         sources[name]["generated/test_expected.hex"])
        match_hashes(f"{name} stress backend transactions",
                     *(report.get("sway_records_sha256") for report in stress_reports))

        intervals = perf["metrics"]["continuous_frame_intervals_cycles"]
        if len(intervals) != 63 or len(set(intervals)) != 1 or intervals[0] <= 0:
            raise ValueError(f"Expected 63 equal positive completion intervals: {name}")
        if perf["single"]["scalar_outputs_checked"] != 57 or perf["stream"]["scalar_outputs_checked"] != 3648:
            raise ValueError(f"Incomplete output checks: {name}")
        clock = timing["core_pll_derived_mhz"]
        if clock != core_mhz:
            raise ValueError(f"Expected common validated {core_mhz} MHz clock: {name}")
        resources = {key: value["used"] for key, value in physical["packed_resources"].items()}
        if not resources or any(not isinstance(value, int) or value < 0 for value in resources.values()):
            raise ValueError(f"Invalid packed resource counts: {name}")
        if resource_keys is not None and resources.keys() != resource_keys:
            raise ValueError(f"Packed resource keys differ: {name}")
        resource_keys = resources.keys()
        rows[name] = {
            "cycles_per_frame": intervals[0],
            "single_frame_latency_cycles": perf["metrics"]["single_frame_latency_cycles"],
            "validated_clock_mhz": clock,
            "derived_frames_per_second": clock * 1e6 / intervals[0],
            "packed_resources": resources,
            "raw_yosys_cells": physical["raw_yosys_cells"],
            "scalar_outputs_checked": 3705,
            "stress_outputs_checked_per_backend": 798,
        }
        for path in paths.values():
            evidence[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()

    # Configurations were checked separately: identical source text alone does
    # not distinguish the macro-selected dedicated and shared implementations.
    if sources["dedicated"] != sources["shared"]:
        raise ValueError("Dedicated/shared hardware or fixtures differ beyond configuration")
    baseline = rows["dedicated"]
    candidate = rows["shared"]
    equal_throughput = (baseline["cycles_per_frame"] == candidate["cycles_per_frame"] and
                        baseline["validated_clock_mhz"] == candidate["validated_clock_mhz"])
    if not equal_throughput:
        raise ValueError("Dedicated/shared validated kernel throughput differs")
    savings = {}
    for key, count in baseline["packed_resources"].items():
        other = candidate["packed_resources"][key]
        savings[key] = {"fewer_cells": count - other,
                        "reduction_percent": 100 * (count - other) / count if count else None}
    return {
        "status": "pass",
        "scope": "Simulation kernel cycles and completed post-route static timing; no board measurement",
        "variants": rows,
        "sharing_ablation": {
            "baseline": "dedicated",
            "candidate": "shared",
            "same_hardware_source_and_fixtures": True,
            "intended_build_configurations_checked": True,
            "simulation_physical_sources_and_audit_hashes_checked": True,
            "equal_validated_kernel_throughput": equal_throughput,
            "packed_resource_savings": savings,
        },
        "evidence_sha256": evidence,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core-mhz", type=int, choices=(60, 80), default=60)
    parser.add_argument("--physical-directory", default="physical")
    args = parser.parse_args()
    args.output.unlink(missing_ok=True)
    result = summarize(args.results, args.core_mhz, args.physical_directory)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["sharing_ablation"], indent=2))


if __name__ == "__main__":
    main()
