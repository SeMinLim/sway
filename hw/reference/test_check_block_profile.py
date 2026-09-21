#!/usr/bin/env python3
"""Synthetic event evidence tests the checker, not measured DUT performance."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

from check_block_profile import ADDED_RULES, COUNT_NAMES, DETAIL_EVENTS, SAMPLE, STAGES, check_block_profile, check_schedules
from test_check_profile import profile_transcript, schedule


def scale_transcript(text: str) -> str:
    lines = []
    for line in text.splitlines():
        fields = line.split(",")
        if fields[0] == "SWAY_PERF_BEGIN":
            pass
        elif fields[0] in ("SWAY_PERF_INPUT_FRAME", "SWAY_PERF_FRAME", "SWAY_PERF_PASS"):
            fields[-2:] = [str(int(field) * 1000) for field in fields[-2:]]
        else:
            fields[-1] = str(int(fields[-1]) * 1000)
        lines.append(",".join(fields))
    return "\n".join(lines) + "\n"


def cycle_of(line: str) -> int:
    fields = line.split(",")
    if fields[0] == "SWAY_PERF_BEGIN":
        return -1
    if fields[0] == "SWAY_LINEAR_EVENT":
        return int(fields[4])
    if fields[0] == "SWAY_LINEAR_COUNT":
        return int(fields[3])
    if fields[0] == "SWAY_PERF_PASS":
        return int(fields[-2])
    return int(fields[-1])


def chronological(text: str) -> str:
    return "\n".join(sorted(text.splitlines(), key=cycle_of)) + "\n"


def synthetic_block_transcript(group_overlap=False) -> tuple[str, str, str, list[int]]:
    profile, perf, expected = profile_transcript()
    profile, perf = scale_transcript(profile), scale_transcript(perf)
    outer = {}
    for line in profile.splitlines():
        fields = line.split(",")
        if fields[0] == "SWAY_PROFILE" and fields[1] in ("block0_in", "block1_in"):
            outer[(fields[1], int(fields[2]) * 16 + int(fields[3]))] = int(fields[-1])
    lines = profile.splitlines()
    for ordinal in range(1024):
        begin = outer[("block0_in", ordinal)]
        end = outer[("block1_in", ordinal)]
        put = begin + 3
        get = put + 967
        frame, token = divmod(ordinal, 16)
        cycles = (begin + 1, begin + 2, put, get, get + 1, get + 2, get + 3,
                  get + 4, get + 5, get + 6, get + 7, get + 8, end - 1)
        for name, cycle in zip(STAGES, cycles):
            lines.append(f"SWAY_BLOCK,{name},{frame},{token},{cycle}")
        for name, cycle in (("put", put), ("capture", put + 1), ("start", put + 2), ("emit", put + 966), ("get", get)):
            lines.append(f"SWAY_LINEAR_EVENT,{name},{ordinal},{token},{cycle},-1,-1")
        for name, count, offset in zip(COUNT_NAMES, (400, 400, 400, 400, 400, 380, 20, 20, 40),
                                       (955, 956, 958, 959, 960, 959, 961, 915, 948)):
            if group_overlap:
                offset -= 19 * 6
            lines.append(f"SWAY_LINEAR_COUNT,{name},{ordinal},{put + offset},{(ordinal + 1) * count}")
        if ordinal != SAMPLE:
            continue
        for group in range(20):
            first = put + 5 + group * (42 if group_overlap else 48)
            def add(name, cycle, item=-1):
                lines.append(f"SWAY_LINEAR_EVENT,{name},{ordinal},{token},{cycle},{group},{item}")
            add("group", first - 2)
            add("chunk", first - 1, 0)
            add("chunk", first + 31, 1)
            for item in range(20):
                read = first + 2 * item
                for name, offset in (("read", 0), ("dispatch", 1), ("response", 3), ("multiply", 4), ("combine", 5)):
                    add(name, read + offset, item)
                add("last" if item == 19 else "accumulate", read + 6, item)
            last = first + 44
            for name, offset in (("restart", 1), ("bias", 1), ("add", 2), ("round", 3), ("collect", 4)):
                if name == "restart" and group_overlap:
                    continue
                add(name, last + offset)
    return chronological("\n".join(lines)), profile, perf, expected


def block_schedule() -> str:
    text = schedule(True)
    marker = "Logical execution order:"
    rules = "".join(f"Rule: {name}\nPredicate: True\nBlocking rules: (none)\n \n" for name in sorted(ADDED_RULES))
    text = text.replace(marker, rules + marker)
    text = text.replace("original_a, original_b, test_dut_profileTick\n", "original_a, original_b, test_dut_profileTick, " + ", ".join(sorted(ADDED_RULES)) + "\n")
    return text


class BlockCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text, cls.baseline, cls.perf, cls.expected = synthetic_block_transcript()

    def check(self, text=None, stderr="", baseline=None):
        return check_block_profile(self.text if text is None else text, stderr,
                                   self.baseline if baseline is None else baseline, "", self.perf, "", self.expected, 2)

    def first(self, prefix):
        return next(line for line in self.text.splitlines() if line.startswith(prefix))

    def test_valid_disjoint_partition_and_unhidden_starvation(self):
        result, spans, sample = self.check()
        self.assertEqual(result["scalar_outputs_checked"], 3648)
        self.assertEqual(result["block_transactions_checked"], 13312)
        self.assertEqual(result["linear_token_events_checked"], 5120)
        self.assertEqual(len(spans), 14336)
        measured = result["sample_input_projection"]
        self.assertEqual(measured["cycle_partition"], {"before_first_read": 5, "read_issue_cycles": 400,
            "within_group_nonissue_cycles": 380, "between_group_nonissue_cycles": 171, "after_last_read": 5044})
        self.assertEqual(sum(measured["cycle_partition"].values()), 6000)
        self.assertEqual(measured["within_group_read_intervals_cycles"]["histogram"], {"2": 380})
        self.assertEqual(measured["last_accumulate_to_next_group_read_cycles"]["histogram"], {"4": 19})
        self.assertFalse(result["flow_evidence"]["retained_input_refilled_at_first_possible_cycle_all_tokens"])
        self.assertTrue(result["flow_evidence"]["linear_output_consumed_next_cycle_all_tokens"])
        self.assertEqual(sum(row["event"] == "put" for row in sample), 1)
        self.assertEqual(measured["accepted_to_emit_cycles"], 966)
        self.assertEqual(measured["next_put_after_emit_cycles"], 5034)
        self.assertEqual(result["input_projection_lifecycle"]["accepted_to_emit"]["steady_frames_cycles"]["histogram"], {"966": 768})
        residual_slots = result["flow_evidence"]["residual_slots"]
        self.assertEqual(residual_slots["capacity"], 4)
        self.assertEqual(residual_slots["release_to_reuse_pairs"], 1020)
        self.assertFalse(residual_slots["reused_cycle_after_release_all_pairs"])
        self.assertEqual(residual_slots["steady_residence_cycles"]["count"], 768)

    def test_overlap_allows_issue_before_previous_group_completes(self):
        text, baseline, perf, expected = synthetic_block_transcript(group_overlap=True)
        result, _, _ = check_block_profile(text, "", baseline, "", perf, "", expected, 2, group_overlap=True)
        self.assertEqual(result["group_scheduling"], "issue-overlapped")
        self.assertEqual(result["sample_input_projection"]["last_accumulate_to_next_group_read_cycles"]["histogram"], {"-2": 19})
        with self.assertRaisesRegex(AssertionError, "restart firing"):
            check_block_profile(text, "", baseline, "", perf, "", expected, 2)
        with self.assertRaisesRegex(AssertionError, "restart firing"):
            check_block_profile(self.text, "", self.baseline, "", self.perf, "", self.expected, 2, group_overlap=True)

    def test_overlap_rejects_interleaved_accumulations(self):
        text, baseline, perf, expected = synthetic_block_transcript(group_overlap=True)
        line = next(line for line in text.splitlines() if line.startswith("SWAY_LINEAR_EVENT,last,128,") and line.endswith(",0,19"))
        next_accumulate = next(line for line in text.splitlines() if line.startswith("SWAY_LINEAR_EVENT,accumulate,128,") and line.endswith(",1,0"))
        fields = line.split(",")
        fields[4] = next_accumulate.split(",")[4]
        changed = chronological(text.replace(line, ",".join(fields)))
        with self.assertRaisesRegex(AssertionError, "Next group accumulation preceded"):
            check_block_profile(changed, "", baseline, "", perf, "", expected, 2, group_overlap=True)

    def test_missing_duplicate_reordered_stage(self):
        line = self.first("SWAY_BLOCK,inproj_in,0,0,")
        for changed in (self.text.replace(line + "\n", ""), self.text.replace(line, line + "\n" + line),
                        self.text.replace(line, line.replace(",0,0,", ",0,1,"))):
            with self.assertRaises(AssertionError):
                self.check(changed)

    def test_missing_duplicate_mislabeled_detail(self):
        line = self.first("SWAY_LINEAR_EVENT,read,128,")
        fields = line.split(",")
        changed_fields = fields[:-1] + ["1"]
        for changed in (self.text.replace(line + "\n", ""), self.text.replace(line, line + "\n" + line),
                        self.text.replace(line, ",".join(changed_fields))):
            with self.assertRaises(AssertionError):
                self.check(changed)

    def test_unknown_malformed_and_failure_records(self):
        line = self.first("SWAY_BLOCK,norm_in,0,0,")
        for replacement in ("SWAY_BLOCK_FAIL,bad", "SWAY_LINEAR_FAIL,bad", "SWAY_BLOCK,unknown,0,0,1",
                            "SWAY_LINEAR_EVENT,read,128,0,1,0", " " + line, "SWAY_OTHER,1"):
            with self.subTest(replacement=replacement), self.assertRaises(AssertionError):
                self.check(self.text.replace(line, replacement))

    def test_wrong_counter_and_emit_cycle(self):
        line = self.first("SWAY_LINEAR_COUNT,read,128,")
        for field_index in (3, 4):
            fields = line.split(",")
            fields[field_index] = str(int(fields[field_index]) + 1)
            with self.assertRaises(AssertionError):
                self.check(chronological(self.text.replace(line, ",".join(fields))))

    def test_baseline_and_scalar_changes_rejected(self):
        line = self.first("SWAY_PERF_OUTPUT,0,")
        fields = line.split(",")
        fields[2] = str(int(fields[2]) + 1)
        with self.assertRaisesRegex(AssertionError, "baseline SWAY_"):
            self.check(self.text.replace(line, ",".join(fields)))
        with self.assertRaises(AssertionError):
            self.check(baseline=self.baseline.replace("SWAY_PROFILE,block0_in,0,0,332000", "SWAY_PROFILE,block0_in,0,0,332001"))

    def test_noncausal_pipeline_rejected_after_resorting(self):
        line = self.first("SWAY_LINEAR_EVENT,dispatch,128,")
        read = self.first("SWAY_LINEAR_EVENT,read,128,")
        fields = line.split(",")
        fields[4] = read.split(",")[4]
        with self.assertRaisesRegex(AssertionError, "Noncausal read to dispatch"):
            self.check(chronological(self.text.replace(line, ",".join(fields))))

    def test_linear_and_block_crosscheck(self):
        line = self.first("SWAY_BLOCK,inproj_in,0,0,")
        fields = line.split(",")
        fields[-1] = str(int(fields[-1]) + 1)
        with self.assertRaisesRegex(AssertionError, "put disagrees"):
            self.check(chronological(self.text.replace(line, ",".join(fields))))

    def test_stderr_and_records_outside_run(self):
        for stderr in ("SWAY_BLOCK_FAIL", "SWAY_LINEAR_FAIL", "FATAL: failure"):
            with self.assertRaises(AssertionError):
                self.check(stderr=stderr)
        line = self.first("SWAY_BLOCK,norm_in,0,0,")
        with self.assertRaisesRegex(AssertionError, "outside BEGIN/PASS"):
            self.check(line + "\n" + self.text.replace(line + "\n", ""))

    def test_schedule_equivalence_and_rejections(self):
        actual = block_schedule()
        result = check_schedules(schedule(True), actual)
        self.assertEqual(result["original_rules_checked"], 3)
        for changed in (actual.replace("queue.i_notEmpty", "queue.i_notFull"),
                        actual.replace("Blocking rules: original_a", "Blocking rules: (none)"),
                        actual.replace("Logical execution order: original_a, original_b", "Logical execution order: original_b, original_a"),
                        actual.replace(next(iter(ADDED_RULES)), "unexpected_rule")):
            with self.assertRaises(AssertionError):
                check_schedules(schedule(True), changed)

    def test_cli_records_selected_linear_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay = root / "SwayLinear.bsv"
            overlay.write_text("selected internal-profile package\n")
            for name, text in (("profile.log", self.text), ("baseline.log", self.baseline), ("perf.log", self.perf), ("stderr.log", "")):
                (root / name).write_text(text)
            (root / "input.hex").write_text("00\n" * (2 * 320))
            (root / "expected.hex").write_text("".join(f"{value & 255:02x}\n" for value in self.expected))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("check_block_profile.py")),
                "--log", str(root / "profile.log"), "--stderr", str(root / "stderr.log"),
                "--baseline-log", str(root / "baseline.log"), "--baseline-stderr", str(root / "stderr.log"),
                "--perf-log", str(root / "perf.log"), "--perf-stderr", str(root / "stderr.log"),
                "--input", str(root / "input.hex"), "--expected", str(root / "expected.hex"),
                "--linear-source", str(overlay), "--output", str(root / "summary.json"),
                "--stages", str(root / "stages.csv"), "--sample", str(root / "sample.csv")],
                text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((root / "summary.json").read_text())["source_sha256"]
            self.assertEqual(manifest[str(overlay)], hashlib.sha256(overlay.read_bytes()).hexdigest())
            self.assertNotIn("bsv/SwayLinear.bsv", manifest)

    def test_failed_cli_removes_stale_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ("summary.json", "stages.csv", "sample.csv")]
            for path in paths:
                path.write_text("stale pass result")
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("check_block_profile.py")),
                "--input", str(root / "missing.hex"), "--output", str(paths[0]), "--stages", str(paths[1]), "--sample", str(paths[2])],
                text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SWAY_BLOCK_PROFILE_CHECK_FAIL", result.stderr)
            self.assertTrue(all(not path.exists() for path in paths))


if __name__ == "__main__":
    unittest.main()
