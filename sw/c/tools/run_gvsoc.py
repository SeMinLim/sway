#!/usr/bin/env python3
"""Build, run, and record real GAP8 GVSoC execution, never host timings."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_revision(path):
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def fields(line):
    result = {}
    for key, value in re.findall(r"(\w+)=([^\s]+)", line):
        result[key] = int(value) if value.isdigit() else value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scalar", action="store_true")
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--cluster-hz", type=int, default=170000000)
    parser.add_argument("--fc-hz", type=int, default=250000000)
    parser.add_argument("--max-cores", type=int, choices=(1, 2, 4, 8), default=8)
    parser.add_argument("--opt-level", choices=("O2", "O3"), default="O2")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.trials < 1:
        parser.error("repeats and trials must be positive")
    if not os.environ.get("RULES_DIR") or not os.environ.get("GAP_SDK_HOME"):
        parser.error("source the GAP SDK board configuration first")
    output = args.output or ROOT / "results" / ("gvsoc_scalar.json" if args.scalar else "gvsoc_simd.json")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.with_suffix(".log")
    command = ["make", "-f", "Makefile.gap8", "clean", "all", "run", "platform=gvsoc",
               "PMSIS_OS=pulpos", f"SWAY_SIMD={int(not args.scalar)}",
               f"SWAY_CL_HZ={args.cluster_hz}", f"SWAY_FC_HZ={args.fc_hz}",
               f"SWAY_REPEATS={args.repeats}", f"SWAY_TRIALS={args.trials}",
               f"SWAY_MAX_CORES={args.max_cores}", f"SWAY_OPT={args.opt_level}"]
    with log_path.open("w") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            log.write(line)
            if re.search(r"SWAY_(?:CONFIG|RESULT|DONE|SKIP|ERROR|CHECK) ", line) or "[STEP " in line or "error:" in line:
                print(line, end="", flush=True)
        status = process.wait()
    log_text = log_path.read_text()
    configs = [fields(line) for line in log_text.splitlines() if "SWAY_CONFIG " in line]
    samples = [fields(line) for line in log_text.splitlines() if "SWAY_RESULT " in line]
    done = [fields(line) for line in log_text.splitlines() if "SWAY_DONE " in line]
    if status or len(configs) != 1 or len(done) != 1 or done[0].get("errors") != 0:
        raise RuntimeError(f"GVSoC run did not complete successfully; inspect {log_path}")
    config = configs[0]
    if not samples:
        raise RuntimeError("GVSoC completed without a measured configuration")
    groups = {}
    for sample in samples:
        if sample["errors"] != 0 or not sample["cl_cycles"] or not sample["fc_cycles"]:
            raise RuntimeError("invalid execution or cycle counter")
        groups.setdefault((sample["schedule"], sample["cores"], sample["simd"]), []).append(sample)
    summaries = []
    if done[0].get("configurations") != len(groups):
        raise RuntimeError("completed configuration count disagrees with measurements")
    for (schedule, cores, simd), group in groups.items():
        if len(group) != args.trials or sorted(item["trial"] for item in group) != list(range(args.trials)):
            raise RuntimeError("incomplete trials")
        if any(item["frames"] != 8 * args.repeats for item in group):
            raise RuntimeError("unexpected measured frame count")
        cl = [item["cl_cycles"] / item["frames"] for item in group]
        fc = [item["fc_cycles"] / item["frames"] for item in group]
        reference = [item["reference_us"] / item["frames"] for item in group]
        summaries.append({"schedule": schedule, "cores": cores, "simd": simd,
                          "cluster_cycles_per_frame_median": statistics.median(cl),
                          "cluster_cycles_per_frame_min": min(cl),
                          "cluster_cycles_per_frame_max": max(cl),
                          "cluster_frames_per_second": config["cl_hz"] / statistics.median(cl),
                          "dispatch_frames_per_second": config["fc_hz"] / statistics.median(fc),
                          "dispatch_us_per_frame": statistics.median(fc) * 1e6 / config["fc_hz"],
                          "reference_timer_frames_per_second": 1e6 / statistics.median(reference)})
    executable = ROOT / "BUILD" / "GAP8_V3" / "GCC_RISCV" / "sway_gap8"
    binaries = list((ROOT / "BUILD").rglob("sway_gap8"))
    if not executable.is_file() and len(binaries) == 1:
        executable = binaries[0]
    sources = ["sway.c", "sway.h", "sway_math.h", "gap8_main.c", "Makefile.gap8",
               "generated/sway_model.c", "generated/sway_model.h",
               "generated/sway_fixtures.c", "generated/sway_fixtures.h"]
    report = {
        "method": "GAP8 GVSoC timing simulation; not physical-board measurement",
        "input": "Eight repository real MARS fixtures, repeated; fixed INT8 input through 57 INT8 coordinates",
        "timing": "Cluster elapsed cycles include team fork, L2 input/output access, full inference and synchronization. FC elapsed cycles also include task dispatch. Model initialization, one 14-fixture correctness/warm-up pass, validation and printing are outside timing.",
        "sdk_revision": git_revision(os.environ["GAP_SDK_HOME"]),
        "sdk_patch": subprocess.check_output(["git", "-C", os.environ["GAP_SDK_HOME"], "diff"], text=True),
        "toolchain_revision": git_revision(os.environ["GAP_RISCV_GCC_TOOLCHAIN"]),
        "compiler": subprocess.check_output(["riscv32-unknown-elf-gcc", "--version"], text=True).splitlines()[0],
        "optimization_level": args.opt_level,
        "command": command,
        "config": config,
        "clock_interpretation": "cl_hz/fc_hz are programmed FLL rates, not pi_freq_get requests. reference_us is elapsed virtual reference-clock time, quantized at 1/32768 second, independently checking the cycle conversion.",
        "samples": samples,
        "summary": summaries,
        "skipped": [fields(line) for line in log_text.splitlines() if "SWAY_SKIP " in line],
        "source_sha256": {name: sha256(ROOT / name) for name in sources},
        "executable_sha256": sha256(executable) if executable.is_file() else None,
        "log_sha256": sha256(log_path),
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
