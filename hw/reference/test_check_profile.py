#!/usr/bin/env python3
"""Synthetic boundary transcripts validate the checker, not DUT performance."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

from check_profile import BOUNDARIES, TOKEN_BOUNDARIES, check_profile, check_schedules
from test_check_perf import transcript


def profile_transcript() -> tuple[str, str, list[int]]:
    baseline, expected = transcript(64)
    timed = []
    for line in baseline.splitlines():
        fields = line.split(",")
        if fields[0] == "SWAY_PERF_BEGIN":
            cycle = -1
        elif fields[0] == "SWAY_PERF_PASS":
            cycle = int(fields[-2])
        else:
            cycle = int(fields[-1])
        timed.append((cycle, line))
    for frame in range(64):
        start = 10 + frame * 1000
        for token in range(16):
            for offset, name in enumerate(TOKEN_BOUNDARIES):
                cycle = start + 320 + 6 * token + offset
                timed.append((cycle, f"SWAY_PROFILE,{name},{frame},{token},{cycle}"))
        for name, offset in (("head_output_in", 450), ("serialize_in", 480)):
            cycle = start + offset
            timed.append((cycle, f"SWAY_PROFILE,{name},{frame},0,{cycle}"))
    timed.sort(key=lambda item: item[0])
    return "\n".join(line for _, line in timed) + "\n", baseline, expected


def schedule(profile: bool = False) -> str:
    text = "=== Generated schedule for mkTbSwayPerf ===\n\nRule schedule\n-------------\n"
    text += "Rule: original_a\nPredicate: True\nBlocking rules: (none)\n \n"
    text += "Rule: original_b\nPredicate: queue.i_notEmpty &&\n\t enable\nBlocking rules: original_a\n \n"
    if profile:
        text += "Rule: test_dut_profileTick\nPredicate: True\nBlocking rules: (none)\n \n"
    order = "original_a, original_b" + (", test_dut_profileTick" if profile else "")
    return text + f"Logical execution order: {order}\n\n============================================\n"


class ProfileCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text, cls.baseline, cls.expected = profile_transcript()

    def check(self, text=None, stderr="", baseline=None, baseline_stderr=""):
        return check_profile(self.text if text is None else text, stderr,
                             self.baseline if baseline is None else baseline,
                             baseline_stderr, self.expected, 2)

    def test_valid_counts_spans_and_cadence(self):
        report, spans = self.check()
        self.assertEqual(report["profile_transactions_checked"], 5248)
        self.assertEqual(report["scalar_outputs_checked"], 3648)
        self.assertEqual(len(spans), 4352)
        self.assertEqual(set(report["boundaries"]), set(BOUNDARIES))
        self.assertEqual(report["stage_spans"]["embedding"]["steady_frames_cycles"]["histogram"], {"1": 768})
        self.assertEqual(report["boundaries"]["head_in"]["steady_within_frame_token_intervals_cycles"]["histogram"], {"6": 720})
        self.assertEqual(report["boundaries"]["head_in"]["steady_between_frame_token_intervals_cycles"]["histogram"], {"910": 47})
        self.assertEqual(report["kernel_output"]["steady_frame_intervals_cycles"]["histogram"], {"1000": 47})
        self.assertIn("backpressure", report["stage_span_interpretation"])

    def test_missing_duplicate_or_reordered_transaction(self):
        line = "SWAY_PROFILE,embedding_in,0,0,331\n"
        for changed in (self.text.replace(line, ""), self.text.replace(line, line + line),
                        self.text.replace(line, line.replace(",0,0,", ",0,1,"))):
            with self.subTest(changed=changed[:50]), self.assertRaises(AssertionError):
                self.check(changed)

    def test_unknown_malformed_or_failure_record(self):
        line = "SWAY_PROFILE,embedding_in,0,0,331"
        for replacement in ("SWAY_PROFILE,unknown,0,0,331", "SWAY_PROFILE,embedding_in,0,331",
                            "SWAY_PROFILE,embedding_in,0,0,-1", "SWAY_PROFILE_FAIL bad",
                            " " + line, "SWAY_PROFILE,embedding_in,0,0,nan"):
            with self.subTest(replacement=replacement), self.assertRaises(AssertionError):
                self.check(self.text.replace(line, replacement))

    def test_records_outside_begin_pass(self):
        line = "SWAY_PROFILE,patch_ready,0,0,330\n"
        for changed in (line + self.text.replace(line, ""), self.text.replace(line, "") + line):
            with self.assertRaises(AssertionError):
                self.check(changed)

    def test_non_monotonic_cycles(self):
        with self.assertRaises(AssertionError):
            self.check(self.text.replace("embedding_in,0,0,331", "embedding_in,0,0,329"))

    def test_cross_boundary_causality(self):
        # Keep temporal log order valid while moving the first embedding transfer
        # before its patch exists, so this specifically tests cross-stage pairing.
        original = "SWAY_PROFILE,embedding_in,0,0,331\n"
        changed = self.text.replace(original, "")
        anchor = "SWAY_PROFILE,patch_ready,0,0,330\n"
        changed = changed.replace(anchor, original.replace(",331", ",329") + anchor)
        with self.assertRaisesRegex(AssertionError, "Output preceded input"):
            self.check(changed)

    def test_wrong_scalar_or_changed_baseline_cycle(self):
        with self.assertRaises(AssertionError):
            self.check(self.text.replace("OUTPUT,0,-128,510", "OUTPUT,0,-127,510"))
        # Changing only baseline input start remains a valid perf transcript, but
        # must fail exact instrumentation equivalence.
        with self.assertRaisesRegex(AssertionError, "changed baseline"):
            self.check(baseline=self.baseline.replace("INPUT_FRAME,0,10,329", "INPUT_FRAME,0,9,329"))

    def test_failure_stderr_in_either_run(self):
        for stderr in ("FATAL: failed", "SWAY_PROFILE_FAIL bad", "ERROR invalid"):
            with self.assertRaises(AssertionError):
                self.check(stderr=stderr)
        with self.assertRaises(AssertionError):
            self.check(baseline_stderr="FATAL: baseline failed")

    def test_unrecognized_sway_record_is_not_filtered(self):
        with self.assertRaises(AssertionError):
            self.check(self.text.replace("SWAY_PERF_BEGIN,64,2", "SWAY_PERF_BEGIN,64,2\nSWAY_OTHER,1"))

    def test_schedule_equivalence(self):
        result = check_schedules(schedule(), schedule(True))
        self.assertEqual(result["original_rules_checked"], 2)
        self.assertTrue(result["original_logical_execution_order_equal"])

    def test_schedule_change_rejected(self):
        original = schedule(True)
        for changed in (original.replace("queue.i_notEmpty", "queue.i_notFull"),
                        original.replace("Blocking rules: original_a", "Blocking rules: (none)"),
                        original.replace("Logical execution order: original_a, original_b", "Logical execution order: original_b, original_a"),
                        original.replace("test_dut_profileTick", "other_rule"),
                        original.replace("Rule: test_dut_profileTick\nPredicate: True", "Rule: test_dut_profileTick\nPredicate: enabled")):
            with self.subTest(changed=changed[-100:]), self.assertRaises(AssertionError):
                check_schedules(schedule(), changed)

    def test_cli_records_selected_linear_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay = root / "SwayLinear.bsv"
            overlay.write_text("selected boundary-profile package\n")
            (root / "profile.log").write_text(self.text)
            (root / "baseline.log").write_text(self.baseline)
            (root / "stderr.log").write_text("")
            (root / "input.hex").write_text("00\n" * (2 * 320))
            (root / "expected.hex").write_text("".join(f"{value & 255:02x}\n" for value in self.expected))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("check_profile.py")),
                "--log", str(root / "profile.log"), "--stderr", str(root / "stderr.log"),
                "--baseline-log", str(root / "baseline.log"), "--baseline-stderr", str(root / "stderr.log"),
                "--input", str(root / "input.hex"), "--expected", str(root / "expected.hex"),
                "--linear-source", str(overlay), "--output", str(root / "summary.json"),
                "--csv", str(root / "stages.csv")], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((root / "summary.json").read_text())["source_sha256"]
            self.assertEqual(manifest[str(overlay)], hashlib.sha256(overlay.read_bytes()).hexdigest())
            self.assertNotIn("bsv/SwayLinear.bsv", manifest)

    def test_failed_cli_removes_stale_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "summary.json"
            table = root / "stages.csv"
            output.write_text('{"status": "pass"}')
            table.write_text("stale result\n")
            command = [sys.executable, str(Path(__file__).with_name("check_profile.py")),
                       "--input", str(root / "missing.hex"), "--output", str(output), "--csv", str(table)]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SWAY_PROFILE_CHECK_FAIL", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(table.exists())


if __name__ == "__main__":
    unittest.main()
