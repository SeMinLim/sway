#!/usr/bin/env python3
"""Build isolated lane configurations and check frozen INT8 outputs in Bluesim."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from check_sim import check_log, read_hex


ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, cwd, log, env):
    with log.open("w") as output:
        subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                       env=env, check=True)
    text = log.read_text(errors="replace")
    if re.search(r"SWAY_FAIL|FATAL:|Error:|ARITH_FAIL", text):
        raise AssertionError("Failure in " + str(log))


def build(bsc, snapshot, top, result, env, backend, iverilog, vvp, ivl_dir):
    build_dir = snapshot / "build"
    build_dir.mkdir(exist_ok=True)
    common = ["-sim" if backend == "bluesim" else "-verilog",
              "-bdir", str(build_dir), "-simdir", str(build_dir), "-vdir", str(build_dir),
              "-info-dir", str(build_dir), "-p", "+:bsv:generated:sim"]
    source = "sim/" + top[2:] + ".bsv"
    run([bsc, *common, "-u", "-show-schedule", "-g", top, source], snapshot,
        result / "compile.log", env)
    if backend == "bluesim":
        run([bsc, *common, "-e", top, "-o", str(build_dir / top)], snapshot,
            result / "link.log", env)
        run([str(build_dir / top)], snapshot, result / "simulation.log", env)
    else:
        runtime = Path(bsc).parent.parent / "lib/Verilog"
        command = [iverilog]
        if ivl_dir:
            command += ["-B", ivl_dir]
        command += ["-g2012", "-s", "main", "-D", "TOP=" + top,
                    "-y", str(runtime), "-I", str(runtime),
                    "-o", str(build_dir / top), str(runtime / "main.v"), str(build_dir / (top + ".v"))]
        run(command, snapshot, result / "link.log", env)
        run([vvp, str(build_dir / top)], snapshot, result / "simulation.log", env)
    schedule = build_dir / (top + ".sched")
    if schedule.exists():
        shutil.copy2(schedule, result / "schedule.txt")
    warnings = [line for line in (result / "compile.log").read_text().splitlines()
                if "Warning:" in line]
    if any(code in "\n".join(warnings) for code in ("G0021", "G0117")):
        raise AssertionError("Unreachable/starved rule warning in " + str(result))
    return warnings


def check_kernel(log, input_path, expected_path, backend):
    inputs = read_hex(input_path)
    expected = read_hex(expected_path)
    text = log.read_text()
    records = {"SWAY_OUTPUT": [], "SWAY_INPUT_FRAME": [], "SWAY_FRAME": []}
    passed = []
    for line in text.splitlines():
        if line.startswith("SWAY_PASS "):
            match = re.fullmatch(r"SWAY_PASS frames=(\d+) outputs=(\d+) cycles=(\d+) drain_cycles=(\d+)", line)
            if not match:
                raise AssertionError("Invalid kernel completion")
            passed.append(tuple(map(int, match.groups())))
        elif line.startswith("SWAY_"):
            key, *values = line.split(",")
            if key not in records or len(values) != 3:
                raise AssertionError("Unexpected kernel record: " + line)
            records[key].append(tuple(map(int, values)))
    frames = len(inputs) // 320
    assert len(inputs) == frames * 320 and len(expected) == frames * 57
    assert len(passed) == 1
    frame_count, output_count, finish_cycle, drain = passed[0]
    assert (frame_count, output_count) == (frames, len(expected))
    outputs = records["SWAY_OUTPUT"]
    assert [(index, value) for index, value, cycle in outputs] == list(enumerate(expected))
    assert all(a[2] < b[2] for a, b in zip(outputs, outputs[1:]))
    assert drain >= 2048 and finish_cycle - outputs[-1][2] >= drain
    for key in ("SWAY_INPUT_FRAME", "SWAY_FRAME"):
        assert [item[0] for item in records[key]] == list(range(frames))
    timing = []
    repeated = []
    for frame in range(frames):
        _, first_input, last_input = records["SWAY_INPUT_FRAME"][frame]
        _, first_output, last_output = records["SWAY_FRAME"][frame]
        assert first_input + 319 <= last_input < first_output <= last_output
        assert (first_output, last_output) == (outputs[frame * 57][2], outputs[(frame + 1) * 57 - 1][2])
        assert all(b[2] == a[2] + 1 for a, b in zip(outputs[frame * 57:(frame + 1) * 57 - 1],
                                                   outputs[frame * 57 + 1:(frame + 1) * 57]))
        if frame:
            assert first_input > records["SWAY_INPUT_FRAME"][frame - 1][2]
        timing.append({"frame": frame, "input_first_cycle": first_input,
                       "input_last_cycle": last_input, "output_first_cycle": first_output,
                       "output_last_cycle": last_output,
                       "first_input_to_last_output_cycles": last_output - first_input})
        for previous in range(frame):
            if inputs[frame * 320:(frame + 1) * 320] == inputs[previous * 320:(previous + 1) * 320]:
                assert expected[frame * 57:(frame + 1) * 57] == expected[previous * 57:(previous + 1) * 57]
                repeated.append([previous, frame])
                break
    assert repeated
    intervals = [b[1] - a[1] for a, b in zip(records["SWAY_FRAME"], records["SWAY_FRAME"][1:])]
    return {"status": "pass", "evidence": "BSV Bluesim simulation" if backend == "bluesim" else "generated-Verilog simulation (Icarus)",
            "frames_checked": frames, "scalar_outputs_checked": len(expected),
            "input_words": len(inputs), "finish_cycle": finish_cycle,
            "post_completion_drain_cycles": drain,
            "source_bubbles": "none inserted", "sink_stalls": "none inserted",
            "timing_scope": "kernel interface cycles; excludes UART, physical timing and post-completion drain",
            "output_frame_start_intervals_cycles": intervals,
            "repeated_frame_pairs_checked": repeated,
            "input_sha256": file_hash(input_path), "expected_sha256": file_hash(expected_path),
            "log_sha256": file_hash(log), "frames": timing}


def rounded(value, shift):
    if shift <= 0:
        return value << -shift
    quotient, remainder = divmod(abs(value), 1 << shift)
    quotient += int(remainder * 2 > (1 << shift) or
                    (remainder * 2 == (1 << shift) and quotient % 2 == 1))
    return -quotient if value < 0 else quotient


def make_arithmetic(snapshot):
    """Python big integers provide the oracle independently of hardware helpers."""
    groups = []
    for width in (10, 16, 18, 26, 32, 35, 48, 64):
        for shift in sorted(set((-8, -7, -1, 0, 1, 2, 4, 8, width - 1, width))):
            low, high = -(1 << (width - 1)), (1 << (width - 1)) - 1
            values = {low, low + 1, high - 1, high, -1, 0, 1}
            for quotient in (-129, -128, -127, -2, -1, 0, 1, 2, 126, 127, 128):
                base = quotient << max(0, shift)
                offsets = {-1, 0, 1}
                if shift > 0:
                    offsets |= {(1 << (shift - 1)) + offset for offset in (-1, 0, 1)}
                values |= {base + offset for offset in offsets}
            if width == 16 and shift in (-7, 0, 1, 4, 8):
                values = range(low, high + 1)
            groups.append((width, shift, sorted(value for value in values if low <= value <= high)))
    vectors = []
    for group, (width, shift, values) in enumerate(groups):
        for value in values:
            expected = max(-128, min(127, rounded(value, shift)))
            bits = (group << 72) | ((value & ((1 << 64) - 1)) << 8) | (expected & 255)
            vectors.append(f"{bits:020x}\n")
    (snapshot / "generated/arithmetic.hex").write_text("".join(vectors))
    lines = ["package TbSwayArithmetic;", "import RegFile::*;", "import SwayTypes::*;",
             "module mkTbSwayArithmetic(Empty);",
             f'\tRegFile#(Bit#(32), Bit#(80)) cases <- mkRegFileLoad("generated/arithmetic.hex", 0, {len(vectors) - 1});',
             "\tReg#(Bit#(32)) testCnt <- mkReg(0);", "\trule process1;",
             "\t\tlet value = cases.sub(testCnt);", "\t\tBit#(8) group = value[79:72];",
             "\t\tBit#(64) source = value[71:8];", "\t\tInt#(8) expected = unpack(value[7:0]);",
             "\t\tInt#(8) actual = 0;"]
    for width in (10, 16, 18, 26, 32, 35, 48, 64):
        lines.append(f"\t\tInt#({width}) input{width} = unpack(truncate(source));")
    lines.append("\t\tcase ( group )")
    for group, (width, shift, values) in enumerate(groups):
        lines.append(f"\t\t\t{group}: actual = requantN(input{width}, {-shift}, 0);")
    lines.extend(["\t\tendcase", "\t\tif ( actual != expected ) begin",
                  '\t\t\t$display("ARITH_FAIL index=%0d group=%0d expected=%0d actual=%0d", testCnt, group, expected, actual);',
                  "\t\t\t$finish(1);", "\t\tend", f"\t\tif ( testCnt == {len(vectors) - 1} ) begin",
                  f'\t\t\t$display("ARITH_PASS cases={len(vectors)}");',
                  "\t\t\t$finish(0);", "\t\tend", "\t\ttestCnt <= testCnt + 1;",
                  "\tendrule", "endmodule", "endpackage", ""])
    (snapshot / "sim/TbSwayArithmetic.bsv").write_text("\n".join(lines))
    return {"cases": len(vectors), "oracle": "Python arbitrary precision integer ties-to-even rounding and INT8 saturation",
            "exhaustive_int16_right_shifts": [0, 1, 4, 8], "exhaustive_int16_left_shifts": [7],
            "edge_widths": [10, 16, 18, 26, 32, 35, 48, 64],
            "edge_domain": "signed extrema, saturation thresholds, rounding ties, shift n-1 and n",
            "vector_sha256": file_hash(snapshot / "generated/arithmetic.hex")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--backend", choices=("bluesim", "iverilog"), default="bluesim")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--ivl-dir")
    parser.add_argument("--divisors", nargs="+", type=int, choices=(1, 2, 4), default=[1, 2, 4])
    parser.add_argument("--modes", nargs="+", choices=("stress", "kernel"), default=["stress", "kernel"])
    parser.add_argument("--output", type=Path, default=ROOT / "results/baseline/recheck")
    parser.add_argument("--skip-arithmetic", action="store_true")
    args = parser.parse_args()
    bsc = shutil.which(args.bsc)
    if bsc is None:
        raise FileNotFoundError(args.bsc)
    bsc = str(Path(bsc).resolve())
    env = dict(os.environ)
    env["PATH"] = str(Path(bsc).parent) + os.pathsep + env["PATH"]
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    sources = sorted([*ROOT.joinpath("bsv").glob("*.bsv"),
                      *ROOT.joinpath("generated").glob("*.bsv"),
                      *ROOT.joinpath("sim").glob("*.bsv"),
                      ROOT / "generated/test_input.hex", ROOT / "generated/test_expected.hex",
                      ROOT / "reference/check_sim.py", Path(__file__).resolve()])
    result = {"status": "running", "compiler": bsc,
              "source_sha256": {str(path.relative_to(ROOT)): file_hash(path) for path in sources},
              "configurations": []}
    summary = args.output / "validation.json"
    summary.write_text(json.dumps(result, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="sway-refactor-") as temporary:
        package_cache = Path(temporary) / "package_cache"
        package_cache.mkdir()
        for divisor in args.divisors:
            snapshot = Path(temporary) / ("divisor" + str(divisor))
            snapshot.mkdir()
            for folder in ("bsv", "generated", "sim"):
                shutil.copytree(ROOT / folder, snapshot / folder)
            types = snapshot / "bsv/SwayTypes.bsv"
            source, count = re.subn(r"typedef \d+ ParallelismDivisor;",
                                   f"typedef {divisor} ParallelismDivisor;", types.read_text())
            assert count == 1
            types.write_text(source)
            (snapshot / "build").mkdir()
            # These generated packages are independent of the lane typedefs.
            for cached in package_cache.glob("*.bo"):
                shutil.copy2(cached, snapshot / "build" / cached.name)
            if not args.skip_arithmetic:
                arithmetic = args.output / "arithmetic"
                arithmetic.mkdir(exist_ok=True)
                check = make_arithmetic(snapshot)
                check["compiler_warnings"] = build(bsc, snapshot, "mkTbSwayArithmetic", arithmetic, env,
                                                    args.backend, args.iverilog, args.vvp, args.ivl_dir)
                assert f'ARITH_PASS cases={check["cases"]}' in (arithmetic / "simulation.log").read_text()
                check["status"] = "pass"
                result["arithmetic"] = check
                args.skip_arithmetic = True
            for mode in args.modes:
                directory = args.output / f"divisor{divisor}_{mode}"
                directory.mkdir(exist_ok=True)
                top = "mkTbSway" if mode == "stress" else "mkTbSwayKernel"
                warnings = build(bsc, snapshot, top, directory, env, args.backend,
                                 args.iverilog, args.vvp, args.ivl_dir)
                if mode == "stress":
                    check = check_log(directory / "simulation.log", snapshot / "generated/test_input.hex",
                                      snapshot / "generated/test_expected.hex", args.backend)
                else:
                    check = check_kernel(directory / "simulation.log", snapshot / "generated/test_input.hex",
                                         snapshot / "generated/test_expected.hex", args.backend)
                check.update({"parallelism_divisor": divisor, "mode": mode,
                              "compiler_warnings": warnings,
                              "configured_types_sha256": file_hash(types)})
                (directory / "result.json").write_text(json.dumps(check, indent=2) + "\n")
                result["configurations"].append(check)
                summary.write_text(json.dumps(result, indent=2) + "\n")
                print(f"SWAY_REFACTOR_PASS divisor={divisor} mode={mode} outputs={check['scalar_outputs_checked']}", flush=True)
            for package in ("SwayParameters.bo", "GeneratedTestConfig.bo"):
                shutil.copy2(snapshot / "build" / package, package_cache / package)
    assert all(file_hash(ROOT / path) == digest for path, digest in result["source_sha256"].items()), "Sources changed during validation"
    result["status"] = "pass"
    summary.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
