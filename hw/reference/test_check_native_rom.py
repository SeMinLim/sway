#!/usr/bin/env python3
"""Synthetic mapped-cell controls for the FIFO2 address proof, not a P&R test.

The fixture implements the Boolean assignments in BSC 2026.01 FIFO2.v using
LUT4/TRELLIS_FF cells. Actual B1/B2 mapper compatibility needs a real netlist.
"""

import copy
import unittest

from check_native_rom import fifo2_address_path


def fixture():
    cells = {}
    command = "core_block0_inputProjection_engine_issueCommandQ"
    address = list(range(100, 111))
    head = list(range(200, 220))
    tail = list(range(300, 320))
    enq, deq, full, empty, clock = 400, 401, 402, 403, 404
    next_bit = 500

    def lut(name, inputs, fn):
        nonlocal next_bit
        bit = next_bit
        next_bit += 1
        padded = [*inputs, *(["0"] * (4 - len(inputs)))]
        truth = sum(int(fn(*[(i >> shift) & 1 for shift in range(len(inputs))])) << i
                    for i in range(16))
        cells[name] = {"type": "LUT4", "parameters": {"INIT": f"{truth:016b}"},
                       "connections": dict(zip("ABCDZ", [[x] for x in padded + [bit]])),
                       "port_directions": dict(zip("ABCDZ", ["input"] * 4 + ["output"]))}
        return bit

    def register(name, data, output, enable, ce_mode):
        cells[name] = {"type": "TRELLIS_FF", "parameters": {
            "CLKMUX": "CLK", "CEMUX": ce_mode, "LSRMUX": "LSR", "GSR": "DISABLED"},
            "attributes": {"src": "BSC-2026.01/lib/Verilog/FIFO2.v:131.4-150.9"},
            "connections": {"CLK": [clock], "CE": [enable], "DI": [data], "LSR": ["0"], "Q": [output]},
            "port_directions": {"CLK": "input", "CE": "input", "DI": "input", "LSR": "input", "Q": "output"}}

    from_input = lut("d0di", [enq, deq, full, empty],
                     lambda e, d, f, n: (e and not n) or (e and d and f))
    from_tail = lut("d0d1", [deq, full], lambda d, f: d and not f)
    hold = lut("d0h", [enq, deq, full, empty],
               lambda e, d, f, n: (not d and not e) or (not d and n) or (not e and f))
    tail_enable = lut("d1di", [enq, empty], lambda e, n: e and n)
    for i in range(11):
        new_value = lut(f"input_term_{i}", [from_input, address[i]], lambda a, b: a and b)
        tail_value = lut(f"tail_term_{i}", [from_tail, tail[i + 9]], lambda a, b: a and b)
        hold_value = lut(f"hold_term_{i}", [hold, head[i + 9]], lambda a, b: a and b)
        head_data = lut(f"head_data_{i}", [new_value, tail_value, hold_value], lambda a, b, c: a or b or c)
        register(f"head_{i}", head_data, head[i + 9], "1", "1")
        register(f"tail_{i}", address[i], tail[i + 9], tail_enable, "CE")
    nets = {command + suffix: {"bits": bits} for suffix, bits in {
        ".data0_reg": head, ".data1_reg": tail,
        ".ENQ": [enq], ".DEQ": [deq], ".FULL_N": [full], ".EMPTY_N": [empty]}.items()}
    return cells, nets, command, address, head[9:20], clock


def check(data):
    cells, nets, command, address, actual, clock = data
    drivers = {}
    for name, cell in cells.items():
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    drivers.setdefault(bit, []).append((name, port))
    return fifo2_address_path(cells, nets, drivers, command, address, actual, clock)


class TestFIFO2AddressProof(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def test_source_equations_pass_all_1408_assignments(self):
        result = check(self.data)
        self.assertEqual(result["truth_assignments_checked"], 1408)
        self.assertEqual(len(result["registers"]), 22)

    def test_mapped_head_clock_enable_equivalent_to_hold_passes(self):
        cells = self.data[0]
        hold_bit = cells["d0h"]["connections"]["Z"][0]
        cells["head_enable"] = {
            "type": "LUT4", "parameters": {"INIT": "0101010101010101"},
            "connections": {"A": [hold_bit], "B": ["0"], "C": ["0"], "D": ["0"], "Z": [900]},
            "port_directions": {"A": "input", "B": "input", "C": "input", "D": "input", "Z": "output"}}
        for i in range(11):
            cells[f"head_{i}"]["parameters"]["CEMUX"] = "CE"
            cells[f"head_{i}"]["connections"]["CE"] = [900]
        check(self.data)

    def test_yosys_string_constant_enable_without_ce_port_passes(self):
        for i in range(11):
            cell = self.data[0][f"head_{i}"]
            cell["parameters"]["CEMUX"] = "1 "
            del cell["connections"]["CE"]
            del cell["port_directions"]["CE"]
        check(self.data)

    def mux_fixture(self, kind):
        cells = self.data[0]
        original = cells["head_data_0"]
        a, b, c = [original["connections"][p][0] for p in "ABC"]
        cells["mux_low"] = {
            "type": "LUT4", "parameters": {"INIT": "1110111011101110"},
            "connections": {"A": [b], "B": [c], "C": ["0"], "D": ["0"], "Z": [900]},
            "port_directions": {"A": "input", "B": "input", "C": "input", "D": "input", "Z": "output"}}
        low, high, select = ("BLUT", "ALUT", "C0") if kind == "PFUMX" else ("D0", "D1", "SD")
        cells["head_data_0"] = {
            "type": kind, "parameters": {},
            "connections": {low: [900], high: ["1"], select: [a], "Z": original["connections"]["Z"]},
            "port_directions": {low: "input", high: "input", select: "input", "Z": "output"}}
        return low, high

    def test_native_mux_primitives_preserve_equations(self):
        for kind in ("PFUMX", "L6MUX21"):
            with self.subTest(kind=kind):
                self.data = fixture()
                self.mux_fixture(kind)
                check(self.data)

    def test_reversed_native_mux_selection_rejected(self):
        for kind in ("PFUMX", "L6MUX21"):
            with self.subTest(kind=kind):
                self.data = fixture()
                low, high = self.mux_fixture(kind)
                connections = self.data[0]["head_data_0"]["connections"]
                connections[low], connections[high] = connections[high], connections[low]
                with self.assertRaisesRegex(ValueError, "next-state mismatch"):
                    check(self.data)

    def remove_aliases_with_guarded_controls(self):
        cells, nets, command, *_ = self.data
        for name, request, guard, output in (("enqueue_guard", 450, 402, 400),
                                             ("dequeue_guard", 451, 403, 401)):
            cells[name] = {
                "type": "LUT4", "parameters": {"INIT": "1000100010001000"},
                "connections": {"A": [request], "B": [guard], "C": ["0"], "D": ["0"], "Z": [output]},
                "port_directions": {"A": "input", "B": "input", "C": "input", "D": "input", "Z": "output"}}
        for suffix in (".ENQ", ".DEQ", ".data0_reg"):
            del nets[command + suffix]
        for name, output, reset_value, fn in (
            ("full_state", 402, "SET", lambda e, d, f, n: (not n) if e and not d else 1 if d and not e else f),
            ("empty_state", 403, "RESET", lambda e, d, f, n: 1 if e and not d else (not f) if d and not e else n),
        ):
            incoming = output + 500
            truth = sum(int(fn(*[(i >> shift) & 1 for shift in range(4)])) << i for i in range(16))
            cells[name + "_next"] = {
                "type": "LUT4", "parameters": {"INIT": f"{truth:016b}"},
                "connections": {"A": [400], "B": [401], "C": [402], "D": [403], "Z": [incoming]},
                "port_directions": {"A": "input", "B": "input", "C": "input", "D": "input", "Z": "output"}}
            cells[name] = {
                "type": "TRELLIS_FF", "parameters": {"CLKMUX": "CLK", "CEMUX": "1 ", "LSRMUX": "LSR",
                                                       "GSR": "DISABLED", "REGSET": reset_value, "SRMODE": "LSR_OVER_CE"},
                "attributes": {"src": "BSC-2026.01/lib/Verilog/FIFO2.v:104.4-130.9"},
                "connections": {"CLK": [404], "DI": [incoming], "LSR": [455], "Q": [output]},
                "port_directions": {"CLK": "input", "DI": "input", "LSR": "input", "Q": "output"}}
        # With guarded enqueue, ENQ implies FULL_N; synthesis can remove FULL_N
        # from the ENQ && DEQ && FULL_N term without changing reachable behavior.
        cells["d0di"]["parameters"]["INIT"] = f"{sum(int((i & 1) and (((i >> 1) & 1) or not ((i >> 3) & 1))) << i for i in range(16)):016b}"

    def test_optimized_alias_recovery_with_proven_guards_passes(self):
        self.remove_aliases_with_guarded_controls()
        result = check(self.data)
        self.assertTrue(result["control_aliases_recovered"])
        self.assertTrue(result["occupancy_guard_implications_proven"])
        self.assertEqual(result["truth_assignments_checked"], 792)
        self.assertEqual(result["control_bits"]["ENQ"], 400)
        self.assertEqual(result["control_bits"]["DEQ"], 401)
        self.assertEqual(result["occupancy_identity_proof"]["transition_assignments_checked"], 9)

    def test_recovered_controls_without_guard_implications_rejected(self):
        for name in ("enqueue_guard", "dequeue_guard"):
            with self.subTest(name=name):
                self.data = fixture()
                self.remove_aliases_with_guarded_controls()
                self.data[0][name]["parameters"]["INIT"] = "1010101010101010"
                with self.assertRaisesRegex(ValueError, "uniquely proven FIFO2 dequeue"):
                    check(self.data)

    def test_alias_recovery_does_not_mask_wrong_data_enable_clock_or_rom(self):
        mutations = {
            "data": lambda c, a: c["tail_0"]["connections"].update(DI=[101]),
            "enable": lambda c, a: c["tail_1"]["connections"].update(CE=["1"]),
            "clock": lambda c, a: c["head_0"]["connections"].update(CLK=[9999]),
            "rom": lambda c, a: a.__setitem__(0, 100),
            "head_logic": lambda c, a: c["head_data_1"]["parameters"].update(INIT="0" * 16),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.data = fixture()
                self.remove_aliases_with_guarded_controls()
                mutate(self.data[0], self.data[4])
                with self.assertRaises(ValueError):
                    check(self.data)

    def test_data_only_replacement_controls_rejected_against_occupancy(self):
        for original, gates in ((401, ("d0di", "d0d1", "d0h")), (400, ("d0di", "d0h", "d1di"))):
            with self.subTest(original=original):
                self.data = fixture()
                self.remove_aliases_with_guarded_controls()
                cells = self.data[0]
                cells["alternate_control"] = {
                    "type": "LUT4", "parameters": {"INIT": "1000100010001000"},
                    "connections": {"A": [original], "B": [452], "C": ["0"], "D": ["0"], "Z": [950]},
                    "port_directions": {"A": "input", "B": "input", "C": "input", "D": "input", "Z": "output"}}
                for gate in gates:
                    for port in "ABCD":
                        if cells[gate]["connections"][port] == [original]:
                            cells[gate]["connections"][port] = [950]
                with self.assertRaisesRegex(ValueError, "uniquely proven FIFO2 dequeue"):
                    check(self.data)

    def test_recovered_occupancy_reset_or_transition_corruption_rejected(self):
        for name, field in (("full_state", "REGSET"), ("empty_state_next", "INIT")):
            with self.subTest(name=name):
                self.data = fixture()
                self.remove_aliases_with_guarded_controls()
                self.data[0][name]["parameters"][field] = "RESET" if field == "REGSET" else "0" * 16
                with self.assertRaisesRegex(ValueError, "uniquely proven FIFO2 dequeue"):
                    check(self.data)

    def test_tail_wrong_counter_bit_rejected(self):
        self.data[0]["tail_0"]["connections"]["DI"] = [self.data[3][1]]
        with self.assertRaisesRegex(ValueError, "does not match the address counter"):
            check(self.data)

    def test_wrong_head_truth_table_rejected(self):
        self.data[0]["head_data_0"]["parameters"]["INIT"] = "0" * 16
        with self.assertRaisesRegex(ValueError, "next-state mismatch"):
            check(self.data)

    def test_tail_wrong_enable_rejected(self):
        self.data[0]["tail_0"]["connections"]["CE"] = ["1"]
        with self.assertRaisesRegex(ValueError, "next-state mismatch"):
            check(self.data)

    def test_head_wrong_clock_rejected(self):
        self.data[0]["head_0"]["connections"]["CLK"] = [9999]
        with self.assertRaisesRegex(ValueError, "CLK does not match"):
            check(self.data)

    def test_unrecognized_register_provenance_rejected(self):
        self.data[0]["head_0"]["attributes"]["src"] = "other.v:1.1-2.1"
        with self.assertRaisesRegex(ValueError, "not a mapped FIFO2"):
            check(self.data)

    def test_data_reset_rejected(self):
        self.data[0]["tail_0"]["connections"]["LSR"] = [400]
        with self.assertRaisesRegex(ValueError, "data reset must be disabled"):
            check(self.data)

    def test_unproven_data_source_rejected(self):
        self.data[0]["input_term_0"]["connections"]["B"] = [self.data[3][1]]
        with self.assertRaisesRegex(ValueError, "unproven FIFO2 address input"):
            check(self.data)

    def test_rom_address_bypass_rejected(self):
        self.data[4][0] = self.data[3][0]
        with self.assertRaisesRegex(ValueError, "head does not drive"):
            check(self.data)

    def test_unknown_logic_fails_closed(self):
        self.data[0]["head_data_0"]["type"] = "UNVERIFIED_PRIMITIVE"
        with self.assertRaisesRegex(ValueError, "unsupported FIFO2 address logic"):
            check(self.data)

    def test_alias_between_slots_rejected(self):
        data = copy.deepcopy(self.data)
        command = data[2]
        data[1][command + ".data1_reg"]["bits"][9] = data[4][0]
        with self.assertRaisesRegex(ValueError, "DI does not match|aliases unrelated"):
            check(data)


if __name__ == "__main__":
    unittest.main()
