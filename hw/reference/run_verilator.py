#!/usr/bin/env python3
"""Build and check the complete native-ROM generated-Verilog testbench with Verilator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time

from check_sim import check_log


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def executable(value: str) -> str:
    found = shutil.which(value)
    if found is None:
        raise FileNotFoundError(f"Executable not found: {value}")
    return str(Path(found).absolute())


def bsc_runtime(explicit: Path | None) -> Path:
    if explicit is not None:
        runtime = explicit.resolve()
    elif os.environ.get("BLUESPECDIR"):
        runtime = Path(os.environ["BLUESPECDIR"]).resolve() / "Verilog"
    else:
        help_text = subprocess.run(
            [executable("bsc"), "-help"], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=True, timeout=30,
        ).stdout
        match = re.search(r"^Bluespec directory: (.+)$", help_text, re.MULTILINE)
        if match is None:
            raise ValueError("Cannot discover BSC runtime; provide --bsc-runtime")
        runtime = Path(match[1]).resolve() / "Verilog"
    if not (runtime / "main.v").is_file():
        raise FileNotFoundError(f"Missing original BSC simulation entry point: {runtime / 'main.v'}")
    return runtime


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def check_native_rtl(rtl: str, resource_comparison: bool = False) -> list[int]:
    if not re.search(r"\bmodule\s+mkTbSway\s*\(", rtl):
        raise ValueError("Generated RTL does not declare mkTbSway")
    native_banks = [int(bank) for bank in re.findall(
        r"\bSwayCoeffRom\s*#\s*\(\s*\.BANK_ID\(32'd(\d+)\)", rtl
    )]
    expected = [bank for bank in range(44) if not resource_comparison or bank // 4 not in (3, 7)]
    if sorted(native_banks) != expected:
        raise ValueError(f"Expected native coefficient BVI bank IDs {expected}; regenerate with SWAY_ROM_NATIVE "
                         "and the selected architecture")
    fixture_names = re.findall(r'\.file\("([^"\n]+)"\)', rtl)
    if sorted(fixture_names) != ["generated/test_expected.hex", "generated/test_input.hex"]:
        raise ValueError("Unexpected generated-Verilog file dependencies")
    return sorted(native_banks)


def run(args: argparse.Namespace, report_path: Path) -> dict:
    hw = Path(__file__).resolve().parents[1]
    verilator = executable(args.verilator)
    runtime = bsc_runtime(args.bsc_runtime)
    version = subprocess.run(
        [verilator, "--version"], check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=30,
    ).stdout.strip()
    sources = {
        "mkTbSway.v": args.rtl.resolve(),
        "SwayCoeffRom.v": hw / "rtl/SwayCoeffRom.v",
        "SwayCoeffRomInit.vh": hw / "generated/SwayCoeffRomInit.vh",
        "generated/test_input.hex": hw / "generated/test_input.hex",
        "generated/test_expected.hex": hw / "generated/test_expected.hex",
    }
    source_hashes = {name: digest(path) for name, path in sources.items()}
    native_banks = check_native_rtl(sources["mkTbSway.v"].read_text(), args.resource_comparison)

    # Capture original runtime before compiling; later retain the exact files that
    # Verilator reports it consumed. The entry point and clock/reset logic are unmodified.
    runtime_before = {str(path.resolve()): digest(path) for path in runtime.rglob("*") if path.is_file()}
    args.build_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=args.build_dir.resolve()))
    for name, source in sources.items():
        destination = work / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if digest(destination) != source_hashes[name]:
            raise AssertionError(f"Source changed while copying: {source}")

    result_dir = report_path.parent
    build_log = result_dir / "verilator.build.log"
    run_log = result_dir / "verilator.log"
    stderr_log = result_dir / "verilator.stderr.log"
    build_command = [
        verilator, "--binary", "--timing", "--top-module", "main", "-Wno-fatal",
        "-j", str(args.jobs), "-DSWAY_ROM_BEHAVIORAL", "-DTOP=mkTbSway", "-I.",
        "-y", str(runtime), "+libext+.v", str(runtime / "main.v"),
        "mkTbSway.v", "SwayCoeffRom.v",
    ]
    start = time.perf_counter()
    with build_log.open("w") as output:
        build = subprocess.run(build_command, cwd=work, stdout=output, stderr=subprocess.STDOUT,
                               timeout=args.build_timeout)
    build_seconds = time.perf_counter() - start
    if build.returncode != 0:
        raise RuntimeError(f"Verilator build exited {build.returncode}; see {build_log}")

    dependency_file = work / "obj_dir/Vmain__verFiles.dat"
    dependencies = []
    for line in dependency_file.read_text().splitlines():
        if line.startswith("S "):
            dependencies.append((work / shlex.split(line)[-1]).resolve())
    runtime_used = {str(path): runtime_before[str(path)] for path in dependencies
                    if str(path) in runtime_before}
    if str(runtime / "main.v") not in runtime_used or len(runtime_used) < 2:
        raise AssertionError("Verilator did not record the original BSC runtime dependencies")
    if not all((work / name).resolve() in dependencies for name in
               ("mkTbSway.v", "SwayCoeffRom.v", "SwayCoeffRomInit.vh")):
        raise AssertionError("Verilator did not compile all required RTL/ROM sources")

    binary = work / "obj_dir/Vmain"
    run_command = [str(binary)]
    start = time.perf_counter()
    with run_log.open("w") as output, stderr_log.open("w") as errors:
        simulation = subprocess.run(run_command, cwd=work, stdout=output, stderr=errors,
                                    timeout=args.run_timeout)
    runtime_seconds = time.perf_counter() - start
    if simulation.returncode != 0:
        raise RuntimeError(f"Verilator simulation exited {simulation.returncode}; see {run_log} and {stderr_log}")
    for name, source in sources.items():
        if digest(source) != source_hashes[name] or digest(work / name) != source_hashes[name]:
            raise AssertionError(f"Source or fixture changed during verification: {source}")
    for name, expected_hash in runtime_used.items():
        if digest(Path(name)) != expected_hash:
            raise AssertionError(f"BSC runtime changed during verification: {name}")

    report = check_log(run_log, work / "generated/test_input.hex",
                       work / "generated/test_expected.hex", "verilator", stderr_log)
    report.update({
        "backend": "verilator",
        "verilator_version": version,
        "build_seconds": build_seconds,
        "runtime_seconds": runtime_seconds,
        "build_command": build_command,
        "run_command": run_command,
        "working_directory": str(work),
        "source_paths": {name: str(path) for name, path in sources.items()},
        "source_sha256": source_hashes,
        "bsc_runtime_sha256": runtime_used,
        "runner_sha256": digest(Path(__file__)),
        "checker_sha256": digest(Path(__file__).with_name("check_sim.py")),
        "executable_sha256": digest(binary),
        "build_log_sha256": digest(build_log),
        "stderr_sha256": digest(stderr_log),
        "native_coefficient_banks": len(native_banks),
        "native_coefficient_bank_ids": native_banks,
        "resource_comparison": args.resource_comparison,
        "clock_reset_model": "Unmodified BSC Verilog/main.v and runtime with --timing",
        "model_scope": "Generated kernel Verilog with native coefficient BVI behavioral ROM; two-state simulation. "
                       "Icarus four-state verification, FPGA primitive reset tests and physical timing are distinct checks.",
    })
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    hw = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtl", type=Path, default=hw / "iverilog/mkTbSway.v")
    parser.add_argument("--build-dir", type=Path, default=hw / "verilator")
    parser.add_argument("--results-dir", type=Path, default=hw / "results")
    parser.add_argument("--bsc-runtime", type=Path)
    parser.add_argument("--resource-comparison", action="store_true",
                        help="Require the 36 native banks outside delta layers 3 and 7")
    parser.add_argument("--verilator", default="verilator")
    parser.add_argument("--jobs", type=positive, default=4)
    parser.add_argument("--build-timeout", type=positive, default=600)
    parser.add_argument("--run-timeout", type=positive, default=60)
    args = parser.parse_args()
    args.results_dir = args.results_dir.resolve()
    args.results_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.results_dir / "verilator.json"
    report_path.unlink(missing_ok=True)
    # A failed attempt must not leave logs from a previous successful run.
    for suffix in ("log", "stderr.log", "build.log"):
        (args.results_dir / f"verilator.{suffix}").write_text("")
    try:
        report = run(args, report_path)
    except (OSError, ValueError, AssertionError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"SWAY_VERILATOR_FAIL: {error}\n")
    print(f"SWAY_VERILATOR_PASS frames={report['frames_checked']} "
          f"outputs={report['scalar_outputs_checked']} cycles={report['finish_cycle']} "
          f"build_seconds={report['build_seconds']:.3f} run_seconds={report['runtime_seconds']:.3f}")


if __name__ == "__main__":
    main()
