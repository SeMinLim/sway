#!/usr/bin/env python3
"""Audit all fixed coefficient DP16KD instances in a flattened Yosys netlist.

Checks the native initialization independently of the behavioral ROM model.
Other DP16KD memories are excluded by the coefficient instance hierarchy.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys


ENGINES = [
    "core_embedding_engine",
    "core_block0_inputProjection_engine", "core_block0_stateProjection_engine",
    "core_block0_deltaProjection_engine", "core_block0_outputProjection_engine",
    "core_block1_inputProjection_engine", "core_block1_stateProjection_engine",
    "core_block1_deltaProjection_engine", "core_block1_outputProjection_engine",
    "core_headHidden_engine", "core_headOutput_engine",
]


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def binary_parameter(parameters: dict, key: str, width: int | None = None) -> int:
    value = parameters.get(key)
    require(isinstance(value, str) and re.fullmatch(r"[01]+", value) is not None,
            f"Missing, unknown or malformed binary parameter {key}")
    if width is not None:
        require(len(value) == width, f"{key} must have {width} bits, found {len(value)}")
    return int(value, 2)


def one_connection(cell: dict, port: str, name: str):
    bits = cell.get("connections", {}).get(port)
    require(isinstance(bits, list) and len(bits) == 1, f"{name}.{port} must connect one bit")
    return bits[0]


def decode_initialization(parameters: dict) -> list[int]:
    words = [0] * 2048
    for block in range(64):
        value = binary_parameter(parameters, f"INITVAL_{block:02X}", 320)
        for row in range(16):
            slot = (value >> (row * 20)) & ((1 << 20) - 1)
            require(slot >> 18 == 0, f"Nonzero unused INITVAL slot bits at block {block}, row {row}")
            words[block * 32 + row * 2] = slot & 511
            words[block * 32 + row * 2 + 1] = (slot >> 9) & 511
    return words


def fifo2_address_path(cells: dict, nets: dict, drivers: dict, command_name: str,
                       address: list, actual_address: list, clock: int) -> dict:
    """Prove the FIFO2 address data equations, including mapped FF enables.

    The reference equations are the data0_reg/data1_reg assignments in BSC
    2026.01 FIFO2.v. With preserved aliases, all seven-input valuations are
    checked. Optimized control recovery also proves ENQ implies FULL_N and DEQ
    implies EMPTY_N, then checks every valuation satisfying those guards.
    Unsupported mapping primitives or ambiguous control recovery fail closed.
    When Yosys removes ENQ/DEQ aliases, their unique data-cone cut points must
    satisfy the same seven-input equations; no gate names or bit IDs are used.
    Recovered controls must also explain the actual occupancy FF transitions
    and reset values, so a data-only replacement control cannot be accepted.
    """
    def named_bits(suffix: str, width: int) -> list:
        bits = nets.get(command_name + suffix, {}).get("bits")
        require(isinstance(bits, list) and len(bits) == width,
                f"Missing {width}-bit FIFO2 net {command_name}{suffix}")
        return bits

    head = nets.get(command_name + ".data0_reg", {}).get("bits")
    if head is None:
        # Whole-design optimization names the head FFs after native ROM ADDR.
        # Their direct Q driver, FIFO2 origin, clock and data equations are
        # independently checked below; an optional source alias adds a check.
        head = [None] * 9 + actual_address
    else:
        require(isinstance(head, list) and len(head) == 20,
                f"Malformed FIFO2 head alias {command_name}.data0_reg")
    tail = named_bits(".data1_reg", 20)
    require(head[9:20] == actual_address,
            f"{command_name} FIFO2 head does not drive the native ROM address")
    occupancy = [named_bits("." + name, 1)[0] for name in ("FULL_N", "EMPTY_N")]
    aliases = [nets.get(command_name + "." + name, {}).get("bits") for name in ("ENQ", "DEQ")]
    require(all(bits is None or isinstance(bits, list) and len(bits) == 1 for bits in aliases),
            f"Malformed FIFO2 ENQ/DEQ aliases in {command_name}")
    proven_cells = set()
    register_names = []
    head_enables = []
    tail_enables = []

    def data_register(bit: int, role: str) -> tuple[str, dict]:
        sources = drivers.get(bit, [])
        require(len(sources) == 1, f"{command_name} {role} must have exactly one driver")
        name, port = sources[0]
        cell = cells[name]
        require(cell.get("type") == "TRELLIS_FF" and port == "Q",
                f"{name} must be a direct TRELLIS_FF Q output")
        require("FIFO2.v:" in cell.get("attributes", {}).get("src", ""),
                f"{name} is not a mapped FIFO2 data register")
        parameters = cell.get("parameters", {})
        for parameter, expected in {"CLKMUX": "CLK", "LSRMUX": "LSR", "GSR": "DISABLED"}.items():
            require(parameters.get(parameter) == expected, f"{name}.{parameter} must be {expected}")
        # Yosys JSON appends a space to distinguish the string "1" from bits.
        require(parameters.get("CEMUX") in ("CE", "1", "1 "), f"{name}.CEMUX must be CE or 1")
        require(one_connection(cell, "CLK", name) == clock, f"{name}.CLK does not match the expected core clock")
        require(one_connection(cell, "LSR", name) == "0", f"{name} data reset must be disabled")
        proven_cells.add(name)
        return name, cell

    def enable_bit(name: str, cell: dict):
        if cell["parameters"]["CEMUX"] in ("1", "1 "):
            return "1"
        return one_connection(cell, "CE", name)

    def combinational(bit, values: dict, active: set):
        if bit in ("0", "1"):
            return int(bit)
        if bit in values:
            return values[bit]
        require(type(bit) is int and bit not in active,
                f"{command_name} unknown bit or cycle in FIFO2 address logic")
        sources = drivers.get(bit, [])
        require(len(sources) == 1, f"{command_name} unproven FIFO2 address input {bit}")
        name, port = sources[0]
        cell = cells[name]
        kind = cell.get("type")
        require(port == "Z" and kind in ("LUT4", "PFUMX", "L6MUX21"),
                f"{name} unsupported FIFO2 address logic {cell.get('type')}.{port}")
        inputs = {"LUT4": ("A", "B", "C", "D"), "PFUMX": ("BLUT", "ALUT", "C0"),
                  "L6MUX21": ("D0", "D1", "SD")}[kind]
        require(set(cell.get("connections", {})) == set(inputs) | {"Z"},
                f"{name} malformed {kind} ports")
        require(cell.get("port_directions") == {**{p: "input" for p in inputs}, "Z": "output"},
                f"{name} malformed {kind} directions")
        active.add(bit)
        operands = [combinational(one_connection(cell, p, name), values, active) for p in inputs]
        active.remove(bit)
        proven_cells.add(name)
        if kind == "LUT4":
            table = binary_parameter(cell.get("parameters", {}), "INIT", 16)
            index = sum(value << shift for shift, value in enumerate(operands))
            values[bit] = (table >> index) & 1
        else:
            # Yosys ecp5/common_sim.vh: PFUMX=C0?ALUT:BLUT;
            # L6MUX21=SD?D1:D0. Both input cones are proven above.
            require(not cell.get("parameters", {}), f"{name} unexpected {kind} parameters")
            values[bit] = operands[1] if operands[2] else operands[0]
        return values[bit]

    def prove_bit(index: int, controls: list, guarded: bool = False):
        head_name, head_cell = data_register(head[index + 9], "head")
        tail_name, tail_cell = data_register(tail[index + 9], "tail")
        require(one_connection(tail_cell, "DI", tail_name) == address[index],
                f"{tail_name}.DI does not match the address counter bit {index}")
        leaves = [address[index], head[index + 9], tail[index + 9], *controls]
        require(all(type(bit) is int for bit in leaves) and len(set(leaves)) == 7,
                f"{command_name} address bit {index} aliases unrelated FIFO2 data/control")
        for data, old_head, old_tail, enq, deq, not_full, not_empty in itertools.product((0, 1), repeat=7):
            if guarded and ((enq and not not_full) or (deq and not not_empty)):
                continue
            values = dict(zip(leaves, (data, old_head, old_tail, enq, deq, not_full, not_empty)))
            d0di = (enq and not not_empty) or (enq and deq and not_full)
            d0d1 = deq and not not_full
            d0h = ((not deq) and (not enq)) or ((not deq) and not_empty) or ((not enq) and not_full)
            expected_head = int((d0di and data) or (d0d1 and old_tail) or (d0h and old_head))
            expected_tail = data if enq and not_empty else old_tail
            for name, cell, previous, expected in ((head_name, head_cell, old_head, expected_head),
                                                     (tail_name, tail_cell, old_tail, expected_tail)):
                enable = combinational(enable_bit(name, cell), values, set())
                # Check the DI cone even when CE is false, so hidden unproven
                # signals cannot escape provenance checks through a dead branch.
                incoming = combinational(one_connection(cell, "DI", name), values, set())
                actual = incoming if enable else previous
                require(actual == expected, f"{name} FIFO2 next-state mismatch for address bit {index}, inputs={tuple(values[b] for b in leaves)}")
        return head_name, tail_name, enable_bit(head_name, head_cell), enable_bit(tail_name, tail_cell)

    def proves_guard(control: int, guard: int) -> bool:
        # A conservative Boolean-domain evaluation: every unproven input can
        # independently be 0 or 1. A singleton zero output therefore proves the
        # implication even when the upstream controller cone is not audited.
        memo = {guard: {0}}
        active = set()

        def domain(bit):
            if bit in ("0", "1"):
                return {int(bit)}
            if bit in memo:
                return memo[bit]
            if bit in active or len(drivers.get(bit, [])) != 1:
                return {0, 1}
            name, port = drivers[bit][0]
            cell = cells[name]
            kind = cell.get("type")
            inputs = {"LUT4": ("A", "B", "C", "D"), "PFUMX": ("BLUT", "ALUT", "C0"),
                      "L6MUX21": ("D0", "D1", "SD")}.get(kind)
            if port != "Z" or inputs is None:
                return {0, 1}
            require(set(cell.get("connections", {})) == set(inputs) | {"Z"},
                    f"{name} malformed guard logic ports")
            require(cell.get("port_directions") == {**{p: "input" for p in inputs}, "Z": "output"},
                    f"{name} malformed guard logic directions")
            active.add(bit)
            combinations = itertools.product(*(domain(one_connection(cell, p, name)) for p in inputs))
            if kind == "LUT4":
                table = binary_parameter(cell.get("parameters", {}), "INIT", 16)
                result = {(table >> sum(value << shift for shift, value in enumerate(values))) & 1
                          for values in combinations}
            else:
                require(not cell.get("parameters", {}), f"{name} unexpected guard mux parameters")
                result = {values[1] if values[2] else values[0] for values in combinations}
            active.remove(bit)
            memo[bit] = result
            return result

        return domain(control) == {0}

    def prove_occupancy(controls: list) -> dict:
        registers = []
        resets = []
        for bit, reset_value in zip(occupancy, ("SET", "RESET")):
            sources = drivers.get(bit, [])
            require(len(sources) == 1, f"{command_name} occupancy must have exactly one driver")
            name, port = sources[0]
            cell = cells[name]
            require(cell.get("type") == "TRELLIS_FF" and port == "Q",
                    f"{name} occupancy must be a direct TRELLIS_FF Q output")
            require("FIFO2.v:" in cell.get("attributes", {}).get("src", ""),
                    f"{name} is not a mapped FIFO2 occupancy register")
            parameters = cell.get("parameters", {})
            for parameter, expected in {"CLKMUX": "CLK", "LSRMUX": "LSR", "GSR": "DISABLED",
                                        "REGSET": reset_value}.items():
                require(parameters.get(parameter) == expected, f"{name}.{parameter} must be {expected}")
            require(parameters.get("SRMODE", "LSR_OVER_CE") == "LSR_OVER_CE",
                    f"{name} occupancy reset must be synchronous with reset priority")
            require(parameters.get("LSRMODE", "LSR") == "LSR", f"{name} unsupported occupancy preload")
            require(parameters.get("CEMUX") in ("CE", "1", "1 "), f"{name} unsupported occupancy enable")
            require(one_connection(cell, "CLK", name) == clock, f"{name}.CLK does not match the expected core clock")
            resets.append(one_connection(cell, "LSR", name))
            registers.append((name, cell))
            proven_cells.add(name)
        require(resets[0] == resets[1] and type(resets[0]) is int and resets[0] not in controls,
                f"{command_name} occupancy registers must share one independent reset signal")
        for enq, deq, not_full, not_empty in itertools.product((0, 1), repeat=4):
            if (enq and not not_full) or (deq and not not_empty):
                continue
            values = {**dict(zip(controls, (enq, deq, not_full, not_empty))), resets[0]: 0}
            expected_full = int(not not_empty) if enq and not deq else 1 if deq and not enq else not_full
            expected_empty = 1 if enq and not deq else int(not not_full) if deq and not enq else not_empty
            for (name, cell), previous, expected in zip(registers, (not_full, not_empty), (expected_full, expected_empty)):
                enable = combinational(enable_bit(name, cell), values, set())
                incoming = combinational(one_connection(cell, "DI", name), values, set())
                require((incoming if enable else previous) == expected,
                        f"{name} recovered control does not match FIFO2 occupancy transition")
        return {"registers": [name for name, _ in registers], "reset_bit": resets[0],
                "transition_assignments_checked": 9}

    def cone_candidates(bit, stops: set, found: set):
        if type(bit) is not int or bit in stops or bit in found:
            return
        found.add(bit)
        sources = drivers.get(bit, [])
        if len(sources) != 1:
            return
        name, port = sources[0]
        cell = cells[name]
        if port != "Z" or cell.get("type") not in ("LUT4", "PFUMX", "L6MUX21"):
            return
        for port, direction in cell.get("port_directions", {}).items():
            if direction == "input":
                cone_candidates(one_connection(cell, port, name), stops, found)

    recovered = not all(bits is not None for bits in aliases)
    if recovered:
        # ENQ is independently constrained by tail.CE = ENQ && EMPTY_N.
        # Find the unique missing DEQ cut by proving the complete first-bit
        # data equations, then prove that same control pair for all 11 bits.
        tail_name, tail_cell = data_register(tail[9], "tail")
        tail_ce = enable_bit(tail_name, tail_cell)
        enq_candidates = set()
        cone_candidates(tail_ce, set(occupancy), enq_candidates)
        if aliases[0] is not None:
            enq_candidates = {aliases[0][0]}
        enqueues = []
        for candidate in sorted(enq_candidates):
            saved = proven_cells.copy()
            try:
                for enq, not_empty in itertools.product((0, 1), repeat=2):
                    require(combinational(tail_ce, {candidate: enq, occupancy[1]: not_empty}, set()) == (enq and not_empty),
                            "Candidate is not FIFO2 enqueue")
                enqueues.append(candidate)
            except ValueError:
                pass
            proven_cells.clear()
            proven_cells.update(saved)
        require(len(enqueues) == 1, f"{command_name} requires one uniquely proven FIFO2 enqueue control")
        enq = enqueues[0]
        head_name, head_cell = data_register(head[9], "head")
        deq_candidates = set()
        stops = {address[0], head[9], tail[9], enq, *occupancy}
        for bit in (one_connection(head_cell, "DI", head_name), enable_bit(head_name, head_cell)):
            cone_candidates(bit, stops, deq_candidates)
        if aliases[1] is not None:
            deq_candidates = {aliases[1][0]}
        dequeues = []
        for candidate in sorted(deq_candidates):
            saved = proven_cells.copy()
            try:
                require(proves_guard(enq, occupancy[0]) and proves_guard(candidate, occupancy[1]),
                        "Recovered FIFO2 controls do not imply their occupancy guards")
                prove_bit(0, [enq, candidate, *occupancy], guarded=True)
                prove_occupancy([enq, candidate, *occupancy])
                dequeues.append(candidate)
            except ValueError:
                pass
            proven_cells.clear()
            proven_cells.update(saved)
        require(len(dequeues) == 1, f"{command_name} requires one uniquely proven FIFO2 dequeue control")
        controls = [enq, dequeues[0], *occupancy]
    else:
        controls = [aliases[0][0], aliases[1][0], *occupancy]
    require(all(type(bit) is int for bit in controls) and len(set(controls)) == 4,
            f"{command_name} FIFO2 controls must be four distinct signals")
    occupancy_proof = prove_occupancy(controls) if recovered else None
    for index in range(11):
        head_name, tail_name, head_enable, tail_enable = prove_bit(index, controls, guarded=recovered)
        register_names.extend((head_name, tail_name))
        head_enables.append(head_enable)
        tail_enables.append(tail_enable)
    require(len(set(register_names)) == 22, f"{command_name} requires exactly 22 distinct address data registers")
    require(len(set(head_enables)) == 1 and len(set(tail_enables)) == 1,
            f"{command_name} FIFO2 address register enables differ within one queue slot")
    return {"registers": register_names, "proven_cells": proven_cells,
            "head_enable": head_enables[0], "tail_enable": tail_enables[0],
            "control_bits": dict(zip(("ENQ", "DEQ", "FULL_N", "EMPTY_N"), controls)),
            "control_aliases_recovered": recovered,
            "occupancy_guard_implications_proven": recovered,
            "occupancy_identity_proof": occupancy_proof,
            "truth_assignments_checked": 11 * (72 if recovered else 128),
            "mapping": "command.D_IN19:9 = addressCnt10:0; FIFO2 head/tail next-state truth tables; head.Q=ADA13:3; ADA2:0=0"}


def check_native_rom(netlist_path: Path, reference_path: Path, generated: Path, top: str = "mkTop",
                     clock_net: str = "clocks_coreReset.CLK", input_projection_fifo2: bool = False) -> dict:
    netlist = read_json(netlist_path)
    reference = read_json(reference_path)
    modules = netlist.get("modules", {})
    require(isinstance(modules, dict) and top in modules, f"Missing flattened top module {top}")
    module = modules[top]
    cells, nets = module.get("cells"), module.get("netnames")
    require(isinstance(cells, dict) and isinstance(nets, dict), "Input is not a raw Yosys netlist with cells and netnames")
    expected_clock = nets.get(clock_net, {}).get("bits")
    require(isinstance(expected_clock, list) and len(expected_clock) == 1 and type(expected_clock[0]) is int,
            f"Missing expected core clock net {clock_net}")
    generated_hashes = reference.get("generatedSHA256", {})
    bank_metadata = reference.get("linearROMBanks", {}).get("banks", [])
    require(isinstance(bank_metadata, list) and len(bank_metadata) == 44, "Reference must describe exactly 44 coefficient banks")
    expected_metadata = {}
    for bank in bank_metadata:
        require(isinstance(bank, dict), "Malformed coefficient bank metadata")
        key = (bank.get("layer"), bank.get("lane"))
        require(key not in expected_metadata, f"Duplicate source bank {key}")
        expected_metadata[key] = bank
    require(set(expected_metadata) == {(layer, lane) for layer in range(11) for lane in range(4)},
            "Reference coefficient layer/lane coverage is incomplete")

    native_file = reference.get("linearROMBanks", {}).get("nativeRegisteredROM", {}).get("file")
    require(native_file == "SwayCoeffRomInit.vh", "Reference lacks the registered coefficient initialization artifact")
    require(generated_hashes.get(native_file) == file_hash(generated / native_file), "Native initialization artifact hash mismatch")

    expected_prefixes = {f"{engine}_weightR_{lane}_raw": (layer, lane)
                         for layer, engine in enumerate(ENGINES) for lane in range(4)}
    matched = {}
    excluded = []
    coefficient_alias_cells = set()
    drivers = {}
    for name, cell in cells.items():
        for port, direction in cell.get("port_directions", {}).items():
            if direction == "output":
                for bit in cell.get("connections", {}).get(port, []):
                    if type(bit) is int:
                        drivers.setdefault(bit, []).append((name, port))
        prefix = next((prefix for prefix in expected_prefixes if name == prefix or name.startswith(prefix + ".")), None)
        is_coefficient = re.search(r"_weightR_[0-9]+_raw(?:\.|$)", name) is not None
        if prefix is not None:
            if cell.get("type") == "DP16KD":
                key = expected_prefixes[prefix]
                require(key not in matched, f"Multiple native memories for coefficient bank {key}")
                matched[key] = (name, cell, prefix)
            else:
                # Yosys can name command FIFO flops after the shared native ADDR
                # alias. Every such cell must be proven an address flop below.
                coefficient_alias_cells.add(name)
        elif is_coefficient:
            raise ValueError(f"Unrecognized coefficient hierarchy: {name}")
        elif cell.get("type") == "DP16KD":
            excluded.append(name)
    require(set(matched) == set(expected_metadata),
            f"Native coefficient memories missing: {sorted(set(expected_metadata) - set(matched))}")

    results = []
    clocks = set()
    address_registers = set()
    proven_address_cells = set()
    fifo2_engines = {}
    engine_enables = {}
    for key in sorted(matched):
        layer, lane = key
        name, cell, prefix = matched[key]
        parameters = cell.get("parameters", {})
        # DP16KD pin mux parameters can invert clocks or override tied pins.
        for port in cell.get("connections", {}):
            require(parameters.get(port + "MUX", port) == port, f"{name}.{port}MUX must pass through {port}")
        for mode in ("REGMODE_A", "REGMODE_B"):
            require(parameters.get(mode) == "OUTREG", f"{name}.{mode} must be OUTREG")
        for width in ("DATA_WIDTH_A", "DATA_WIDTH_B"):
            require(binary_parameter(parameters, width) == 9, f"{name}.{width} must be 9")
        require(parameters.get("GSR") == "DISABLED", f"{name}.GSR must be DISABLED")
        for side in "AB":
            require(parameters.get(f"CSDECODE_{side}") == "0b000", f"{name}.CSDECODE_{side} must be 0b000")
        for port, constant in {"CEA": "1", "OCEA": "1", "WEA": "0", "RSTA": "0",
                               "CEB": "0", "OCEB": "0", "WEB": "0", "RSTB": "0", "CLKB": "0"}.items():
            require(one_connection(cell, port, name) == constant, f"{name}.{port} must be constant {constant}")
        for side in "AB":
            for bit in range(3):
                require(one_connection(cell, f"CS{side}{bit}", name) == "0", f"{name} chip select must be zero")
            for bit in range(18):
                require(one_connection(cell, f"DI{side}{bit}", name) == "0", f"{name} write data must be zero")
        for bit in range(14):
            require(one_connection(cell, f"ADB{bit}", name) == "0", f"{name} unused port B address must be zero")
        for bit in range(3):
            require(one_connection(cell, f"ADA{bit}", name) == "0", f"{name} x9 address low bits must be zero")
        clock = one_connection(cell, "CLKA", name)
        require(type(clock) is int, f"{name}.CLKA must be a connected clock signal")
        require(clock == expected_clock[0], f"{name}.CLKA does not match expected core clock {clock_net}")
        clocks.add(clock)

        address_name = ENGINES[layer] + "_addressCnt"
        address = nets.get(address_name, {}).get("bits")
        require(isinstance(address, list) and len(address) >= 11, f"Missing address counter net {address_name}")
        command_name = ENGINES[layer] + "_issueCommandQ"
        command_input = nets.get(command_name + ".D_IN", {}).get("bits")
        command_output = nets.get(command_name + ".D_OUT", {}).get("bits")
        require(isinstance(command_input, list) and len(command_input) == 20,
                f"Missing 20-bit command FIFO input {command_name}.D_IN")
        # Tuple3(Bit11 address, Int8 x, Bool last) packs address in bits 19:9.
        require(command_input[9:20] == address[:11], f"{command_name} address input does not match {address_name}[10:0]")
        actual_address = [one_connection(cell, f"ADA{bit + 3}", name) for bit in range(11)]
        if command_output is not None:
            require(isinstance(command_output, list) and len(command_output) == 20,
                    f"Malformed command FIFO output {command_name}.D_OUT")
            require(actual_address == command_output[9:20], f"{name} ADA13:3 does not match {command_name}.D_OUT[19:9]")
        address_cells = []
        fifo2 = input_projection_fifo2 and layer in (1, 5)
        if fifo2:
            proof = fifo2_address_path(cells, nets, drivers, command_name, address[:11], actual_address, expected_clock[0])
            address_cells = proof["registers"]
            address_registers.update(address_cells)
            proven_address_cells.update(proof["proven_cells"])
            engine_enables[layer] = proof["head_enable"]
            fifo2_engines[layer] = {"engine": ENGINES[layer], "address_registers_checked": len(address_cells),
                                    "truth_assignments_checked": proof["truth_assignments_checked"],
                                    "control_bits": proof["control_bits"],
                                    "control_aliases_recovered": proof["control_aliases_recovered"],
                                    "occupancy_guard_implications_proven": proof["occupancy_guard_implications_proven"],
                                    "occupancy_identity_proof": proof["occupancy_identity_proof"],
                                    "tail_register_enable_bit": proof["tail_enable"]}
        for bit, output_bit in enumerate(actual_address if not fifo2 else []):
            output_drivers = drivers.get(output_bit, [])
            require(len(output_drivers) == 1, f"{name}.ADA{bit + 3} must have exactly one register driver")
            register_name, register_port = output_drivers[0]
            register = cells[register_name]
            require(register.get("type") == "TRELLIS_FF" and register_port == "Q",
                    f"{name}.ADA{bit + 3} is not a direct TRELLIS_FF Q output")
            require("FIFO1.v:" in register.get("attributes", {}).get("src", ""),
                    f"{register_name} is not a mapped FIFO1 data register")
            register_parameters = register.get("parameters", {})
            for parameter, expected in {"CLKMUX": "CLK", "CEMUX": "CE", "LSRMUX": "LSR", "GSR": "DISABLED"}.items():
                require(register_parameters.get(parameter) == expected, f"{register_name}.{parameter} must be {expected}")
            require(one_connection(register, "DI", register_name) == address[bit],
                    f"{register_name}.DI does not match {address_name}[{bit}]")
            require(one_connection(register, "CLK", register_name) == expected_clock[0],
                    f"{register_name}.CLK does not match the expected core clock")
            require(one_connection(register, "LSR", register_name) == "0", f"{register_name} data reset must be disabled")
            enable = one_connection(register, "CE", register_name)
            require(type(enable) is int, f"{register_name}.CE must use the FIFO enqueue signal")
            require(engine_enables.setdefault(layer, enable) == enable, f"{command_name} address register enables differ")
            address_registers.add(register_name)
            proven_address_cells.add(register_name)
            address_cells.append(register_name)
        alias = nets.get(prefix + ".ADDR", {}).get("bits")
        if alias is not None:
            require(alias == actual_address, f"{name} native ADDR alias mismatch")

        bank = expected_metadata[key]
        filename = f"linear_l{layer}_lane{lane}.hex"
        require(bank.get("file") == filename, f"Source bank filename mismatch for {key}")
        path = generated / filename
        digest = file_hash(path)
        require(bank.get("sha256") == digest and generated_hashes.get(filename) == digest,
                f"Source bank hash mismatch: {filename}")
        lines = path.read_text().splitlines()
        require(all(re.fullmatch(r"[0-9a-fA-F]{2}", line) is not None for line in lines), f"Invalid INT8 hex: {filename}")
        expected = [int(line, 16) for line in lines]
        require(len(expected) == bank.get("depth") and 0 < len(expected) <= 2048, f"Source bank depth mismatch: {filename}")
        expected += [0] * (2048 - len(expected))
        decoded = decode_initialization(parameters)
        if decoded != expected:
            index = next(index for index, pair in enumerate(zip(decoded, expected)) if pair[0] != pair[1])
            raise ValueError(f"{name} INIT mismatch at address {index}: {decoded[index]} != {expected[index]}")
        results.append({"layer": layer, "lane": lane, "cell": name, "source": filename,
                        "source_sha256": digest, "source_words": bank["depth"], "padded_words_checked": 2048,
                        "decoded_bytes_sha256": hashlib.sha256(bytes(decoded)).hexdigest(),
                        "address_counter": address_name, "command_fifo": command_name,
                        "address_register_cells": address_cells, "address_register_enable_bit": engine_enables[layer],
                        "address_output_alias_preserved": command_output is not None,
                        "address_mapping_checked": proof["mapping"] if fifo2 else "command.D_IN19:9 = addressCnt10:0; FIFO1 FF DI=counter, Q=ADA13:3; ADA2:0=0"})
    require(len(clocks) == 1, "Coefficient banks do not share one clock signal")
    require(not coefficient_alias_cells - proven_address_cells,
            f"Unproven cells in coefficient hierarchy: {sorted(coefficient_alias_cells - proven_address_cells)}")
    expected_registers = 143 if input_projection_fifo2 else 121
    require(len(address_registers) == expected_registers,
            f"Expected exactly {expected_registers} shared command address registers")
    return {"status": "pass", "evidence": "raw flattened Yosys native coefficient memory netlist audit",
            "top": top, "netlist": str(netlist_path), "netlist_sha256": file_hash(netlist_path),
            "reference_report_sha256": file_hash(reference_path), "checker_sha256": file_hash(Path(__file__)),
            "native_header_sha256": generated_hashes[native_file], "coefficient_banks_checked": len(results),
            "padded_words_checked": len(results) * 2048, "initval_parameters_checked": len(results) * 64,
            "output_mode": "OUTREG", "port_a_clock_enable": 1, "port_a_output_enable": 1,
            "write_enabled_banks": 0, "shared_clock_net_bit": next(iter(clocks)),
            "expected_clock_net": clock_net, "pin_muxes": "default passthrough",
            "command_address_registers_checked": len(address_registers),
            "input_projection_fifo2": input_projection_fifo2,
            "fifo2_address_proofs": [fifo2_engines[layer] for layer in sorted(fifo2_engines)],
            "coefficient_alias_registers_checked": sorted(coefficient_alias_cells & address_registers),
            "coefficient_alias_logic_cells_checked": sorted(coefficient_alias_cells - address_registers),
            "other_dp16kd_excluded": excluded, "banks": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--top", default="mkTop")
    parser.add_argument("--clock-net", default="clocks_coreReset.CLK")
    parser.add_argument("--input-projection-fifo2", action="store_true",
                        help="Require FIFO2 address paths only in input-projection layers 1 and 5")
    parser.add_argument("--reference", type=Path, default=Path("generated/reference_report.json"))
    parser.add_argument("--generated", type=Path, default=Path("generated"))
    parser.add_argument("--output", type=Path, default=Path("results/native_rom.json"))
    args = parser.parse_args()
    args.output.unlink(missing_ok=True)
    try:
        result = check_native_rom(args.netlist, args.reference, args.generated, args.top, args.clock_net,
                                  args.input_projection_fifo2)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {"status": "fail", "error": str(error), "netlist": str(args.netlist),
                  "checker_sha256": file_hash(Path(__file__))}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(f"SWAY_NATIVE_ROM_FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"SWAY_NATIVE_ROM_PASS banks={result['coefficient_banks_checked']} words={result['padded_words_checked']}")


if __name__ == "__main__":
    main()
