#!/usr/bin/env python3
"""Synthetic parser tests only; these fixtures are not FPGA timing evidence."""

import json
from pathlib import Path
import tempfile
import unittest

from check_timing import check_timing


class TimingGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sway_timing_checker_")
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.report_path = folder / "synthetic_report.json"
        self.log_path = folder / "synthetic_route.log"
        self.bitstream_path = folder / "synthetic_bytes.bit"
        self.core = "$glbnet$clocks_coreReset.CLK"
        self.uart = "$glbnet$CLK_clk_25mhz$TRELLIS_IO_IN"
        self.report = {
            "fmax": {
                self.core: {"achieved": 105.125, "constraint": 100.0},
                self.uart: {"achieved": 80.5, "constraint": 25.0},
            },
            "critical_paths": [
                {"from": "posedge " + name, "to": "posedge " + name,
                 "path": [{"type": "routing", "delay": 1.0}]}
                for name in (self.core, self.uart)
            ],
        }
        self.log = (
            "Info:     Derived frequency constraint of 100.0 MHz for net clocks_coreReset.CLK\n"
            "Info: Routing complete.\n"
            f"Info: Max frequency for clock '{self.core}': 105.13 MHz (PASS at 100.00 MHz)\n"
            f"Info: Max frequency for clock '{self.uart}': 80.50 MHz (PASS at 25.00 MHz)\n"
            "Info: Program finished normally.\n"
        )
        self.bitstream_path.write_bytes(b"SYNTHETIC CHECKER TEST, NOT AN FPGA BITSTREAM")

    def check(self, core_mhz=100.0):
        self.report_path.write_text(json.dumps(self.report))
        self.log_path.write_text(self.log)
        return check_timing(self.report_path, self.log_path, self.bitstream_path, core_mhz)

    def test_complete_pass(self):
        result = self.check()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["core_pll_derived_mhz"], 100.0)
        self.assertEqual(len(result["bitstream_sha256"]), 64)

    def select_80mhz(self):
        self.report["fmax"][self.core]["constraint"] = 80.0
        self.report["fmax"][self.core]["achieved"] = 92.125
        self.log = self.log.replace("100.0 MHz for net", "80.0 MHz for net")
        self.log = self.log.replace("105.13 MHz (PASS at 100.00 MHz)",
                                    "92.13 MHz (PASS at 80.00 MHz)")

    def test_selected_80mhz_pass(self):
        self.select_80mhz()
        result = self.check(80.0)
        self.assertEqual(result["core_pll_derived_mhz"], 80.0)
        self.assertEqual(result["clocks"]["clocks_coreReset.CLK"]["constraint_mhz"], 80.0)

    def test_80mhz_requires_explicit_selection(self):
        self.select_80mhz()
        with self.assertRaisesRegex(AssertionError, "PLL-derived 100 MHz"):
            self.check()

    def test_80mhz_target_rejects_100mhz_pll(self):
        self.report["fmax"][self.core]["constraint"] = 80.0
        with self.assertRaisesRegex(AssertionError, "PLL-derived 80 MHz"):
            self.check(80.0)

    def test_80mhz_target_rejects_wrong_report_constraint(self):
        self.select_80mhz()
        self.report["fmax"][self.core]["constraint"] = 75.0
        with self.assertRaisesRegex(AssertionError, "exactly 80.0 MHz"):
            self.check(80.0)

    def test_80mhz_target_still_rejects_timing_failure(self):
        self.select_80mhz()
        self.report["fmax"][self.core]["achieved"] = 79.99
        with self.assertRaisesRegex(AssertionError, "failed timing"):
            self.check(80.0)

    def test_unsupported_pll_frequency_rejected(self):
        with self.assertRaisesRegex(ValueError, "physical 60, 80, or 100 MHz"):
            self.check(70.0)

    def select_60mhz(self):
        self.report["fmax"][self.core]["constraint"] = 60.0
        self.report["fmax"][self.core]["achieved"] = 72.125
        self.log = self.log.replace("100.0 MHz for net", "60.0 MHz for net")
        self.log = self.log.replace("105.13 MHz (PASS at 100.00 MHz)",
                                    "72.13 MHz (PASS at 60.00 MHz)")

    def test_selected_60mhz_pass(self):
        self.select_60mhz()
        self.assertEqual(self.check(60.0)["core_pll_derived_mhz"], 60.0)

    def test_actual_60mhz_integer_ps_constraint_pass(self):
        self.select_60mhz()
        self.report["fmax"][self.core]["constraint"] = 60.00239944458008
        result = self.check(60.0)
        self.assertEqual(result["core_pll_derived_mhz"], 60.0)
        self.assertEqual(result["clocks"]["clocks_coreReset.CLK"]["constraint_mhz"],
                         60.00239944458008)

    def test_60mhz_weaker_constraint_never_accepted(self):
        self.select_60mhz()
        for frequency in (59.9988, 59.9999999):
            with self.subTest(frequency=frequency):
                self.report["fmax"][self.core]["constraint"] = frequency
                with self.assertRaisesRegex(AssertionError, "exactly 60.0 MHz"):
                    self.check(60.0)

    def test_60mhz_arbitrary_nearby_constraint_rejected(self):
        self.select_60mhz()
        for frequency in (60.0000001, 60.001, 60.0024, 60.00239944458009):
            with self.subTest(frequency=frequency):
                self.report["fmax"][self.core]["constraint"] = frequency
                with self.assertRaisesRegex(AssertionError, "exactly 60.0 MHz"):
                    self.check(60.0)

    def test_60mhz_must_pass_stricter_integer_ps_constraint(self):
        self.select_60mhz()
        self.report["fmax"][self.core]["constraint"] = 60.00239944458008
        self.report["fmax"][self.core]["achieved"] = 60.001
        with self.assertRaisesRegex(AssertionError, "failed timing"):
            self.check(60.0)

    def test_60mhz_requires_explicit_selection(self):
        self.select_60mhz()
        with self.assertRaisesRegex(AssertionError, "PLL-derived 100 MHz"):
            self.check()

    def test_60mhz_target_rejects_80mhz_pll(self):
        self.select_80mhz()
        with self.assertRaisesRegex(AssertionError, "PLL-derived 60 MHz"):
            self.check(60.0)

    def test_60mhz_target_rejects_wrong_report_constraint(self):
        self.select_60mhz()
        self.report["fmax"][self.core]["constraint"] = 59.0
        with self.assertRaisesRegex(AssertionError, "exactly 60.0 MHz"):
            self.check(60.0)

    def test_60mhz_target_still_rejects_timing_failure(self):
        self.select_60mhz()
        self.report["fmax"][self.core]["achieved"] = 59.99
        with self.assertRaisesRegex(AssertionError, "failed timing"):
            self.check(60.0)

    def test_router2_completion(self):
        self.log = self.log.replace("Routing complete.", "Router2 time 12.34s")
        self.assertEqual(self.check()["status"], "pass")

    def test_placement_failure_is_not_final_timing(self):
        placement = f"Info: Max frequency for clock '{self.core}': 18.40 MHz (FAIL at 100.00 MHz)\n"
        self.log = placement + self.log
        self.assertEqual(self.check()["status"], "pass")

    def test_18_mhz_rejected(self):
        self.report["fmax"][self.core]["achieved"] = 18.4
        with self.assertRaises(AssertionError):
            self.check()

    def test_reduced_core_constraint_rejected(self):
        self.report["fmax"][self.core]["constraint"] = 50.0
        with self.assertRaises(AssertionError):
            self.check()

    def test_unfinished_route_rejected(self):
        self.log = self.log.replace("Info: Routing complete.\n", "")
        with self.assertRaises(AssertionError):
            self.check()

    def test_missing_normal_exit_rejected(self):
        self.log = self.log.replace("Info: Program finished normally.\n", "")
        with self.assertRaises(AssertionError):
            self.check()

    def test_missing_and_empty_bitstream_rejected(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.bitstream_path.unlink()
                else:
                    self.bitstream_path.write_bytes(b"")
                with self.assertRaises(AssertionError):
                    self.check()

    def test_other_clock_failure_rejected(self):
        self.report["fmax"]["other_clock"] = {"achieved": 10.0, "constraint": 20.0}
        with self.assertRaises(AssertionError):
            self.check()

    def test_missing_actual_pll_clock_rejected(self):
        self.log = self.log.replace("100.0 MHz for net clocks_coreReset.CLK", "100.0 MHz for net unused_clock")
        with self.assertRaises(AssertionError):
            self.check()

    def test_stale_json_log_pair_rejected(self):
        self.report["fmax"][self.core]["achieved"] = 110.0
        with self.assertRaises(AssertionError):
            self.check()

    def test_failures_and_bypass_flag_rejected(self):
        original = self.log
        for text in ("ERROR: synthetic error\n", "FATAL: synthetic error\n", "--timing-allow-fail\n"):
            with self.subTest(text=text):
                self.log = original + text
                with self.assertRaises(AssertionError):
                    self.check()

    def test_nonfinite_frequency_rejected(self):
        self.report["fmax"][self.core]["achieved"] = float("nan")
        with self.assertRaises(ValueError):
            self.check()

    def test_hold_violation_warning_rejected(self):
        self.log += "Warning: Hold/min time violation for clock 'posedge clocks_coreReset.CLK':\n"
        with self.assertRaises(AssertionError):
            self.check()


if __name__ == "__main__":
    unittest.main()
