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
