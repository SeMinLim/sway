#!/usr/bin/env python3
"""Reset an active kernel, then check a complete restart against frozen INT8 outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]

WRAPPER = """`timescale 1ns/1ps
module main;
  reg CLK = 0;
  reg RST_N = 0;
  always #5 CLK = ~CLK;
  mkTbSwayKernel top(.CLK(CLK), .RST_N(RST_N));

  initial begin
    repeat (5) @(negedge CLK);
    RST_N = 1;
    repeat (40000) @(negedge CLK);
    $display("RESET_TEST_BEFORE sent=%0d received=%0d cycles=%0d", top.sentCnt, top.receivedCnt, top.cycleCnt);
    if (top.sentCnt == 0 || top.sentCnt * 57 <= top.receivedCnt * 320 || top.finishOn !== 0) begin
      $fatal(1, "RESET_TEST_FAIL no in-flight work");
    end
    RST_N = 0;
    repeat (5) @(negedge CLK);
    if (top.sentCnt !== 0 || top.receivedCnt !== 0 || top.cycleCnt !== 0 ||
        top.drainCnt !== 0 || top.finishOn !== 0) begin
      $fatal(1, "RESET_TEST_FAIL testbench counters did not reset");
    end
    $display("RESET_TEST_COUNTERS_CLEAR");
    RST_N = 1;
    $display("RESET_TEST_RESTART");
  end
endmodule
"""


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(root):
    return sorted(set([
        *root.joinpath("bsv").rglob("*.bsv"),
        *root.joinpath("generated").rglob("*.bsv"),
        *root.joinpath("sim").rglob("*.bsv"),
        *root.joinpath("rtl").rglob("*.v"),
        root / "generated/test_input.hex", root / "generated/test_expected.hex",
        root / "reference/check_refactor.py", root / "reference/check_sim.py",
        root / "reference/check_warm_reset.py",
    ]))


def source_hashes(root):
    return {str(path.relative_to(root)): file_hash(path) for path in sources(root)}


def executable(name):
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(name)
    return str(Path(path).resolve())


def run(command, cwd, log, env, report):
    report["commands"].append({"argv": command, "cwd": str(cwd), "log": str(log)})
    with log.open("w") as output:
        completed = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, check=False)
    report["commands"][-1]["returncode"] = completed.returncode
    report["commands"][-1]["log_sha256"] = file_hash(log)
    if completed.returncode:
        raise RuntimeError(f"Command exited {completed.returncode}: {log}")
    text = log.read_text(errors="replace")
    if re.search(r"SWAY_FAIL|RESET_TEST_FAIL|FATAL:|Error:", text):
        raise AssertionError("Failure in " + str(log))
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hw-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--ivl-dir")
    args = parser.parse_args()
    root = args.hw_root.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("--output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    report = {"status": "running", "commands": [], "reset_after_cycles": 40000,
              "reset_hold_cycles": 5, "clock_period_ns": 10,
              "evidence": "generated-Verilog simulation (Icarus), active-low reset during in-flight work"}
    result_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        report["source_sha256"] = source_hashes(root)
        bsc = executable(args.bsc)
        iverilog = executable(args.iverilog)
        vvp = executable(args.vvp)
        env = dict(os.environ)
        env["PATH"] = str(Path(bsc).parent) + os.pathsep + env["PATH"]
        report["tools"] = {"bsc": bsc, "iverilog": iverilog, "vvp": vvp,
                           "ivl_dir": args.ivl_dir}
        for name, command in (("bsc", [bsc, "-v"]),
                              ("iverilog", [iverilog, *(["-B", args.ivl_dir] if args.ivl_dir else []), "-V"]),
                              ("vvp", [vvp, "-V"])):
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       env=env, text=True, check=False)
            report["tools"][name + "_version"] = completed.stdout.strip()
            report["tools"][name + "_version_returncode"] = completed.returncode
        snapshot = output / "snapshot"
        snapshot.mkdir()
        for folder in ("bsv", "generated", "sim", "reference", "rtl"):
            shutil.copytree(root / folder, snapshot / folder)
        if source_hashes(snapshot) != report["source_sha256"]:
            raise AssertionError("Snapshot source hash mismatch")
        build = snapshot / "build"
        build.mkdir()
        wrapper = output / "warm_reset_main.v"
        wrapper.write_text(WRAPPER)
        report["wrapper_sha256"] = file_hash(wrapper)
        common = ["-verilog", "-bdir", str(build), "-simdir", str(build),
                  "-vdir", str(build), "-info-dir", str(build), "-p", "+:bsv:generated:sim"]
        compile_text = run([bsc, *common, "-u", "-show-schedule", "-g", "mkTbSwayKernel",
                            "sim/TbSwayKernel.bsv"], snapshot, output / "compile.log", env, report)
        if re.search(r"G0021|G0117", compile_text):
            raise AssertionError("Unreachable or starved rule warning")
        report["compiler_warnings"] = [line for line in compile_text.splitlines() if "Warning:" in line]
        runtime = Path(bsc).parent.parent / "lib/Verilog"
        command = [iverilog, *(["-B", args.ivl_dir] if args.ivl_dir else []),
                   "-g2012", "-s", "main", "-y", str(snapshot / "rtl"), "-y", str(runtime),
                   "-I", str(runtime), "-o", str(build / "warm_reset.vvp"),
                   str(wrapper), str(build / "mkTbSwayKernel.v")]
        run(command, snapshot, output / "link.log", env, report)
        simulation = run([vvp, str(build / "warm_reset.vvp")], snapshot,
                         output / "simulation.log", env, report)
        match = re.search(r"^RESET_TEST_BEFORE sent=(\d+) received=(\d+) cycles=(\d+)$",
                          simulation, re.MULTILINE)
        if match is None or simulation.count("RESET_TEST_RESTART") != 1:
            raise AssertionError("Missing warm-reset observations")
        sent, received, cycle = map(int, match.groups())
        if sent == 0 or sent * 57 <= received * 320 or cycle != 40000:
            raise AssertionError("Reset did not interrupt in-flight work at cycle 40000")
        if simulation.count("RESET_TEST_COUNTERS_CLEAR") != 1:
            raise AssertionError("Missing counter reset confirmation")
        report["before_reset"] = {"input_words_accepted": sent, "outputs_received": received,
                                  "cycle": cycle, "in_flight_work_confirmed": True}
        post_reset = output / "post_reset.log"
        post_reset.write_text(simulation.split("RESET_TEST_RESTART", 1)[1].lstrip("\r\n"))
        sys.path.insert(0, str(snapshot / "reference"))
        from check_refactor import check_kernel
        checked = check_kernel(post_reset, snapshot / "generated/test_input.hex",
                               snapshot / "generated/test_expected.hex", "iverilog")
        if (checked["frames_checked"], checked["scalar_outputs_checked"]) != (14, 798):
            raise AssertionError("Warm-reset fixtures must cover 14 frames and 798 outputs")
        report["post_reset"] = checked
        schedule = build / "mkTbSwayKernel.sched"
        if schedule.exists():
            shutil.copy2(schedule, output / "schedule.txt")
        report["source_unchanged"] = source_hashes(root) == report["source_sha256"]
        if not report["source_unchanged"]:
            raise AssertionError("Source changed during warm-reset verification")
        report["status"] = "pass"
    except Exception as error:
        report["status"] = "fail"
        report["error"] = str(error)
        raise
    finally:
        result_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
