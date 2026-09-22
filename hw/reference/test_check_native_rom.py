#!/usr/bin/env python3
"""Synthetic mapped-cell controls for the FIFO2 address proof, not a P&R test.

The fixture implements the Boolean assignments in BSC 2026.01 FIFO2.v using
LUT4/TRELLIS_FF cells. Actual B1/B2 mapper compatibility needs a real netlist.
"""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from check_native_rom import BooleanProof, ENGINES, check_native_rom, fifo2_address_path


def fixture(command_width=20):
    address_offset = command_width - 11
    cells = {}
    command = "core_block0_inputProjection_engine_issueCommandQ"
    address = list(range(100, 111))
    head = list(range(200, 200 + command_width))
    tail = list(range(300, 300 + command_width))
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
        tail_value = lut(f"tail_term_{i}", [from_tail, tail[i + address_offset]], lambda a, b: a and b)
        hold_value = lut(f"hold_term_{i}", [hold, head[i + address_offset]], lambda a, b: a and b)
        head_data = lut(f"head_data_{i}", [new_value, tail_value, hold_value], lambda a, b, c: a or b or c)
        register(f"head_{i}", head_data, head[i + address_offset], "1", "1")
        register(f"tail_{i}", address[i], tail[i + address_offset], tail_enable, "CE")
    nets = {command + suffix: {"bits": bits} for suffix, bits in {
        ".data0_reg": head, ".data1_reg": tail,
        ".ENQ": [enq], ".DEQ": [deq], ".FULL_N": [full], ".EMPTY_N": [empty]}.items()}
    return cells, nets, command, address, head[address_offset:command_width], clock


def check(data):
    cells, nets, command, address, actual, clock = data
    drivers = {}
    for name, cell in cells.items():
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    drivers.setdefault(bit, []).append((name, port))
    command_width = len(nets[command + ".data1_reg"]["bits"])
    return fifo2_address_path(cells, nets, drivers, command, address, actual, clock,
                              command_width, command_width - 11)


class TestBooleanProof(unittest.TestCase):
    def test_all_two_input_function_identities(self):
        bdd = BooleanProof()
        x, y = bdd.variable(10), bdd.variable(20)
        nodes = [bdd.choose(y, bdd.choose(x, (table >> 3) & 1, (table >> 2) & 1),
                           bdd.choose(x, (table >> 1) & 1, table & 1)) for table in range(16)]
        for left in range(16):
            self.assertEqual(bdd.negate(nodes[left]), nodes[left ^ 15])
            for right in range(16):
                self.assertEqual(bdd.apply("and", nodes[left], nodes[right]), nodes[left & right])
                self.assertEqual(bdd.apply("or", nodes[left], nodes[right]), nodes[left | right])
                self.assertEqual(bdd.apply("xor", nodes[left], nodes[right]), nodes[left ^ right])
                for select in range(16):
                    self.assertEqual(bdd.choose(nodes[select], nodes[left], nodes[right]),
                                     nodes[(select & left) | ((select ^ 15) & right)])


class TestFIFO2AddressProof(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def test_source_equations_pass_all_1408_assignments(self):
        result = check(self.data)
        self.assertEqual(result["truth_assignments_checked"], 1408)
        self.assertEqual(len(result["registers"]), 22)

    def test_folded_command_address_equations_pass(self):
        self.data = fixture(22)
        result = check(self.data)
        self.assertEqual(result["truth_assignments_checked"], 1408)
        self.assertIn("D_IN21:11", result["mapping"])

    def test_folded_wrong_counter_bit_rejected(self):
        self.data = fixture(22)
        self.data[0]["tail_0"]["connections"]["DI"] = [self.data[3][1]]
        with self.assertRaisesRegex(ValueError, "does not match the address counter"):
            check(self.data)

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

    def test_folded_optimized_alias_recovery_with_proven_guards_passes(self):
        self.data = fixture(22)
        self.remove_aliases_with_guarded_controls()
        result = check(self.data)
        self.assertTrue(result["occupancy_guard_implications_proven"])
        self.assertEqual(result["truth_assignments_checked"], 792)
        self.assertIn("D_IN21:11", result["mapping"])

    def factor_dequeue_control(self):
        self.data = fixture(22)
        self.remove_aliases_with_guarded_controls()
        cells = self.data[0]
        original = cells.pop("dequeue_guard")
        replacements = []
        for name, cell in cells.items():
            for port, direction in cell["port_directions"].items():
                if direction == "input" and cell["connections"][port] == [401]:
                    replacements.append((name, port))
        for index, (name, port) in enumerate(replacements):
            control = copy.deepcopy(original)
            control["connections"]["A"] = [3000 + index]
            control["connections"]["Z"] = [2000 + index]
            cells[f"duplicated_request_{index}"] = {
                "type": "LUT4", "parameters": {"INIT": "1000100010001000"},
                "connections": {"A": [451], "B": [456], "C": ["0"], "D": ["0"], "Z": [3000 + index]},
                "port_directions": {"A": "input", "B": "input", "C": "input", "D": "input", "Z": "output"}}
            cells[f"duplicated_dequeue_{index}"] = control
            cells[name]["connections"][port] = [2000 + index]

    def test_factored_dispatch_proves_complete_mapped_cones(self):
        self.factor_dequeue_control()
        result = check(self.data)
        self.assertIsNone(result["control_bits"]["DEQ"])
        self.assertEqual(result["occupancy_identity_proof"]["legal_occupancy_states_checked"], 3)
        self.assertIn("ROBDD", result["occupancy_identity_proof"]["method"])
        self.assertEqual(len(result["registers"]), 22)

    def test_factored_dispatch_still_rejects_corrupted_data_or_occupancy(self):
        for name, field, value in (("head_data_1", "INIT", "0" * 16),
                                   ("full_state", "REGSET", "RESET"),
                                   ("empty_state_next", "INIT", "0" * 16)):
            with self.subTest(name=name):
                self.factor_dequeue_control()
                self.data[0][name]["parameters"][field] = value
                with self.assertRaises(ValueError):
                    check(self.data)

    def test_factored_dispatch_rejects_tail_enable_corruption(self):
        self.factor_dequeue_control()
        self.data[0]["tail_1"]["connections"]["CE"] = ["1"]
        with self.assertRaisesRegex(ValueError, "next-state mismatch"):
            check(self.data)

    def test_factored_dispatch_rejects_wrong_fifo_address_input(self):
        self.factor_dequeue_control()
        self.data[0]["tail_1"]["connections"]["DI"] = [self.data[3][0]]
        with self.assertRaisesRegex(ValueError, "does not match the address counter"):
            check(self.data)

    def test_factored_dispatch_rejects_unknown_logic(self):
        self.factor_dequeue_control()
        self.data[0]["head_data_1"]["type"] = "UNPROVEN_PRIMITIVE"
        with self.assertRaisesRegex(ValueError, "unsupported FIFO2 Boolean logic"):
            check(self.data)

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


def native_fixture(root, resource_comparison):
    """Mapped coefficient fixtures exercise exact coverage/content, not P&R."""
    generated = root / "generated"
    generated.mkdir()
    header = generated / "SwayCoeffRomInit.vh"
    header.write_text("synthetic native coefficient initialization fixture\n")
    hashes = {header.name: hashlib.sha256(header.read_bytes()).hexdigest()}
    banks = []
    for layer in range(11):
        for lane in range(4):
            source = generated / f"linear_l{layer}_lane{lane}.hex"
            source.write_text(f"{layer * 4 + lane:02x}\n")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            hashes[source.name] = digest
            banks.append({"layer": layer, "lane": lane, "file": source.name, "depth": 1, "sha256": digest})
    reference = root / "reference.json"
    reference.write_text(json.dumps({"generatedSHA256": hashes, "linearROMBanks": {
        "banks": banks, "nativeRegisteredROM": {"file": header.name}}}))
    cells = {}
    nets = {"clocks_coreReset.CLK": {"bits": [1]}}
    layers = [layer for layer in range(11) if not resource_comparison or layer not in (3, 7)]
    for layer in layers:
        command = ENGINES[layer] + "_issueCommandQ"
        width = 22 if resource_comparison and layer not in (1, 5) else 20
        offset = width - 11
        base = 10000 * (layer + 1)
        address = [base + i for i in range(11)]
        head = [base + 100 + i for i in range(width)]
        if resource_comparison:
            fcells, fnets, fname, faddress, factual, fclock = fixture(width)
            def remap(bit):
                return 1 if bit == fclock else base + bit if type(bit) is int else bit
            for name, cell in fcells.items():
                cell = copy.deepcopy(cell)
                cell["connections"] = {port: [remap(bit) for bit in bits]
                                       for port, bits in cell["connections"].items()}
                cells[command + "." + name] = cell
            for name, net in fnets.items():
                nets[name.replace(fname, command)] = {"bits": [remap(bit) for bit in net["bits"]]}
            address = [remap(bit) for bit in faddress]
            head = nets[command + ".data0_reg"]["bits"]
        else:
            for index in range(11):
                cells[command + f".address_ff{index}"] = {
                    "type": "TRELLIS_FF", "parameters": {"CLKMUX": "CLK", "CEMUX": "CE",
                        "LSRMUX": "LSR", "GSR": "DISABLED"},
                    "attributes": {"src": "BSC-2026.01/lib/Verilog/FIFO1.v:1.1-2.1"},
                    "connections": {"DI": [address[index]], "Q": [head[offset + index]], "CLK": [1],
                                    "LSR": ["0"], "CE": [base + 500]},
                    "port_directions": {"DI": "input", "Q": "output", "CLK": "input", "LSR": "input", "CE": "input"}}
        nets[ENGINES[layer] + "_addressCnt"] = {"bits": address}
        nets[command + ".D_IN"] = {"bits": [base + 1000 + i for i in range(offset)] + address}
        nets[command + ".D_OUT"] = {"bits": head}
        for lane in range(4):
            prefix = f"{ENGINES[layer]}_weightR_{lane}_raw"
            parameters = {"REGMODE_A": "OUTREG", "REGMODE_B": "OUTREG", "DATA_WIDTH_A": "1001",
                          "DATA_WIDTH_B": "1001", "GSR": "DISABLED", "CSDECODE_A": "0b000", "CSDECODE_B": "0b000"}
            for block in range(64):
                parameters[f"INITVAL_{block:02X}"] = f"{layer * 4 + lane if block == 0 else 0:0320b}"
            connections = {port: [bit] for port, bit in {"CEA": "1", "OCEA": "1", "WEA": "0", "RSTA": "0",
                "CEB": "0", "OCEB": "0", "WEB": "0", "RSTB": "0", "CLKB": "0", "CLKA": 1}.items()}
            for side in "AB":
                for bit in range(3):
                    connections[f"CS{side}{bit}"] = ["0"]
                for bit in range(18):
                    connections[f"DI{side}{bit}"] = ["0"]
            for bit in range(14):
                connections[f"ADB{bit}"] = ["0"]
                connections[f"ADA{bit}"] = ["0" if bit < 3 else head[offset + bit - 3]]
            cells[prefix] = {"type": "DP16KD", "parameters": parameters, "connections": connections,
                             "port_directions": {port: "input" for port in connections}}
            nets[prefix + ".ADDR"] = {"bits": head[offset:]}
    netlist = root / "netlist.json"
    netlist.write_text(json.dumps({"modules": {"mkTop": {"cells": cells, "netnames": nets}}}))
    return netlist, reference, generated


class TestNativeROMCoverage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_default_still_requires_and_checks_all_44_banks(self):
        paths = native_fixture(self.root, False)
        result = check_native_rom(*paths)
        self.assertEqual(result["coefficient_banks_checked"], 44)
        self.assertEqual(result["command_address_registers_checked"], 121)
        self.assertFalse(result["resource_comparison"])

    def test_resource_mode_checks_exactly_36_banks_and_198_address_registers(self):
        paths = native_fixture(self.root, True)
        result = check_native_rom(*paths, resource_comparison=True)
        self.assertEqual(result["coefficient_banks_checked"], 36)
        self.assertEqual(result["command_address_registers_checked"], 198)
        self.assertEqual(result["non_native_coefficient_layers"], [3, 7])
        self.assertEqual(len(result["fifo2_address_proofs"]), 9)

    def test_36_banks_are_rejected_without_explicit_resource_mode(self):
        paths = native_fixture(self.root, True)
        with self.assertRaisesRegex(ValueError, "Native coefficient memories missing"):
            check_native_rom(*paths)

    def test_resource_mode_rejects_unexpected_native_delta_bank(self):
        paths = native_fixture(self.root, True)
        data = json.loads(paths[0].read_text())
        cells = data["modules"]["mkTop"]["cells"]
        cells[ENGINES[3] + "_weightR_0_raw"] = copy.deepcopy(cells[ENGINES[0] + "_weightR_0_raw"])
        paths[0].write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Unrecognized coefficient hierarchy"):
            check_native_rom(*paths, resource_comparison=True)

    def test_resource_mode_rejects_missing_non_delta_bank(self):
        paths = native_fixture(self.root, True)
        data = json.loads(paths[0].read_text())
        del data["modules"]["mkTop"]["cells"][ENGINES[10] + "_weightR_3_raw"]
        paths[0].write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Native coefficient memories missing"):
            check_native_rom(*paths, resource_comparison=True)

    def test_resource_mode_still_rejects_coefficient_corruption(self):
        paths = native_fixture(self.root, True)
        data = json.loads(paths[0].read_text())
        data["modules"]["mkTop"]["cells"][ENGINES[0] + "_weightR_0_raw"]["parameters"]["INITVAL_00"] = f"{1:0320b}"
        paths[0].write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "INIT mismatch"):
            check_native_rom(*paths, resource_comparison=True)

    def remove_folded_counter_alias(self, paths):
        data = json.loads(paths[0].read_text())
        module = data["modules"]["mkTop"]
        address = module["netnames"].pop(ENGINES[0] + "_addressCnt")["bits"]
        for index, bit in enumerate(address):
            module["cells"][f"folded_counter_{index}"] = {
                "type": "TRELLIS_FF", "parameters": {"CLKMUX": "CLK", "CEMUX": "CE", "LSRMUX": "LSR",
                    "GSR": "DISABLED", "REGSET": "RESET", "SRMODE": "LSR_OVER_CE"},
                "attributes": {"src": "mkTop.v:12.3-14.6|cells_map_trellis.v:83.1-83.4"},
                "connections": {"CLK": [1], "CE": [200000], "LSR": [200001 if index < 2 else "0"],
                                "DI": [200010 + index], "Q": [bit]},
                "port_directions": {"CLK": "input", "CE": "input", "LSR": "input", "DI": "input", "Q": "output"}}
        paths[0].write_text(json.dumps(data))
        return data

    def test_folded_counter_alias_removed_by_mapper_is_recovered(self):
        paths = native_fixture(self.root, True)
        self.remove_folded_counter_alias(paths)
        result = check_native_rom(*paths, resource_comparison=True)
        bank = result["banks"][0]
        self.assertFalse(bank["address_counter_alias_preserved"])
        self.assertEqual(len(bank["address_counter_alias_recovery"]["registers"]), 11)

    def test_folded_counter_recovery_rejects_wrong_clock_or_provenance(self):
        paths = native_fixture(self.root, True)
        data = self.remove_folded_counter_alias(paths)
        for field in ("clock", "source", "enable", "clear"):
            with self.subTest(field=field):
                mutated = copy.deepcopy(data)
                cell = mutated["modules"]["mkTop"]["cells"]["folded_counter_1"]
                if field == "source":
                    cell["attributes"]["src"] = "FIFO2.v:1.1-2.1"
                else:
                    cell["connections"][{"clock": "CLK", "enable": "CE", "clear": "LSR"}[field]] = [299999]
                paths[0].write_text(json.dumps(mutated))
                with self.assertRaises(ValueError):
                    check_native_rom(*paths, resource_comparison=True)

    def test_resource_mode_still_requires_complete_source_manifest(self):
        paths = native_fixture(self.root, True)
        data = json.loads(paths[1].read_text())
        data["linearROMBanks"]["banks"] = [bank for bank in data["linearROMBanks"]["banks"] if bank["layer"] not in (3, 7)]
        paths[1].write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "exactly 44 coefficient banks"):
            check_native_rom(*paths, resource_comparison=True)


if __name__ == "__main__":
    unittest.main()
