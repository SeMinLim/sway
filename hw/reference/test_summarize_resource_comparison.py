"""Reject mismatched/stale evidence before reporting an iso-throughput saving."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from summarize_resource_comparison import REQUIRED_SOURCES, summarize


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ResourceComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        sources = {key: digest(key) for key in REQUIRED_SOURCES |
                   {"bsv/SwayDelta.bsv", "bsv/SwayFoldedLinear.bsv"}}
        for name in ("buffered", "dedicated", "shared"):
            prefix = self.root / name
            self.write(prefix / "perf" / "summary.json", {
                "status": "pass", "source_sha256": sources,
                "metrics": {"continuous_frame_intervals_cycles": [7600] * 63,
                            "single_frame_latency_cycles": 16545 if name == "buffered" else 20763},
                "single": {"scalar_outputs_checked": 57}, "stream": {"scalar_outputs_checked": 3648},
            })
            hashes = {key: digest(name + key) for key in
                      ("raw", "replicated", "report", "log", "bitstream")}
            self.write(prefix / "physical/timing.json", {
                "status": "pass", "all_reported_clocks_pass": True, "core_pll_derived_mhz": 60,
                "report_sha256": hashes["report"], "log_sha256": hashes["log"],
                "bitstream_sha256": hashes["bitstream"],
            })
            self.write(prefix / "physical/physical_result.json", {
                "status": "pass", "netlist_sha256": hashes["raw"],
                "physical_netlist_sha256": hashes["replicated"],
                "nextpnr_report_sha256": hashes["report"], "nextpnr_log_sha256": hashes["log"],
                "bitstream_sha256": hashes["bitstream"],
                "packed_resources": {"TRELLIS_COMB": {"used": 900 if name == "shared" else 1000},
                                     "MULT18X18D": {"used": 0}},
                "raw_yosys_cells": {"LUT4": 100},
            })
            self.write(prefix / "physical/native_rom.json", {
                "status": "pass", "netlist_sha256": hashes["raw"],
            })
            self.write(prefix / "physical/control_replication_audit.json", {
                "status": "pass", "input_sha256": hashes["raw"], "output_sha256": hashes["replicated"],
            })
            prefixed_sources = {"hw/" + key: value for key, value in sources.items()}
            self.write(prefix / "physical/source_hashes.json",
                       {"source_sha256": prefixed_sources} if name == "shared" else prefixed_sources)
            manifest = {"source_sha256": prefixed_sources, "configuration": {
                "INPUT_PROJECTION_VARIANT": "B2", "RESOURCE_VARIANT": name,
                "CORE_MHZ": 60, "CRITICAL_CONTROL_REPAIR": "0",
            }}
            self.write(prefix / "manifest.json", manifest)
            stress_dir = prefix / "stress"
            for backend in ("bluesim", "verilator"):
                self.write(stress_dir / (backend + ".json"), {
                    "status": "pass", "frames_checked": 14, "scalar_outputs_checked": 798,
                    "input_sha256": sources["generated/test_input.hex"],
                    "expected_sha256": sources["generated/test_expected.hex"],
                    "sway_records_sha256": digest(name + "transactions"),
                })

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def reject_changed_field(self, relative, field, value):
        path = self.root / relative
        original = path.read_text()
        document = json.loads(original)
        document[field] = value
        self.write(path, document)
        try:
            with self.assertRaises(ValueError):
                summarize(self.root)
        finally:
            path.write_text(original)

    def test_valid_mixed_manifest_formats_and_zero_resource(self):
        report = summarize(self.root)
        self.assertEqual(report["status"], "pass")
        savings = report["sharing_ablation"]["packed_resource_savings"]
        self.assertEqual(savings["TRELLIS_COMB"], {"fewer_cells": 100, "reduction_percent": 10.0})
        self.assertIsNone(savings["MULT18X18D"]["reduction_percent"])
        self.assertEqual(report["variants"]["shared"]["validated_clock_mhz"], 60)
        self.assertAlmostEqual(report["variants"]["shared"]["derived_frames_per_second"], 60e6 / 7600)

    def test_failed_physical_audit_or_stress_report(self):
        for path in ("physical/physical_result.json", "physical/native_rom.json",
                     "physical/control_replication_audit.json", "stress/bluesim.json",
                     "stress/verilator.json"):
            with self.subTest(path=path):
                self.reject_changed_field("shared/" + path, "status", "fail")

    def test_stale_netlist_route_or_bitstream_hash(self):
        for field in ("netlist_sha256", "physical_netlist_sha256", "nextpnr_report_sha256",
                      "nextpnr_log_sha256", "bitstream_sha256"):
            with self.subTest(field=field):
                self.reject_changed_field("shared/physical/physical_result.json", field, digest("stale"))

    def test_absent_hashes_do_not_match(self):
        self.reject_changed_field("shared/physical/timing.json", "bitstream_sha256", None)

    def test_source_changed_between_simulation_and_physical(self):
        path = "shared/physical/source_hashes.json"
        entries = json.loads((self.root / path).read_text())["source_sha256"]
        entries["hw/bsv/SwayDelta.bsv"] = digest("different hardware")
        self.reject_changed_field(path, "source_sha256", entries)

    def test_same_sources_with_wrong_shared_macro_configuration(self):
        path = "shared/manifest.json"
        configuration = json.loads((self.root / path).read_text())["configuration"]
        configuration["RESOURCE_VARIANT"] = "dedicated"
        self.reject_changed_field(path, "configuration", configuration)

    def test_80mhz_configuration_rejected_for_final_60mhz_comparison(self):
        for name in ("buffered", "dedicated", "shared"):
            with self.subTest(name=name):
                path = name + "/manifest.json"
                configuration = json.loads((self.root / path).read_text())["configuration"]
                configuration["CORE_MHZ"] = 80
                self.reject_changed_field(path, "configuration", configuration)

    def test_buffered_configuration_is_required(self):
        (self.root / "buffered/manifest.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing or ambiguous build configuration"):
            summarize(self.root)

    def test_legacy_alternate_manifest_is_rejected(self):
        path = self.root / "shared/manifest.json"
        (path.parent / "source_manifest.json").write_text(path.read_text())
        with self.assertRaisesRegex(ValueError, "Missing or ambiguous build configuration"):
            summarize(self.root)

    def test_missing_candidate_resource_is_not_a_saving(self):
        self.reject_changed_field("shared/physical/physical_result.json", "packed_resources",
                                  {"MULT18X18D": {"used": 0}})

    def test_unequal_throughput_is_not_an_iso_throughput_pass(self):
        self.reject_changed_field("shared/perf/summary.json", "metrics", {
            "continuous_frame_intervals_cycles": [8000] * 63,
            "single_frame_latency_cycles": 20763,
        })

    def test_incomplete_or_different_stress_evidence(self):
        self.reject_changed_field("shared/stress/verilator.json", "scalar_outputs_checked", 797)
        self.reject_changed_field("shared/stress/verilator.json", "input_sha256", digest("other inputs"))
        self.reject_changed_field("shared/stress/verilator.json", "sway_records_sha256", digest("other events"))

    def test_unvalidated_clock_or_failing_secondary_clock(self):
        self.reject_changed_field("shared/physical/timing.json", "core_pll_derived_mhz", 80)
        self.reject_changed_field("shared/physical/timing.json", "core_pll_derived_mhz", 100)
        self.reject_changed_field("shared/physical/timing.json", "all_reported_clocks_pass", False)


if __name__ == "__main__":
    unittest.main()
