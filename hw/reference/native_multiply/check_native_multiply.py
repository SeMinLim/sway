#!/usr/bin/env python3
"""Run isolated native DSP wrapper checks; no FPGA synthesis or physical timing claim."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, cwd, log, report):
    report.setdefault("commands", []).append([str(part) for part in command])
    with log.open("w") as output:
        result = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(f"Command exited {result.returncode}: {log}")
    return log.read_text()


def check_log(text, vectors):
    if "FAIL" in text:
        raise AssertionError("Testbench reported FAIL")
    resets = re.findall(r"RESET kind=(\w+) cycle=(\d+) accepted=(\d+) consumed=(\d+)", text)
    if len(resets) != 2 or resets[0][0] != "pipeline" or resets[1][0] != "full":
        raise AssertionError("Both reset cases must execute")
    if tuple(map(int, resets[0][2:])) != (6, 0) or tuple(map(int, resets[1][2:])) != (8, 0):
        raise AssertionError("Reset preconditions mismatch")
    credits = re.findall(r"CREDIT_BOUND cycle=(\d+) accepted=(\d+) consumed=(\d+)", text)
    if not credits or any(int(a) - int(c) != 8 for _, a, c in credits):
        raise AssertionError("The stalled pipeline must reach eight outstanding credits")
    rows = [tuple(map(int, row)) for row in re.findall(
        r"RESULT index=(\d+) value=(-?\d+) cycle=(\d+) issue=(\d+)", text)]
    if len(rows) != 256:
        raise AssertionError(f"Expected 256 post-reset products, got {len(rows)}")
    for index, (actual_index, actual, cycle, issue) in enumerate(rows):
        if actual_index != index or actual != vectors[index][2] or cycle - issue < 4:
            raise AssertionError(f"Product/order/capture mismatch at {index}")
    if rows[0][2] - rows[0][3] != 4:
        raise AssertionError("First product must become consumable after four cycles")
    final = re.search(r"PASS native-multiply products=256 resets=2 final_cycle=(\d+)", text)
    if not final:
        raise AssertionError("Missing completion marker")
    return {"products": 256, "resets": resets, "credit_checks": credits,
            "first_product_latency_cycles": 4, "final_cycle": int(final[1]), "rows": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--ivl-dir")
    parser.add_argument("--seed", type=int, default=20260928)
    args = parser.parse_args()
    hw = args.hw_root.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("Output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "seed": args.seed,
              "scope": "Functional fixed-configuration signed18x18 wrapper test; no physical timing claim"}
    try:
        bsc = shutil.which(args.bsc)
        iverilog = shutil.which(args.iverilog)
        vvp = shutil.which(args.vvp)
        if not bsc or not iverilog or not vvp:
            raise FileNotFoundError("bsc, iverilog and vvp are required")
        os.environ["PATH"] = str(Path(bsc).parent) + os.pathsep + os.environ.get("PATH", "")
        runtime = Path(bsc).parent.parent / "lib/Verilog"
        if not runtime.is_dir():
            raise FileNotFoundError(f"BSC runtime not found: {runtime}")
        snapshot = output / "snapshot"
        (snapshot / "bsv").mkdir(parents=True)
        (snapshot / "rtl").mkdir()
        for source, target in ((hw / "bsv/SwayMultiply.bsv", snapshot / "bsv/SwayMultiply.bsv"),
                               (hw / "rtl/sway_mult18x18d.v", snapshot / "rtl/sway_mult18x18d.v"),
                               (HERE / "TbNativeMultiply.bsv", snapshot / "bsv/TbNativeMultiply.bsv"),
                               (HERE / "main.v", snapshot / "main.v")):
            shutil.copy2(source, target)
        report["source_sha256"] = {str(p.relative_to(snapshot)): sha256(p)
                                   for p in snapshot.rglob("*") if p.is_file()}
        limits = [-131072, -131071, -65536, -1, 0, 1, 65535, 131071]
        vectors = [(a, b, a * b) for a in limits for b in limits]
        rng = random.Random(args.seed)
        for _ in range(192):
            a = rng.randrange(-131072, 131072)
            b = rng.randrange(-131072, 131072)
            vectors.append((a, b, a * b))
        (snapshot / "vectors.hex").write_text("".join(
            f"{((a & 0x3ffff) << 54) | ((b & 0x3ffff) << 36) | (value & 0xfffffffff):018x}\n"
            for a, b, value in vectors))
        (output / "vectors.json").write_text(json.dumps(vectors) + "\n")
        report["vectors_sha256"] = sha256(snapshot / "vectors.hex")
        report["tools"] = {"bsc": run([bsc, "-v"], snapshot, output / "bsc-version.log", report),
                           "iverilog": run([iverilog, *(["-B", args.ivl_dir] if args.ivl_dir else []), "-V"],
                                           snapshot, output / "iverilog-version.log", report)}
        report["backends"] = {}
        for backend in ("bluesim", "iverilog", "iverilog_bsim"):
            build = snapshot / backend
            build.mkdir()
            sim = backend == "bluesim"
            flags = ["-sim" if sim else "-verilog"]
            if backend != "iverilog":
                flags += ["-D", "BSIM"]
            common = ["-p", "+:" + str(snapshot / "bsv"), "-bdir", str(build),
                      "-vdir", str(build), "-simdir", str(build), "-info-dir", str(build)]
            compile_log = run([bsc, *flags, *common, "-u", "-show-schedule", "-g", "mkTbNativeMultiply",
                               str(snapshot / "bsv/TbNativeMultiply.bsv")], snapshot,
                              output / f"{backend}-compile.log", report)
            if re.search(r"\((G0015|G0021|G0117)\)", compile_log):
                raise AssertionError(f"Unexpected scheduling warning in {backend}")
            if sim:
                run([bsc, *flags, *common, "-e", "mkTbNativeMultiply", "-o", str(build / "test")],
                    snapshot, output / f"{backend}-link.log", report)
                command = [str(build / "test")]
            else:
                generated = build / "mkTbNativeMultiply.v"
                if "sway_mult18x18d" not in generated.read_text():
                    raise AssertionError("Verilog backend silently bypassed native BVI")
                run([iverilog, *(["-B", args.ivl_dir] if args.ivl_dir else []), "-g2012", "-s", "main",
                     "-y", str(snapshot / "rtl"), "-y", str(runtime), "-I", str(runtime),
                     "-o", str(build / "test.vvp"), str(snapshot / "main.v"), str(generated)],
                    snapshot, output / f"{backend}-link.log", report)
                command = [vvp, str(build / "test.vvp")]
            log = run(command, snapshot, output / f"{backend}-simulation.log", report)
            report["backends"][backend] = check_log(log, vectors)
        expected = report["backends"]["bluesim"]["rows"]
        for backend in ("iverilog", "iverilog_bsim"):
            if report["backends"][backend]["rows"] != expected:
                raise AssertionError(f"Cycle-exact backend trace mismatch: {backend}")
        report["status"] = "pass"
        report["model_scope"] = ("Bluesim and Icarus model only the fixed signed18x18, CE-always-one, "
                                 "three-register configuration. Native FPGA primitive remains unexecuted here.")
    except Exception as error:
        report["status"] = "fail"
        report["error"] = str(error)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
