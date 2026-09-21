#!/usr/bin/env python3
"""Synthetic transcript tests for the checker, not DUT performance measurements."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_perf import check_log, read_hex, summarize


def transcript(frames: int, fixture_frames: int = 2) -> tuple[str, list[int]]:
    expected = [(i * 7) % 256 - 128 for i in range(fixture_frames * 57)]
    lines = [f"SWAY_PERF_BEGIN,{frames},{fixture_frames}"]
    for frame in range(frames):
        start = 10 + frame * 1000
        lines.append(f"SWAY_PERF_INPUT_FRAME,{frame},{start},{start + 319}")
        for coordinate in range(57):
            index = frame * 57 + coordinate
            cycle = start + 500 + coordinate
            lines.append(f"SWAY_PERF_OUTPUT,{index},{expected[index % len(expected)]},{cycle}")
        lines.append(f"SWAY_PERF_FRAME,{frame},{start + 500},{start + 556}")
    finish = 10 + (frames - 1) * 1000 + 556 + 2049
    lines.append(f"SWAY_PERF_PASS,{frames},{frames * 57},{finish},2048")
    return "\n".join(lines) + "\n", expected


class PerfCheckerTests(unittest.TestCase):
    def test_single_frame(self):
        text, expected = transcript(1)
        result = check_log(text, "", expected, 2, 1)
        self.assertEqual(result["frames"][0]["first_input_to_last_output_cycles"], 556)
        self.assertEqual(result["scalar_outputs_checked"], 57)

    def test_stream_repeats_fixtures_and_trims(self):
        single_text, expected = transcript(1)
        stream_text, _ = transcript(64)
        single = check_log(single_text, "", expected, 2, 1)
        stream = check_log(stream_text, "", expected, 2, 64)
        result = summarize(single, stream, 100.0)
        window = result["steady_window"]
        self.assertEqual(stream["scalar_outputs_checked"], 3648)
        self.assertEqual(stream["frames"][63]["fixture_frame"], 1)
        self.assertEqual(window["interval_count"], 47)
        self.assertEqual(window["first_completion_frame"], 8)
        self.assertEqual(window["last_completion_frame"], 55)
        self.assertEqual(window["mean_cycles"], 1000)
        self.assertEqual(window["frames_per_second_at_configured_clock"], 100000)
        self.assertTrue(window["constant_interval_observed"])
        self.assertEqual(result["single_frame_latency_us_at_configured_clock"], 5.56)

    def test_drain_not_in_latency(self):
        text, expected = transcript(1)
        original = check_log(text, "", expected, 2, 1)
        changed = check_log(text.replace("2615,2048", "5574,5000"), "", expected, 2, 1)
        self.assertEqual(original["frames"], changed["frames"])

    def test_window_ignores_head_and_tail_and_reports_variation(self):
        text, expected = transcript(1)
        single = check_log(text, "", expected, 2, 1)
        text, _ = transcript(64)
        stream = check_log(text, "", expected, 2, 64)
        stream["frames"][0]["output_last_cycle"] -= 100
        stream["frames"][-1]["output_last_cycle"] += 5000
        self.assertEqual(summarize(single, stream, 100)["steady_window"]["mean_cycles"], 1000)
        stream["frames"][20]["output_last_cycle"] += 20
        window = summarize(single, stream, 100)["steady_window"]
        self.assertEqual((window["min_cycles"], window["max_cycles"]), (980, 1020))
        self.assertFalse(window["constant_interval_observed"])

    def test_wrong_output(self):
        text, expected = transcript(1)
        with self.assertRaises(AssertionError):
            check_log(text.replace("OUTPUT,0,-128,510", "OUTPUT,0,-127,510"), "", expected, 2, 1)

    def test_missing_and_duplicate_output(self):
        text, expected = transcript(1)
        output = "SWAY_PERF_OUTPUT,0,-128,510\n"
        for changed in (text.replace(output, ""), text.replace(output, output + output)):
            with self.subTest(changed=changed[:40]), self.assertRaises(AssertionError):
                check_log(changed, "", expected, 2, 1)

    def test_reordered_output(self):
        text, expected = transcript(1)
        with self.assertRaises(AssertionError):
            check_log(text.replace("OUTPUT,0,-128,510", "OUTPUT,1,-128,510"), "", expected, 2, 1)

    def test_missing_or_incorrect_completion(self):
        text, expected = transcript(1)
        for changed in ("\n".join(text.splitlines()[:-1]), text.replace("PASS,1,57", "PASS,1,56")):
            with self.assertRaises(AssertionError):
                check_log(changed, "", expected, 2, 1)

    def test_malformed_unknown_and_post_pass_records(self):
        text, expected = transcript(1)
        for changed in (text.replace("FRAME,0,510,566", "FRAME,0,510"),
                        text.replace("FRAME,0,510,566", "UNKNOWN,0,510,566"),
                        text + "SWAY_PERF_OUTPUT,57,0,2700\n"):
            with self.assertRaises(AssertionError):
                check_log(changed, "", expected, 2, 1)

    def test_simulator_failure(self):
        text, expected = transcript(1)
        for stderr in ("FATAL: invalid output", "SWAY_PERF_FAIL watchdog", "Error: fixture"):
            with self.assertRaises(AssertionError):
                check_log(text, stderr, expected, 2, 1)

    def test_frame_timestamps(self):
        text, expected = transcript(1)
        for changed in (text.replace("FRAME,0,510,566", "FRAME,0,510,565"),
                        text.replace("INPUT_FRAME,0,10,329", "INPUT_FRAME,0,10,510"),
                        text.replace("INPUT_FRAME,0,10,329", "INPUT_FRAME,0,10,328")):
            with self.assertRaises(AssertionError):
                check_log(changed, "", expected, 2, 1)

    def test_overlapping_input_frames(self):
        text, expected = transcript(2)
        with self.assertRaises(AssertionError):
            check_log(text.replace("INPUT_FRAME,1,1010,1329", "INPUT_FRAME,1,100,1329"), "", expected, 2, 2)

    def test_output_cycle_and_drain(self):
        text, expected = transcript(1)
        for changed in (text.replace("OUTPUT,1,-121,511", "OUTPUT,1,-121,510"),
                        text.replace("2615,2048", "1000,2048"),
                        text.replace("2615,2048", "2615,20")):
            with self.assertRaises(AssertionError):
                check_log(changed, "", expected, 2, 1)

    def test_reject_stress_log_and_wrong_header(self):
        text, expected = transcript(1)
        for changed in (text.replace("BEGIN,1,2", "BEGIN,1,14"),
                        text.replace("SWAY_PERF_BEGIN,1,2\n", ""),
                        "SWAY_OUTPUT,0,0,100\n" + text):
            with self.assertRaises(AssertionError):
                check_log(changed, "", expected, 2, 1)

    def test_invalid_clock(self):
        text, expected = transcript(1)
        single = check_log(text, "", expected, 2, 1)
        text, _ = transcript(64)
        stream = check_log(text, "", expected, 2, 64)
        for clock in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                summarize(single, stream, clock)

    def test_hex_signed_and_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.hex"
            path.write_text("00\n7f\n80\nff\n")
            self.assertEqual(read_hex(path), [0, 127, -128, -1])
            for content in ("", "100\n", "zz\n"):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    read_hex(path)

    def test_cli_uses_configured_clock_not_timing_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reference").mkdir()
            (root / "sim").mkdir()
            script = root / "reference/check_perf.py"
            shutil.copyfile(Path(__file__).with_name("check_perf.py"), script)
            # Synthetic-only source placeholders exercise report provenance.
            for name in ("Makefile", "perf.mk", "sim/TbSwayPerf.bsv"):
                (root / name).write_text("synthetic checker test\n")
            (root / "input.hex").write_text("00\n" * (2 * 320))
            single, expected = transcript(1)
            stream, _ = transcript(64)
            (root / "expected.hex").write_text("".join(f"{value & 255:02x}\n" for value in expected))
            (root / "single.log").write_text(single)
            (root / "stream.log").write_text(stream)
            (root / "stderr.log").write_text("")
            (root / "timing.json").write_text(json.dumps({
                "status": "pass", "all_reported_clocks_pass": True,
                "core_pll_derived_mhz": 100, "achieved_mhz": 999,
            }))
            command = [sys.executable, str(script),
                       "--single-log", str(root / "single.log"),
                       "--stream-log", str(root / "stream.log"),
                       "--single-stderr", str(root / "stderr.log"),
                       "--stream-stderr", str(root / "stderr.log"),
                       "--input", str(root / "input.hex"), "--expected", str(root / "expected.hex"),
                       "--timing", str(root / "timing.json"), "--backend", "bluesim",
                       "--output", str(root / "summary.json")]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "summary.json").read_text())
            self.assertEqual(report["clock_mhz"], 100)
            self.assertEqual(report["metrics"]["steady_window"]["frames_per_second_at_configured_clock"], 100000)
            self.assertIn("sim/TbSwayPerf.bsv", report["source_sha256"])
            self.assertIn("not measured board performance", report["scope"])
            (root / "timing.json").write_text('{"status": "fail"}')
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "summary.json").exists())

    def test_failed_cli_removes_stale_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "summary.json"
            output.write_text(json.dumps({"status": "pass"}))
            command = [sys.executable, str(Path(__file__).with_name("check_perf.py")),
                       "--single-log", str(root / "missing.log"), "--single-stderr", str(root / "missing.err"),
                       "--stream-log", str(root / "missing2.log"), "--stream-stderr", str(root / "missing2.err"),
                       "--input", str(root / "missing.hex"), "--backend", "bluesim", "--output", str(output)]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn("SWAY_PERF_CHECK_FAIL", result.stderr)


if __name__ == "__main__":
    unittest.main()
