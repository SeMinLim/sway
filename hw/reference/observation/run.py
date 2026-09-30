#!/usr/bin/env python3
"""Observe 56 continuous frames without changing the baseline BSV or RTL."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from generate_observer import generate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "reference"))
from check_refactor import check_kernel


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def execute(command, cwd, log, env):
    print("Running " + str(log), flush=True)
    started = time.time()
    with Path(log).open("w") as stream:
        subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
    text = Path(log).read_text(errors="replace")
    if re.search(r"SWAY_FAIL|FATAL:|Error:|ARITH_FAIL", text):
        raise AssertionError("Failure in " + str(log))
    return {"command": command, "seconds": time.time() - started,
            "log_sha256": digest(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--verilator", default="verilator")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Use a fresh output directory: " + str(output))
    output.mkdir(parents=True)
    bsc = str(Path(shutil.which(args.bsc) or args.bsc).resolve())
    verilator = str(Path(shutil.which(args.verilator) or args.verilator).resolve())
    env = dict(os.environ)
    env["PATH"] = str(Path(bsc).parent) + os.pathsep + env["PATH"]
    sources = sorted([*ROOT.joinpath("bsv").glob("*.bsv"),
                      *ROOT.joinpath("generated").glob("*.bsv"),
                      *ROOT.joinpath("sim").glob("*.bsv"),
                      *ROOT.joinpath("rtl").glob("*.v"),
                      ROOT / "generated/test_input.hex", ROOT / "generated/test_expected.hex"])
    hashes = {str(path.relative_to(ROOT)): digest(path) for path in sources}
    measurement_sources = [Path(__file__).resolve(), Path(__file__).with_name("generate_observer.py"),
                           Path(__file__).with_name("stages.json"),
                           ROOT / "reference/check_refactor.py", ROOT / "reference/check_sim.py"]
    measurement_hashes = {str(path): digest(path) for path in measurement_sources}
    report = {"status": "running", "source_sha256": hashes, "commands": [],
              "measurement_source_sha256": measurement_hashes,
              "tool_versions": {"bsc": subprocess.check_output([bsc, "-v"], text=True),
                                "verilator": subprocess.check_output([verilator, "--version"], text=True)},
              "scope": "Generated-Verilog kernel simulation; not a shared hardware implementation",
              "input_repetitions": 4, "frames": 56, "parallelism_divisor": 4}
    report_path = output / "validation.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    snapshot = output / "snapshot"
    snapshot.mkdir()
    for folder in ("bsv", "generated", "sim", "rtl"):
        shutil.copytree(ROOT / folder, snapshot / folder)
    if "typedef 4 ParallelismDivisor;" not in (snapshot / "bsv/SwayTypes.bsv").read_text():
        raise AssertionError("Expected unchanged divisor-4 baseline")
    for filename, words in (("test_input.hex", 14 * 320), ("test_expected.hex", 14 * 57)):
        path = snapshot / "generated" / filename
        original = path.read_text()
        if len(original.splitlines()) != words:
            raise AssertionError("Unexpected fixture length: " + filename)
        path.write_text(original * 4)
    config = snapshot / "generated/GeneratedTestConfig.bsv"
    text, count = re.subn(r"typedef 14 TestFrameCount;", "typedef 56 TestFrameCount;", config.read_text())
    if count != 1:
        raise AssertionError("Unexpected baseline frame count")
    config.write_text(text)
    build = snapshot / "build"
    build.mkdir()
    command = [bsc, "-verilog", "-bdir", str(build), "-vdir", str(build),
               "-info-dir", str(build), "-p", "+:bsv:generated:sim", "-u", "-show-schedule",
               "-g", "mkTbSwayKernel", "sim/TbSwayKernel.bsv"]
    report["commands"].append(execute(command, snapshot, output / "compile.log", env))
    if any(code in (output / "compile.log").read_text() for code in ("G0021", "G0117")):
        raise AssertionError("Unreachable or starved rule warning")
    runtime = Path(bsc).parent.parent / "lib/Verilog"
    rtl = build / "mkTbSwayKernel.v"
    report["generated_rtl_sha256"] = digest(rtl)
    generate(rtl, output, False)
    generate(rtl, output, True)
    compiled_inputs = [rtl, output / "control_top.v", output / "observed_top.v", output / "observer.v",
                       snapshot / "generated/GeneratedTestConfig.bsv",
                       snapshot / "generated/test_input.hex", snapshot / "generated/test_expected.hex",
                       *runtime.glob("*.v")]
    report["compiled_input_sha256"] = {str(path): digest(path) for path in compiled_inputs}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    for observed in (False, True):
        label = "observed" if observed else "control"
        wrapper, observer = output / (label + "_top.v"), output / "observer.v"
        binary_dir = output / (label + "_obj")
        command = [verilator, "--binary", "--timing", "--top-module", "observation_top",
                   "-Wno-fatal", "-Wno-STMTDLY", "--output-split", "10000",
                   "--output-split-cfuncs", "100", "-j", str(args.jobs),
                   "--Mdir", str(binary_dir), "-o", "simulation", "-y", str(snapshot / "rtl"),
                   "-y", str(runtime), "-I" + str(runtime), str(wrapper), str(rtl)]
        if observed:
            command.append(str(observer))
        report["commands"].append(execute(command, snapshot, output / (label + "-build.log"), env))
        report["commands"].append(execute([str(binary_dir / "simulation")], snapshot,
                                          output / (label + ".log"), env))
        result = check_kernel(output / (label + ".log"), snapshot / "generated/test_input.hex",
                              snapshot / "generated/test_expected.hex", "iverilog")
        result["evidence"] = "generated-Verilog simulation (Verilator)"
        report[label] = result
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    control = [line for line in (output / "control.log").read_text().splitlines() if line.startswith("SWAY_")]
    observed = [line for line in (output / "observed.log").read_text().splitlines() if line.startswith("SWAY_")]
    if control != observed:
        raise AssertionError("Observer changed baseline outputs or cycle timestamps")
    for path, value in hashes.items():
        if digest(ROOT / path) != value:
            raise AssertionError("Source changed during measurement: " + path)
    for path, value in {**measurement_hashes, **report["compiled_input_sha256"]}.items():
        if digest(path) != value:
            raise AssertionError("Measurement input changed during measurement: " + path)
    for folder in ("bsv", "rtl"):
        for path in (ROOT / folder).glob("*"):
            if path.is_file() and path.read_bytes() != (snapshot / folder / path.name).read_bytes():
                raise AssertionError("Snapshot changed baseline hardware: " + str(path))
    finish = report["observed"]["finish_cycle"]
    terminal = (output / "stage_waits.csv").read_text().splitlines()[-1]
    if terminal not in (f"{finish},-1,0,0", f"{finish + 1},-1,0,0"):
        raise AssertionError("Missing observer completion marker")
    report.update({"status": "pass", "observer_noninterference": "all SWAY records identical",
                   "baseline_sources_unchanged": True, "terminal_wait_marker": terminal,
                   "artifact_sha256": {name: digest(output / name) for name in
                       ("control.log", "observed.log", "transactions.csv", "stage_waits.csv",
                        "units.json", "stages.json", "observer.v")}})
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("SWAY_OBSERVATION_PASS frames=56 outputs=3192", flush=True)


if __name__ == "__main__":
    main()
