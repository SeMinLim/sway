#!/usr/bin/env python3
"""Focused regressions for shared carry protection in address replication."""
import unittest

import check_address_replicas as checker
import replicate_rom_address as transformer


def cell(kind, inputs, outputs, parameters=None):
    return {"hide_name": 0, "type": kind, "parameters": parameters or {}, "attributes": {},
            "port_directions": {**{p: "input" for p in inputs}, **{p: "output" for p in outputs}},
            "connections": {**{p: [wire] for p, wire in inputs.items()},
                            **{p: [wire] for p, wire in outputs.items()}}}


def fixture(with_carry=True):
    address = list(range(10, 23))
    cells = {}
    for bit, wire in enumerate(address):
        cells["address_" + str(bit)] = cell("TRELLIS_FF",
            {"DI": "0", "CE": 90, "LSR": 91, "CLK": 92}, {"Q": wire}, checker.FF_PARAMETERS.copy())
    for group in range(8):
        cells["coefficient_" + str(group)] = cell("LUT4",
            {"A": address[group], "B": "0", "C": "0", "D": "0"}, {"Z": 100 + group},
            {"INIT": "1010101010101010"})
    nets = {
        checker.ENGINE + "_addressR": {"bits": address},
        checker.ENGINE + "_operandQ_D_IN": {"bits": ["0"] + list(range(100, 108)) + ["0"] * 8},
    }
    for entry in (0, 1):
        storage = []
        for group in range(8):
            q = 400 + entry * 20 + group
            storage.append(q)
            cells["storage_" + str(entry) + "_" + str(group)] = cell("TRELLIS_FF",
                {"DI": 100 + group, "CE": 90, "LSR": 91, "CLK": 92}, {"Q": q},
                checker.FF_PARAMETERS.copy())
        nets[checker.ENGINE + "_operandQ.data" + str(entry) + "_reg"] = {
            "bits": ["0"] + storage + ["0"] * 8}
    if with_carry:
        cells["shared_carry_input"] = cell("LUT4",
            {"A": address[2], "B": "0", "C": "0", "D": "0"}, {"Z": 301},
            {"INIT": "1010101010101010"})
        cells["shared_carry"] = cell("CCU2C",
            {"A0": 301, "B0": address[0], "C0": "0", "D0": "0",
             "A1": "0", "B1": address[1], "C1": "0", "D1": "0", "CIN": "0"},
            {"S0": 300, "S1": 302, "COUT": 303},
            {"INIT0": "0110011010101010", "INIT1": "0110011010101010",
             "INJECT1_0": "NO", "INJECT1_1": "NO"})
        # First discovered from output group3, but also drives canonical state.
        cells["coefficient_3"]["connections"]["B"] = [300]
        cells["address_0"]["connections"]["DI"] = [303]
    return {"modules": {"mkTop": {"cells": cells, "netnames": nets, "ports": {}}}}


class AddressCarryTests(unittest.TestCase):
    def test_legacy_lut_ownership_unchanged(self):
        module = fixture(False)["modules"]["mkTop"]
        owners = checker.coefficient_owners(module, {})
        self.assertEqual(owners, {"coefficient_" + str(group): group for group in range(8)})
        self.assertEqual(checker.protected_carry_cells(module, owners, {}), set())

    def test_late_discovered_carry_and_full_fanin_are_canonical(self):
        module = fixture()["modules"]["mkTop"]
        owners = checker.coefficient_owners(module, {})
        self.assertEqual(owners["coefficient_3"], 3)
        self.assertEqual(owners["shared_carry"], 0)
        self.assertEqual(owners["shared_carry_input"], 0)
        self.assertEqual(checker.protected_carry_cells(module, owners, {}),
                         {"shared_carry", "shared_carry_input"})

    def test_transform_keeps_shared_state_update_cone_identical(self):
        before = fixture()
        after = transformer.transform(before)
        old, new = before["modules"]["mkTop"]["cells"], after["modules"]["mkTop"]["cells"]
        for name in ("shared_carry", "shared_carry_input", "address_0"):
            self.assertEqual(old[name], new[name])
        self.assertNotEqual(old["coefficient_3"]["connections"]["A"],
                            new["coefficient_3"]["connections"]["A"])

    def test_carry_input_rewire_is_rejected_before_reverse_aliasing(self):
        before = fixture()
        after = transformer.transform(before)
        cells = after["modules"]["mkTop"]["cells"]
        cells["shared_carry"]["connections"]["B1"] = cells[checker.PREFIX + "1_1"]["connections"]["Q"][:]
        with self.assertRaisesRegex(RuntimeError, "Protected carry cell or fan-in changed"):
            checker.audit_transition(before, after, "")

    def test_carry_fanin_input_rewire_is_rejected(self):
        before = fixture()
        after = transformer.transform(before)
        cells = after["modules"]["mkTop"]["cells"]
        cells["shared_carry_input"]["connections"]["A"] = cells[checker.PREFIX + "2_2"]["connections"]["Q"][:]
        with self.assertRaisesRegex(RuntimeError, "Protected carry cell or fan-in changed"):
            checker.audit_transition(before, after, "")

    def test_carry_parameter_change_is_rejected(self):
        before = fixture()
        after = transformer.transform(before)
        after["modules"]["mkTop"]["cells"]["shared_carry"]["parameters"]["INIT0"] = "0" * 16
        with self.assertRaisesRegex(RuntimeError, "Protected carry cell or fan-in changed"):
            checker.audit_transition(before, after, "")

    def test_forward_observation_boundary_does_not_accept_carry(self):
        netlist = transformer.transform(fixture())
        module = netlist["modules"]["mkTop"]
        q = module["cells"][checker.PREFIX + "1_1"]["connections"]["Q"][0]
        module["cells"]["shared_carry"]["connections"]["B1"] = [q]
        drivers, users = checker.connectivity(module)
        with self.assertRaisesRegex(RuntimeError, "Foreign primitive after address replica"):
            checker.observation_boundary(module, {q: 11}, drivers, users)

    def test_foreign_register_in_backward_cone_stays_rejected(self):
        module = fixture()["modules"]["mkTop"]
        module["cells"]["shared_carry_input"] = cell("TRELLIS_FF", {"DI": 12}, {"Q": 301})
        with self.assertRaisesRegex(RuntimeError, "Foreign register or primitive"):
            checker.coefficient_owners(module, {})

    def test_cycle_in_backward_cone_stays_rejected(self):
        module = fixture()["modules"]["mkTop"]
        module["cells"]["shared_carry_input"]["connections"]["A"] = [300]
        with self.assertRaisesRegex(RuntimeError, "Coefficient combinational cycle"):
            checker.coefficient_owners(module, {})


if __name__ == "__main__":
    unittest.main()
