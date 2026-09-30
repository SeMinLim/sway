#!/usr/bin/env python3
"""Small adversarial traces for the observation certificate checker."""

import tempfile
import unittest
from pathlib import Path

import analyze


class TraceChecks(unittest.TestCase):
    def transactions(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transactions.csv"
            analyze.write_csv(path, ["cycle", "unit_id", "put", "get", "a", "b", "result"], rows)
            return analyze.read_transactions(path, 1, [0], 1, 100, (0, 100))

    def test_signed_extrema_and_fifo(self):
        rows = [(0, 0, 1, 0, -131072, -131072, 0), (1, 0, 1, 0, -131072, 131071, 0),
                (4, 0, 0, 1, 0, 0, 17179869184), (5, 0, 0, 1, 0, 0, -17179738112)]
        bursts, _, _, stats = self.transactions(rows)
        self.assertEqual(bursts, [(0, 5, 0, 2)])
        self.assertEqual(stats["products_checked"], 2)

    def test_corrupt_or_reordered_product_rejected(self):
        with self.assertRaisesRegex(ValueError, "Arithmetic"):
            self.transactions([(0, 0, 1, 0, 2, 3, 0), (4, 0, 0, 1, 0, 0, 7)])

    def test_early_result_rejected(self):
        with self.assertRaisesRegex(ValueError, "before baseline"):
            self.transactions([(0, 0, 1, 0, 2, 3, 0), (3, 0, 0, 1, 0, 0, 6)])

    def test_unmatched_get_rejected(self):
        with self.assertRaisesRegex(ValueError, "without accepted"):
            self.transactions([(4, 0, 0, 1, 0, 0, 6)])

    def test_incomplete_trace_rejected(self):
        with self.assertRaisesRegex(ValueError, "unretired"):
            self.transactions([(0, 0, 1, 0, 2, 3, 0)])

    def test_duplicate_event_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicated"):
            self.transactions([(0, 0, 1, 0, 2, 3, 0), (0, 0, 1, 0, 2, 3, 0)])

    def test_capacity_guard_uses_pre_edge_state(self):
        rows = [(cycle, 0, 1, 0, 2, 3, 0) for cycle in range(8)]
        rows.append((8, 0, 1, 1, 2, 3, 6))
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.transactions(rows)

    def test_same_cycle_put_get_retains_owner(self):
        rows = [(0, 0, 1, 0, 2, 3, 0), (4, 0, 1, 1, 4, 5, 6), (8, 0, 0, 1, 0, 0, 20)]
        bursts, _, _, _ = self.transactions(rows)
        self.assertEqual(bursts, [(0, 8, 0, 2)])

    def test_partial_csv_row_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.csv"
            path.write_text("cycle,unit_id,put,get,a,b,result\n0,0,1,0")
            with self.assertRaisesRegex(ValueError, "Incomplete CSV"):
                list(analyze.numeric_rows(path, ["cycle", "unit_id", "put", "get", "a", "b", "result"]))

    def test_overlap_and_zero_transition_gap_rejected(self):
        for second_start in (4, 5):
            bursts = [(0, 4, 0, 1), (second_start, second_start + 4, 1, 1)]
            assigned = [(i, unit, 0, start, end, products) for i, (start, end, unit, products) in enumerate(bursts)]
            with self.assertRaisesRegex(ValueError, "transition"):
                analyze.verify_assignments(bursts, assigned, 1)

    def test_full_switch_cycle_and_boundary_crossing(self):
        bursts = [(0, 4, 0, 1), (6, 10, 1, 1), (7, 11, 2, 1)]
        count, assigned = analyze.assign_bursts(bursts)
        self.assertEqual(count, 2)
        self.assertEqual(analyze.verify_assignments(bursts, assigned, count)["owner_changes"], 1)
        self.assertEqual(analyze.overlap(0, 5, (2, 9)), 3)

    def test_retimed_or_missing_reservation_rejected(self):
        bursts = [(0, 4, 0, 1)]
        with self.assertRaisesRegex(ValueError, "changed"):
            analyze.verify_assignments(bursts, [(0, 0, 0, 1, 5, 1)], 1)
        with self.assertRaisesRegex(ValueError, "Missing"):
            analyze.verify_assignments(bursts, [], 1)

    def test_stage_confined_control_separates_cross_stage_reclaim(self):
        bursts = [(0, 4, 0, 1), (6, 10, 1, 1)]
        stages = [{"id": 0, "name": "first"}, {"id": 1, "name": "second"}]
        global_count, _ = analyze.assign_bursts(bursts)
        local_count, allocations = analyze.stage_confined_schedule(bursts, stages, [0, 1])
        self.assertEqual((global_count, local_count), (1, 2))
        self.assertEqual([item["reclaimed_within_stage"] for item in allocations], [0, 0])

    def test_wait_completion_required_and_window_carry_in(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wait.csv"
            header = ["cycle", "stage", "input_wait", "output_wait"]
            analyze.write_csv(path, header, [(0, 0, 1, 0), (5, 0, 0, 1), (10, -1, 0, 0)])
            self.assertEqual(analyze.read_waits(path, 1, 10, (2, 8)), [[3, 3]])
            analyze.write_csv(path, header, [(0, 0, 1, 0), (5, 0, 0, 1)])
            with self.assertRaisesRegex(ValueError, "completion"):
                analyze.read_waits(path, 1, 10, (2, 8))

    def test_missing_kernel_completion_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kernel.log"
            path.write_text("SWAY_OUTPUT,0,0,100\n")
            with self.assertRaisesRegex(ValueError, "Missing kernel completion"):
                analyze.check_kernel(path, [0] * (56 * 320), [0] * (56 * 57))

    def test_whole_transaction_omission_rejected(self):
        per_frame = ([5120, 5120] + [640] * 4 +
                     [1280, 1280, 640, 12800, 12800, 320, 320, 12800] + [5120] * 7)
        stats = [{"puts_full_trace": count * 56} for count in per_frame * 2 + [6400, 6400, 1140]]
        self.assertEqual(analyze.check_product_counts(stats), 10959200)
        stats[0]["puts_full_trace"] -= 1
        with self.assertRaisesRegex(ValueError, "Missing/extra"):
            analyze.check_product_counts(stats)


if __name__ == "__main__":
    unittest.main()
