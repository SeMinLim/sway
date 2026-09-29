#!/usr/bin/env python3
"""Reject physical checkpoints that weaken the fixed board/timing contract."""

import json
from pathlib import Path
import tempfile
import unittest

from check_physical import verify_router_settings


class PhysicalSettingsTest(unittest.TestCase):
    def check_settings(self, **changes):
        settings = {"arch.name": "ecp5", "arch.type": "lfe5u_85f",
                    "arch.package": "CABGA381", "arch.speed": "6",
                    "router": "router1", "placer": "heap", "seed": "1",
                    "timing_driven": "1", "auto_freq": "0",
                    "target_freq": "100000000", "router/tmg_ripup": "1",
                    "placerHeap/cellPlacementTimeout": "0"}
        settings.update(changes)
        settings = {key: value for key, value in settings.items() if value is not None}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "placed.json"
            path.write_text(json.dumps({"modules": {"top": {"settings": settings}}}))
            return verify_router_settings(path, require_ripup=True)

    def test_supported_arch_names(self):
        for name in ("ecp5", "ARCHNAME"):
            self.assertTrue(self.check_settings(**{"arch.name": name})["actual_settings_verified"])

    def test_board_contract(self):
        for key, value in (("arch.name", "ice40"), ("arch.type", "lfe5u_45f"),
                           ("arch.package", "CABGA256"), ("arch.speed", "8"),
                           ("router", "router2"), ("placer", "sa")):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.check_settings(**{key: value})

    def test_timing_overrides(self):
        for key in ("timing/allowFail", "timing/ignoreLoops", "timing/ignoreRelClk"):
            for value in ("1", "bad"):
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                    self.check_settings(**{key: value})

    def test_fixed_timing(self):
        for key, value in (("timing_driven", "0"), ("auto_freq", "1"),
                           ("target_freq", "99000000")):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.check_settings(**{key: value})

    def test_requires_explicit_disabled_retry_guard(self):
        for value in (None, "1", "8", "100000", "invalid"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.check_settings(**{"placerHeap/cellPlacementTimeout": value})
        for value in ("0", "0 ", "0" * 32):
            self.assertTrue(self.check_settings(**{"placerHeap/cellPlacementTimeout": value})
                            ["cell_placement_retry_guard_disabled"])

    def test_requires_timing_ripup(self):
        for value in (None, "0", "bad"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.check_settings(**{"router/tmg_ripup": value})


if __name__ == "__main__":
    unittest.main()
