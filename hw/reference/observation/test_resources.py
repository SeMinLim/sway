#!/usr/bin/env python3
"""Adversarial checks for joint arithmetic observation and scheduling."""

import tempfile
import unittest
from pathlib import Path

import analyze
import analyze_resources as resources


def adder():
    return {"id": 0, "name": "first_accumulate", "stage": "first", "kind": "adder",
            "input_width": 24, "output_width": 24, "row_length": 2,
            "expected_per_frame": 2, "receipt": "next_edge_state_readback"}


def quantizer():
    return {"id": 0, "name": "first_requant", "stage": "first", "kind": "requant",
            "input_width": 24, "output_width": 8, "from_exp": -12, "to_exp": -5,
            "rows": 2, "expected_per_frame": 2, "receipt": "next_edge_state_readback"}


def adder_rows():
    return [(0, 0, 0, 0, 3, 3, 0), (1, 0, 0, 3, -1, 2, 1),
            (1, 0, 1, 0, 0, 3, 0), (2, 0, 1, 0, 0, 2, 1),
            (20, -1, 2, 0, 0, 0, 0)]


class ResourceChecks(unittest.TestCase):
    def trace(self, rows, units=None):
        if units is None:
            units = [adder()]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            analyze.write_csv(path, resources.EVENT_HEADER, rows)
            return resources.read_operations(path, units, 20, (0, 20), frames=1)

    def test_ties_even_and_saturation(self):
        cases = [(1, 0), (3, 2), (5, 2), (-1, 0), (-3, -2), (-5, -2),
                 (255, 127), (257, 127), (-255, -128), (-257, -128)]
        for value, expected in cases:
            self.assertEqual(resources.requant(value, -1, 0), expected)
        self.assertEqual(resources.requant(-1, 2, 0), -4)
        self.assertEqual(resources.requant(32, 2, 0), 127)
        self.assertEqual(resources.requant(-32, 2, 0), -128)
        self.assertEqual(resources.requant(-32768, -16, 0), 0)

    def test_consecutive_capture_and_final_sumq_readback(self):
        bursts, stats = self.trace(adder_rows())
        self.assertEqual(bursts, [(0, 1, 0, 2)])
        self.assertEqual(stats[0]["receipt_latency_histogram_cycles"], {1: 2})

    def test_wrong_local_feedback_rejected(self):
        rows = adder_rows()
        rows[1] = (1, 0, 0, 4, -1, 3, 1)
        with self.assertRaisesRegex(ValueError, "feedback"):
            self.trace(rows)

    def test_wrong_final_flag_rejected(self):
        rows = adder_rows()
        rows[0] = (0, 0, 0, 0, 3, 3, 1)
        with self.assertRaisesRegex(ValueError, "termination"):
            self.trace(rows)

    def test_final_row_resets_local_sum(self):
        unit = adder()
        unit["expected_per_frame"] = 4
        rows = adder_rows()[:-1]
        rows.extend([(4, 0, 0, 2, 3, 5, 0), (20, -1, 2, 0, 0, 0, 0)])
        with self.assertRaisesRegex(ValueError, "feedback"):
            self.trace(rows, [unit])

    def test_arithmetic_and_readback_corruption_rejected(self):
        for index, replacement, message in (
                (0, (0, 0, 0, 0, 3, 4, 0), "Arithmetic result mismatch"),
                (2, (1, 0, 1, 0, 0, 4, 0), "Retained result")):
            rows = adder_rows()
            rows[index] = replacement
            with self.assertRaisesRegex(ValueError, message):
                self.trace(rows)

    def test_delayed_register_readback_rejected(self):
        rows = adder_rows()
        rows[2:4] = [(2, 0, 1, 0, 0, 3, 0), (3, 0, 1, 0, 0, 2, 1)]
        with self.assertRaisesRegex(ValueError, "next edge"):
            self.trace(rows)

    def test_fifo_delivery_can_be_later_than_capture(self):
        unit = quantizer()
        unit["receipt"] = "fifo_dequeue"
        rows = [(0, 0, 0, 192, 0, 2, 0), (1, 0, 0, -192, 0, -2, 1),
                (5, 0, 1, 0, 0, 2, 0), (9, 0, 1, 0, 0, -2, 1),
                (20, -1, 2, 0, 0, 0, 0)]
        bursts, stats = self.trace(rows, [unit])
        self.assertEqual(bursts, [(0, 1, 0, 2)])
        self.assertEqual(stats[0]["receipt_latency_histogram_cycles"], {5: 1, 8: 1})
        rows[2] = (5, 0, 1, 0, 0, -2, 1)
        with self.assertRaisesRegex(ValueError, "FIFO ordering"):
            self.trace(rows, [unit])

    def test_requant_row_metadata_order_rejected(self):
        rows = [(0, 0, 0, 192, 0, 2, 1), (20, -1, 2, 0, 0, 0, 0)]
        with self.assertRaisesRegex(ValueError, "row/channel/part"):
            self.trace(rows, [quantizer()])

    def test_missing_operation_and_terminal_rejected(self):
        with self.assertRaisesRegex(ValueError, "terminal marker"):
            self.trace(adder_rows()[:-1])
        with self.assertRaisesRegex(ValueError, "Missing or extra"):
            self.trace([(20, -1, 2, 0, 0, 0, 0)])

    def test_duplicate_or_incomplete_receipt_rejected(self):
        rows = adder_rows()
        rows.insert(3, rows[2])
        with self.assertRaisesRegex(ValueError, "Duplicated"):
            self.trace(rows)
        with self.assertRaisesRegex(ValueError, "outstanding receipts"):
            self.trace(adder_rows()[:3] + adder_rows()[-1:])

    def test_exact_scale_classes_do_not_mix_equal_shifts(self):
        first, second = quantizer(), quantizer()
        second.update({"id": 1, "name": "second_requant", "stage": "second",
                       "from_exp": -11, "to_exp": -4})
        classes, assignments = resources.allocate_classes([(0, 1, 0, 2), (3, 4, 1, 2)],
                                                          [first, second], [0, 1])
        self.assertEqual(len(classes), 2)
        self.assertEqual(sum(item["shared"] for item in classes), 2)
        resources.verify_class_assignments([(0, 1, 0, 2), (3, 4, 1, 2)],
                                           [first, second], classes, assignments)

    def test_cross_stage_reclaim_and_serialized_certificate(self):
        first, second = adder(), adder()
        second.update({"id": 1, "name": "second_accumulate", "stage": "second"})
        units = [first, second]
        bursts = [(0, 1, 0, 2), (3, 4, 1, 2)]
        classes, assignments = resources.allocate_classes(bursts, units, [0, 1])
        self.assertEqual((classes[0]["baseline"], classes[0]["stage_confined"], classes[0]["shared"]),
                         (2, 2, 1))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "certificate.csv"
            analyze.write_csv(path, resources.ASSIGNMENT_HEADER, assignments)
            loaded = list(analyze.numeric_rows(path, resources.ASSIGNMENT_HEADER))
            resources.verify_class_assignments(bursts, units, classes, loaded)
            loaded[1] = (0, 1, 1, 0, 2, 4, 2)
            with self.assertRaisesRegex(ValueError, "changed"):
                resources.verify_class_assignments(bursts, units, classes, loaded)

    def test_synthesis_inventory_must_verify_every_candidate(self):
        unit = adder()
        item = {"unit_id": 0, "name": unit["name"], "stage": unit["stage"], "kind": "adder",
                "compatibility_key": ["adder", 24, 24], "verified_present": True,
                "constant": False, "merged_with": None, "cone_cells": 12,
                "functional_check": {"status": "pass"}}
        document = {"status": "pass", "resources": [item]}
        self.assertEqual(resources.checked_inventory(document, [unit]), ([0], []))
        item["verified_present"] = False
        with self.assertRaisesRegex(ValueError, "Unverified"):
            resources.checked_inventory(document, [unit])
        item["constant"] = True
        self.assertEqual(resources.checked_inventory(document, [unit]), ([], [0]))

    def test_merged_alias_with_distinct_capture_trace_is_rejected(self):
        inventory = {"resources": [{"unit_id": 0, "merged_with": None}, {"unit_id": 1, "merged_with": 0}]}
        stats = [{"captured_requests_sha256": "first"}, {"captured_requests_sha256": "second"}]
        with self.assertRaisesRegex(ValueError, "distinct capture"):
            resources.check_merged_requests(inventory, stats)

    def test_inventory_and_trace_must_share_sources_and_artifacts(self):
        sources = {"bsv/SwayTypes.bsv": "types", "generated/SwayParameters.bsv": "params", "rtl/mult.v": "rtl"}
        artifacts = {"transactions": "transactions.csv", "units": "units.json", "stages": "stages.json",
                     "waits": "stage_waits.csv", "control": "control.log", "observed": "observed.log",
                     "operations": "extra_operations.csv", "extra_units": "extra_units.json"}
        hashes = {key: name + "_hash" for key, name in artifacts.items()}
        hashes.update({"inputhex": "input_hash", "expectedhex": "expected_hash"})
        validation = {"status": "pass", "baseline_sources_unchanged": True, "extra_resources": True,
                      "source_sha256": sources.copy(), "parallelism_divisor": 4,
                      "tool_versions": {"bsc": "2025.07"},
                      "artifact_sha256": {name: hashes[key] for key, name in artifacts.items()},
                      "compiled_input_sha256": {"snapshot/generated/test_input.hex": "input_hash",
                                                "snapshot/generated/test_expected.hex": "expected_hash"}}
        inventory = {"source_sha256": sources.copy(), "parallelism_divisor": 4,
                     "tool_versions": {"bsc": "2025.07"}, "input_sha256": {"units": hashes["extra_units"]}}
        self.assertEqual(resources.check_provenance(inventory, validation, hashes)["status"], "pass")
        inventory["source_sha256"]["bsv/SwayTypes.bsv"] = "changed"
        with self.assertRaisesRegex(ValueError, "sources differ"):
            resources.check_provenance(inventory, validation, hashes)
        inventory["source_sha256"] = sources.copy()
        hashes["operations"] = "different_run"
        with self.assertRaisesRegex(ValueError, "completed joint run"):
            resources.check_provenance(inventory, validation, hashes)


if __name__ == "__main__":
    unittest.main()
