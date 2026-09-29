#!/usr/bin/env python3
"""Replay the affine fixture against hash-matching production sources."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--ivl-dir", type=Path)
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parent
    report = json.loads((evidence / "report.json").read_text())
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("--output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    for name, expected in report["source_sha256"].items():
        source = evidence / name
        if not source.is_file():
            source = args.hw / name
        if digest(source) != expected:
            raise RuntimeError("Source hash mismatch: " + str(source))
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in ("input.hex", "expected.hex", "monitor.v"):
        source = evidence / name
        if digest(source) != report["evidence_sha256"][name]:
            raise RuntimeError("Fixture hash mismatch: " + name)
        shutil.copy2(source, output / name)
    (output / "build").mkdir()
    bsc = Path(shutil.which(args.bsc) or args.bsc).resolve()
    runtime = bsc.parent.parent / "lib/Verilog"
    commands = [
        ("compile", [str(bsc), "-verilog", "-bdir", "build", "-vdir", "build",
                     "-info-dir", "build", "-p", "+:bsv:generated", "-u", "-show-schedule",
                     "-g", "mkTbLinearSelect", "bsv/TbLinearSelect.bsv"]),
        ("link", [args.iverilog, *(["-B", str(args.ivl_dir)] if args.ivl_dir else []),
                  "-g2012", "-s", "main", "-s", "monitor", "-D", "TOP=mkTbLinearSelect",
                  "-y", "rtl", "-y", str(runtime), "-I", str(runtime),
                  "-o", "build/test.vvp", str(runtime / "main.v"),
                  "build/mkTbLinearSelect.v", "monitor.v"]),
        ("simulation", [args.vvp, "build/test.vvp"]),
    ]

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))

    for phase, command in commands:
        with (output / (phase + ".log")).open("w") as log:
            subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                           preexec_fn=limits, check=True)
    compile_log = (output / "compile.log").read_text()
    simulation = (output / "simulation.log").read_text()
    schedule = (output / "build/mkTbLinearSelect.sched").read_text()
    assert "Warning:" not in compile_log
    assert "LINEAR_FAIL" not in simulation
    assert "LINEAR_PASS tokens=4 values=80" in simulation
    assert "LINEAR_CONTROL issued=25600 max_consecutive_issue=320 restart_requests=76 restarts=76" in simulation
    assert all(value == "(none)" for value in re.findall(r"Blocking rules: (.*)", schedule))
    print(simulation, end="")


if __name__ == "__main__":
    main()
