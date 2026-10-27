#!/usr/bin/env python3
"""Validate serial/parallel C inference against the frozen integer reference."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
CROOT = ROOT / "sw/c"
sys.path.insert(0, str(ROOT / "hw/reference"))
from integer_model import IntegerModel


TRACE_BYTES = 57 + 320 + 640 + 20 + 20 + 2 * 2 * 16 * 40 * 8 * 4


def sha256(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run(command, env=None):
    result = subprocess.run([str(item) for item in command], cwd=CROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(map(str, command)))
    return result.stdout.strip()


def inputs():
    fixture = np.load(ROOT / "hw/generated/test_trace.npz", allow_pickle=False)["input"].astype(np.int8)
    constant = np.repeat(np.arange(-128, 128, dtype=np.int16)[:, None], 320, axis=1).astype(np.int8)
    impulse = np.zeros((640, 320), dtype=np.int8)
    impulse[np.arange(320), np.arange(320)] = 127
    impulse[320 + np.arange(320), np.arange(320)] = -128
    rng = np.random.default_rng(20261027)
    random = rng.integers(-128, 128, (128, 320), dtype=np.int16).astype(np.int8)
    extreme = np.where(rng.integers(0, 2, (64, 320)), 127, -128).astype(np.int8)
    repeated = np.stack([fixture[0].reshape(-1), fixture[9].reshape(-1)] * 8)
    values = np.concatenate((fixture.reshape(-1, 320), constant, impulse, random, extreme, repeated))
    return values.reshape(-1, 8, 8, 5), {
        "frozenFixtures": len(fixture), "constantFrames": len(constant), "singleElementImpulses": len(impulse),
        "randomUniformFrames": len(random), "randomExtremeFrames": len(extreme), "resetSequenceFrames": len(repeated),
        "randomSeed": 20261027,
    }


def reference_file(model, values, path):
    with path.open("wb") as stream:
        for offset in range(0, len(values), 16):
            trace = {}
            output = model.forward_integer(values[offset:offset + 16], trace=trace)
            for index in range(len(output)):
                stream.write(output[index].astype(np.int8).tobytes())
                stream.write(trace["embedding"][index].astype(np.int8).tobytes())
                for block in range(2):
                    stream.write(trace["blocks.%d.residual" % block][index].astype(np.int8).tobytes())
                stream.write(trace["headHidden"][index].astype(np.int8).tobytes())
                stream.write(trace["headActivation"][index].astype(np.int8).tobytes())
                for field in ("currentState", "state"):
                    for block in range(2):
                        stream.write(trace["blocks.%d.%s" % (block, field)][index].astype("<i4").tobytes())
    if path.stat().st_size != len(values) * TRACE_BYTES:
        raise AssertionError("Incorrect reference trace size")


def nearest_even(value, denominator):
    quotient, remainder = divmod(abs(int(value)), int(denominator))
    if 2 * remainder > denominator or (2 * remainder == denominator and quotient & 1):
        quotient += 1
    return -quotient if value < 0 else quotient


def arithmetic_cases(path):
    counts = [0] * 9
    with path.open("wb") as stream:
        def emit(operation, argument, value, expected):
            stream.write(struct.pack("<iiqq", operation, argument, int(value), int(expected)))
            counts[operation] += 1
        small = range(-4096, 4097)
        edges32 = [-(1 << 31), -(1 << 31) + 1, -(1 << 24), -(1 << 23) - 1,
                   -(1 << 23), -(1 << 23) + 1, -129, -128, -127, -1, 0, 1, 126, 127, 128,
                   (1 << 23) - 2, (1 << 23) - 1, 1 << 23, (1 << 31) - 2, (1 << 31) - 1]
        for denominator in [1, 2, 3, 4, 7, 20, 255, 5100, 40800, 81600, (1 << 31) - 1]:
            for value in list(small) + edges32:
                emit(0, denominator, value, nearest_even(value, denominator))
        for shift in range(32):
            boundary = []
            if shift:
                for quotient in [-129, -128, -127, -3, -2, -1, 0, 1, 2, 3, 126, 127, 128]:
                    midpoint = quotient * (1 << shift) + (1 << (shift - 1))
                    boundary.extend([midpoint - 1, midpoint, midpoint + 1])
            for value in list(small) + edges32 + boundary:
                if -(1 << 31) <= value < (1 << 31):
                    emit(1, shift, value, nearest_even(value, 1 << shift))
        edges64 = edges32 + [-(1 << 63), -(1 << 48), -(1 << 40) - 1, -(1 << 35),
                             (1 << 35) - 1, 1 << 40, (1 << 48) - 1, (1 << 63) - 1]
        for shift in range(64):
            for value in edges64:
                emit(2, shift, value, nearest_even(value, 1 << shift))
        for operation, values in [(3, list(small) + edges32), (4, edges64)]:
            for shift in range(-31, 32):
                for value in values:
                    scaled = value * (1 << shift) if shift >= 0 else nearest_even(value, 1 << -shift)
                    emit(operation, shift, value, min(127, max(-128, scaled)))
        for value in list(small) + edges32:
            emit(5, 0, value, min((1 << 23) - 1, max(-(1 << 23), value)))
        for value in list(small) + [x for x in edges32 if -(1 << 23) <= x < (1 << 23)]:
            emit(6, 0, value, value // 128)
        rng = np.random.default_rng(20261027)
        divisors = sorted({20 * span * scale for span in range(1, 256) for scale in (8, 16)} | {2, 3, 7, (1 << 31) - 1})
        for denominator in divisors:
            boundary = []
            for quotient in (0, 1, 2, 127, 128, 255, 1024, 100000):
                middle = quotient * denominator + denominator // 2
                boundary.extend([middle - 1, middle, middle + 1, -middle - 1, -middle, -middle + 1])
            random = rng.integers(-(1 << 31), 1 << 31, 128, dtype=np.int64).tolist()
            for value in edges32 + boundary + random:
                if -(1 << 31) <= value < (1 << 31):
                    emit(7, denominator, value, nearest_even(value, denominator))
        bounded = list(small) + [-(1 << 30) + 1, (1 << 30) - 1, -20497920, 20497920]
        for shift in range(-29, 32):
            for value in bounded:
                scaled = value * (1 << shift) if shift >= 0 else nearest_even(value, 1 << -shift)
                emit(8, shift, value, min(127, max(-128, scaled)))
    return counts


def verify_binary(expected, actual, frames):
    if actual.stat().st_size != frames * TRACE_BYTES:
        raise AssertionError("Incorrect C output/trace size: " + str(actual))
    if sha256(expected) == sha256(actual):
        return
    with expected.open("rb") as left, actual.open("rb") as right:
        for frame in range(frames):
            reference = left.read(TRACE_BYTES)
            observed = right.read(TRACE_BYTES)
            if reference != observed:
                index = next(i for i, (a, b) in enumerate(zip(reference, observed)) if a != b)
                raise AssertionError("Reference mismatch frame=%d recordByte=%d expectedByte=%d actualByte=%d" %
                                     (frame, index, reference[index], observed[index]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    parser.add_argument("--build-dir", type=Path, default=CROOT / "build/validation")
    parser.add_argument("--report", type=Path, default=CROOT / "results/host_validation.json")
    parser.add_argument("--skip-sanitizer", action="store_true")
    args = parser.parse_args()
    args.build_dir.mkdir(parents=True, exist_ok=True)
    run([sys.executable, CROOT / "tools/export_model.py", "--check"])
    model = IntegerModel()
    values, datasets = inputs()
    input_path = args.build_dir / "inputs.bin"
    values.tofile(input_path)
    expected_path = args.build_dir / "expected.bin"
    reference_file(model, values, expected_path)
    common = [args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-DSWAY_TRACE"]
    sources = [CROOT / "sway.c", CROOT / "generated/sway_model.c", CROOT / "tests/validate.c"]
    serial = args.build_dir / "validate_serial"
    parallel = args.build_dir / "validate_parallel"
    frame_parallel = args.build_dir / "validate_frames"
    stream_parallel = args.build_dir / "validate_stream"
    run(common + ["-O3"] + sources + ["-o", serial])
    run(common + ["-O3", "-fopenmp"] + sources + ["-o", parallel])
    run(common + ["-O3", "-fopenmp"] + sources[:-1] + [CROOT / "tests/validate_frames.c", "-o", frame_parallel])
    run(common + ["-O3", "-fopenmp"] + sources[:-1] + [CROOT / "tests/validate_stream.c", "-o", stream_parallel])
    results = []
    for executable, cores in [(serial, 1), (parallel, 1), (parallel, 2), (parallel, 4), (parallel, 8),
                              (frame_parallel, 2), (frame_parallel, 4), (frame_parallel, 8)]:
        output_path = args.build_dir / "actual.bin"
        message = run([executable, input_path, output_path, str(cores)])
        verify_binary(expected_path, output_path, len(values))
        results.append({"configuration": executable.name, "cores": cores, "frames": len(values),
                        "outputAndTraceMatch": True, "log": message})
    stream_expected = bytearray()
    with expected_path.open("rb") as stream:
        for _ in range(len(values)):
            stream_expected.extend(stream.read(57))
            stream.seek(TRACE_BYTES - 57, 1)
    stream_results = []
    for cores in (1, 2, 4, 8):
        output_path = args.build_dir / "stream.bin"
        message = run([stream_parallel, input_path, output_path, str(cores)])
        if output_path.read_bytes() != stream_expected:
            raise AssertionError("Persistent-team output mismatch with %d cores" % cores)
        stream_results.append({"cores": cores, "frames": len(values), "allOutputsMatch": True, "log": message})
    arithmetic_path = args.build_dir / "arithmetic.bin"
    arithmetic_counts = arithmetic_cases(arithmetic_path)
    arithmetic_binary = args.build_dir / "arithmetic"
    run([args.cc, "-std=c11", "-O3", "-Wall", "-Wextra", "-Werror",
         CROOT / "tests/arithmetic.c", "-o", arithmetic_binary])
    arithmetic_log = run([arithmetic_binary, arithmetic_path])
    sanitizer = {"enabled": not args.skip_sanitizer}
    if not args.skip_sanitizer:
        sanitized = args.build_dir / "validate_sanitized"
        flags = ["-O1", "-g", "-fno-omit-frame-pointer", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        run(common + flags + sources + ["-o", sanitized])
        # LeakSanitizer needs /proc thread access, unavailable in some containers.
        # The inference kernel has no dynamic allocations; check bounds and UB.
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="print_stacktrace=1")
        sanitizer["inferenceLog"] = run([sanitized, input_path, args.build_dir / "sanitized.bin", "1"], env=env)
        verify_binary(expected_path, args.build_dir / "sanitized.bin", len(values))
        run([args.cc, "-std=c11"] + flags + [CROOT / "tests/arithmetic.c", "-o", arithmetic_binary])
        sanitizer["arithmeticLog"] = run([arithmetic_binary, arithmetic_path], env=env)
        sanitizer["passed"] = True
        sanitizer["checks"] = ["address", "undefined-behavior"]
        sanitizer["leakDetection"] = False
    relevant = [CROOT / "sway.c", CROOT / "sway.h", CROOT / "sway_math.h",
                CROOT / "generated/sway_model.c", CROOT / "generated/sway_model.h",
                CROOT / "tests/validate.c", CROOT / "tests/validate_frames.c", CROOT / "tests/validate_stream.c",
                CROOT / "tests/arithmetic.c", Path(__file__),
                ROOT / "hw/reference/integer_model.py"]
    report = {
        "schemaVersion": 1, "status": "pass", "kind": "host numerical validation, not GAP8 performance",
        "compiler": run([args.cc, "--version"]).splitlines()[0], "datasets": datasets,
        "framesPerConfiguration": len(values), "outputElementsPerConfiguration": len(values) * 57,
        "traceElementsPerConfiguration": len(values) * (320 + 640 + 20 + 20 + 2 * 2 * 16 * 40 * 8),
        "traceFields": ["embedding", "blocks.0.residual", "blocks.1.residual", "headHidden", "headActivation",
                        "blocks.0.currentState", "blocks.1.currentState", "blocks.0.state", "blocks.1.state"],
        "configurations": results,
        "persistentTeam": {"workspace": "one shared workspace reused for all frames",
                           "externalBarriersBetweenFrames": 0,
                           "traceEnabled": False, "configurations": stream_results},
        "arithmeticCases": dict(zip(["divideEven32", "roundShift32", "roundShift64", "requantize32",
                                      "requantize64", "saturate24", "retainState", "divideEvenReciprocal32",
                                      "requantizeBounded32"], arithmetic_counts)),
        "arithmeticLog": arithmetic_log, "sanitizer": sanitizer,
        "inputSHA256": sha256(input_path), "referenceOutputAndTraceSHA256": sha256(expected_path),
        "sourceSHA256": {str(path.relative_to(ROOT)): sha256(path) for path in relevant},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("All C validation checks passed: " + str(args.report))


if __name__ == "__main__":
    main()
