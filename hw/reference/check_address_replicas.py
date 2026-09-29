#!/usr/bin/env python3
"""Independently validate same-cycle headHidden address-register replicas."""
from collections import Counter, defaultdict
from copy import deepcopy
import re

ENGINE = "main_core_headHidden_engine"
PREFIX = ENGINE + "_addressReplica_"
GROUPS = 8
WIDTH = 13
LUT_TYPES = {"LUT4", "PFUMX", "L6MUX21"}
COEFFICIENT_TYPES = LUT_TYPES | {"CCU2C"}
FF_PARAMETERS = {"CEMUX": "CE", "CLKMUX": "CLK", "GSR": "DISABLED", "LSRMUX": "LSR",
                 "REGSET": "RESET", "SRMODE": "LSR_OVER_CE"}


def connectivity(module):
    drivers, users = defaultdict(list), defaultdict(list)
    for name, cell in module["cells"].items():
        for port, direction in cell["port_directions"].items():
            if direction not in {"input", "output"}:
                raise RuntimeError("Unexpected bidirectional mapped-cell port")
            for index, bit in enumerate(cell["connections"][port]):
                if type(bit) is int:
                    (drivers if direction == "output" else users)[bit].append((name, port, index))
    return drivers, users


def coefficient_owners(module, aliases):
    """Lowest output owns shared logic; carry and its fan-in stay canonical."""
    cells, nets = module["cells"], module["netnames"]
    drivers, _ = connectivity(module)
    leaves = set(nets[ENGINE + "_addressR"]["bits"]) | set(aliases)
    owners, pending = {}, set()
    def visit(bit, group):
        if bit in leaves or bit in {"0", "1"}:
            return
        if type(bit) is not int or len(drivers[bit]) != 1:
            raise RuntimeError("Undefined or multiply driven coefficient input")
        name, _, _ = drivers[bit][0]
        if name in pending:
            raise RuntimeError("Coefficient combinational cycle")
        if name in owners:
            return
        cell = cells[name]
        if cell["type"] not in COEFFICIENT_TYPES:
            raise RuntimeError("Foreign register or primitive in coefficient cone: " + name)
        owners[name] = group
        pending.add(name)
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                for source in cell["connections"][port]:
                    visit(source, group)
        pending.remove(name)
    outputs = nets[ENGINE + "_operandQ_D_IN"]["bits"]
    if len(outputs) != 17:
        raise RuntimeError("Unexpected coefficient tuple width")
    for group, output in enumerate(outputs[1:9]):
        visit(output, group)
    if not owners:
        raise RuntimeError("Missing coefficient output groups")
    for name in protected_carry_cells(module, owners, aliases):
        owners[name] = 0
    return owners


def protected_carry_cells(module, owners, aliases):
    """Preserve every coefficient carry and its complete combinational fan-in.

    A mapped carry can feed both coefficient decoding and canonical address
    next-state logic. Accepting it in backward discovery does not authorize
    substituting replica Q into that shared state-update cone.
    """
    cells, nets = module["cells"], module["netnames"]
    drivers, _ = connectivity(module)
    leaves = set(nets[ENGINE + "_addressR"]["bits"]) | set(aliases)
    protected, pending = set(), set()

    def protect(name):
        if name in pending:
            raise RuntimeError("Shared-carry combinational fan-in cycle")
        if name in protected:
            return
        if name not in owners or cells[name]["type"] not in COEFFICIENT_TYPES:
            raise RuntimeError("Shared-carry fan-in escapes the coefficient cone")
        pending.add(name)
        for port, direction in cells[name]["port_directions"].items():
            if direction == "input":
                for wire in cells[name]["connections"][port]:
                    if wire in leaves or wire in {"0", "1"}:
                        continue
                    if type(wire) is not int or len(drivers[wire]) != 1:
                        raise RuntimeError("Undefined or multiply driven shared-carry fan-in")
                    protect(drivers[wire][0][0])
        pending.remove(name)
        protected.add(name)

    for name in owners:
        if cells[name]["type"] == "CCU2C":
            protect(name)
    return protected


def initialization_proof(module, rtl, canonical):
    """Check the predicates used in the initialization/induction argument."""
    stripped = re.sub(r"//[^\n]*|/\*.*?\*/", "", rtl, flags=re.S)
    assignments = {name: re.sub(r"\s+", "", value)
                   for name, value in re.findall(r"\bassign\s+(\w+)\s*=\s*(.*?);", stripped, re.S)}
    first = "WILL_FIRE_RL_" + ENGINE + "_process1"
    consume = "WILL_FIRE_RL_" + ENGINE + "_process2_1"
    done = "MUX_" + ENGINE + "_activeOn_write_1__SEL_1"
    expected = {
        first: ENGINE + "_inputQ_EMPTY_N&&!" + ENGINE + "_activeOn",
        consume: ENGINE + "_bankQ_EMPTY_N&&" + ENGINE + "_operandQ_FULL_N&&" + ENGINE + "_activeOn",
        ENGINE + "_operandQ_ENQ": consume,
        ENGINE + "_addressR_EN": consume + "||" + first,
        ENGINE + "_addressR_D_IN": consume + "?MUX_" + ENGINE + "_addressR_write_1__VAL_1:13'd0",
        "MUX_" + ENGINE + "_addressR_write_1__VAL_1": ENGINE + "_addressR+13'd1",
        done: "WILL_FIRE_RL_" + ENGINE + "_process4_2&&" + ENGINE + "_affineQ_D_OUT[0]",
        ENGINE + "_activeOn_EN": "WILL_FIRE_RL_" + ENGINE + "_process4_2&&" + ENGINE + "_affineQ_D_OUT[0]||" + first,
        ENGINE + "_activeOn_D_IN": "!" + done,
    }
    for name, value in expected.items():
        if assignments.get(name) != value:
            raise RuntimeError("Address initialization predicate changed: " + name)
    # Inspect only the core-clock sequential block's reset branch, not BSC's
    # synthesis-excluded initialization block (addressR is mkRegU).
    reset_prefix = (r"always\s*@\s*\(\s*posedge\s+clocks_pll_clk_100mhz\s*\)\s*begin\s*"
                    r"if\s*\(\s*clocks_coreReset_OUT_RST\s*==\s*`BSV_RESET_VALUE\s*\)\s*begin")
    match = re.search(reset_prefix, stripped)
    if not match:
        raise RuntimeError("Missing core-clock reset branch")
    reset_body = re.split(r"\bend\b", stripped[match.end():], maxsplit=1)[0]
    active_reset = re.escape(ENGINE) + r"_activeOn\s*<=\s*`BSV_ASSIGNMENT_DELAY\s*1'd0\s*;"
    if len(re.findall(active_reset, reset_body)) != 1:
        raise RuntimeError("activeOn must reset to False before coefficient observation")
    nets = module["netnames"]
    for cell in canonical:
        if cell["connections"]["LSR"] != nets[first]["bits"]:
            raise RuntimeError("Address FF synchronous clear must be exactly process1")
        if cell["connections"]["CE"] != nets[ENGINE + "_addressR_EN"]["bits"]:
            raise RuntimeError("Address FF enable differs from source update enable")
        if cell["connections"]["CLK"] != nets["clocks_pll_clk_100mhz"]["bits"]:
            raise RuntimeError("Address FF must use the original core clock")
    return {"status": "pass", "arbitrary_initial_address_state_allowed": True,
            "base_case": "activeOn resets False; process1 atomically clears original and replicas before setting activeOn; process2_1 requires pre-edge activeOn and cannot observe this initialization edge",
            "induction": "Exact equal D, CE, CLK, LSR and FF parameters preserve equality through every update and hold after process1",
            "observation": "Only coefficient combinational LUT pins use replica Q; operandQ enqueue is exactly process2_1, which requires activeOn",
            "reset": "Warm reset clears activeOn, so process1 must initialize all copies before observation resumes",
            "relies_on_mkRegU_powerup_value": False, "extra_weight_pipeline_cycles": 0}


def observation_boundary(module, aliases, drivers, users):
    """Follow all replica influence, including fused FIFO head capture muxes."""
    cells, nets = module["cells"], module["netnames"]
    allowed = set()
    for stage in ("data0", "data1"):
        bits = nets[ENGINE + "_operandQ." + stage + "_reg"]["bits"]
        if len(bits) != 17:
            raise RuntimeError("Unexpected operand FIFO storage width")
        for wire in bits[1:9]:
            if len(drivers[wire]) != 1 or drivers[wire][0][1:] != ("Q", 0):
                raise RuntimeError("Coefficient FIFO storage is not a unique FF Q")
            name = drivers[wire][0][0]
            if cells[name]["type"] != "TRELLIS_FF":
                raise RuntimeError("Coefficient FIFO storage is not a register")
            allowed.add(name)
    if len(allowed) != 16:
        raise RuntimeError("Expected sixteen distinct coefficient FIFO storage FFs")
    exposed_ports = {wire for port in module["ports"].values() for wire in port["bits"]}
    visited, pending, endpoints = set(), set(), set()
    def walk(wire):
        if wire in exposed_ports:
            raise RuntimeError("Address replica influence escapes through a module port")
        if wire in pending:
            raise RuntimeError("Combinational cycle after an address replica")
        if wire in visited:
            return
        if not users[wire]:
            raise RuntimeError("Address replica influence has an unobserved dangling branch")
        pending.add(wire)
        for name, port, _ in users[wire]:
            cell = cells[name]
            if cell["type"] == "TRELLIS_FF":
                if name not in allowed or port != "DI":
                    raise RuntimeError("Address replica influence escapes coefficient FIFO data: " + name + "." + port)
                endpoints.add(name)
            elif cell["type"] in LUT_TYPES:
                for output, direction in cell["port_directions"].items():
                    if direction == "output":
                        for bit in cell["connections"][output]:
                            walk(bit)
            else:
                raise RuntimeError("Foreign primitive after address replica: " + name)
        pending.remove(wire)
        visited.add(wire)
    for wire in aliases:
        walk(wire)
    if not endpoints:
        raise RuntimeError("Address replicas do not reach any coefficient FIFO data FFs")
    return {"status": "pass", "first_sequential_sinks": sorted(endpoints), "sink_port": "DI",
            "operand_fifo_entries": 2, "coefficient_bits_per_entry": 8,
            "allowed_coefficient_ff_count": len(allowed), "reached_coefficient_ff_count": len(endpoints),
            "foreign_state_or_control_pin_sinks": 0,
            "fused_fifo_capture_luts_checked": True}


def inspect_replicas(module, rtl):
    """Return only independently verified replica-Q -> canonical-Q aliases."""
    cells, nets = module["cells"], module["netnames"]
    present = {name for name in cells if name.startswith(PREFIX)}
    net_present = {name for name in nets if name.startswith(PREFIX)}
    if not present and not net_present:
        return {}, {"status": "not_used", "replica_registers": 0}
    if present != net_present:
        raise RuntimeError("Unexpected address replica cell/net set")
    pairs = {}
    for name in present:
        match = re.fullmatch(re.escape(PREFIX) + r"([1-7])_([0-9]|1[0-2])", name)
        if not match:
            raise RuntimeError("Unexpected address replica name")
        pairs[int(match[1]), int(match[2])] = name
    drivers, users = connectivity(module)
    address = nets[ENGINE + "_addressR"]["bits"]
    if len(address) != WIDTH or len(set(address)) != WIDTH or any(type(bit) is not int for bit in address):
        raise RuntimeError("Expected 13 distinct canonical address bits")
    canonical = []
    for bit in address:
        if len(drivers[bit]) != 1 or drivers[bit][0][1:] != ("Q", 0):
            raise RuntimeError("Canonical address bit has no unique FF Q driver")
        cell = cells[drivers[bit][0][0]]
        if cell["type"] != "TRELLIS_FF" or cell["parameters"] != FF_PARAMETERS:
            raise RuntimeError("Unreviewed address FF type or parameters")
        canonical.append(cell)
    aliases, copies = {}, {(0, bit): wire for bit, wire in enumerate(address)}
    for (group, bit), name in sorted(pairs.items()):
        replica = cells[name]
        q = replica["connections"].get("Q")
        if len(q or []) != 1 or type(q[0]) is not int or q[0] in set(address) | set(aliases):
            raise RuntimeError("Replica Q is not a unique new wire")
        expected = deepcopy(canonical[bit])
        expected["connections"]["Q"] = q
        if replica != expected:
            raise RuntimeError("Replica is not an exact same-cycle FF clone: " + name)
        if drivers[q[0]] != [(name, "Q", 0)]:
            raise RuntimeError("Replica Q has another driver")
        if nets[name] != {"hide_name": 0, "bits": q, "attributes": {}}:
            raise RuntimeError("Unexpected replica net metadata")
        aliases[q[0]], copies[group, bit] = address[bit], q[0]
    owners = coefficient_owners(module, aliases)
    protected = protected_carry_cells(module, owners, aliases)
    all_address = {wire: index for index, wire in enumerate(address)}
    all_address.update({copy: all_address[original] for copy, original in aliases.items()})
    for wire in aliases:
        for user, port, index in users[wire]:
            if user not in owners or cells[user]["port_directions"][port] != "input":
                raise RuntimeError("Replica Q escapes coefficient logic: " + user)
    required_pairs = set()
    for name, group in owners.items():
        for port, direction in cells[name]["port_directions"].items():
            if direction == "input":
                for wire in cells[name]["connections"][port]:
                    if wire in all_address:
                        pair = group, all_address[wire]
                        if group:
                            required_pairs.add(pair)
                        if wire != copies.get(pair):
                            raise RuntimeError("Coefficient leaf uses the wrong address replica group")
    if set(pairs) != required_pairs:
        raise RuntimeError("Replica set differs from exactly the required coefficient address group/bit pairs")
    boundary = observation_boundary(module, aliases, drivers, users)
    proof = initialization_proof(module, rtl, canonical)
    fanout = {str(group): [len(users[copies[group, bit]]) if (group, bit) in copies else None
                           for bit in range(WIDTH)] for group in range(GROUPS)}
    return aliases, {"status": "pass", "replica_registers": len(aliases), "address_groups": GROUPS,
                     "owned_lut_cells": dict(sorted(Counter(group for name, group in owners.items()
                                                           if cells[name]["type"] in LUT_TYPES).items())),
                     "owned_coefficient_cells": dict(sorted(Counter(owners.values()).items())),
                     "protected_carry_cells": sorted(protected),
                     "coefficient_cells_duplicated": 0, "address_q_fanout_by_group": fanout,
                     "unused_replica_q_count": sum(not users[wire] for wire in aliases),
                     "observation_boundary": boundary, "initialization_and_induction": proof}


def audit_transition(before, after, rtl):
    """Reverse only approved changes, then compare the entire JSON structure."""
    original = before["modules"]["mkTop"]
    original_owners = coefficient_owners(original, {})
    protected = protected_carry_cells(original, original_owners, {})
    for name in protected:
        if after["modules"]["mkTop"]["cells"].get(name) != original["cells"][name]:
            raise RuntimeError("Protected carry cell or fan-in changed: " + name)
    aliases, proof = inspect_replicas(after["modules"]["mkTop"], rtl)
    if not aliases:
        raise RuntimeError("Missing required address replicas")
    if set(proof["protected_carry_cells"]) != protected:
        raise RuntimeError("Protected carry cell set changed")
    restored = deepcopy(after)
    module = restored["modules"]["mkTop"]
    rewired = 0
    for name in list(module["cells"]):
        if name.startswith(PREFIX):
            del module["cells"][name]
            del module["netnames"][name]
            continue
        cell = module["cells"][name]
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                for index, wire in enumerate(cell["connections"][port]):
                    if wire in aliases:
                        cell["connections"][port][index] = aliases[wire]
                        rewired += 1
    if restored != before:
        raise RuntimeError("Unapproved structural change outside replica FF/net additions and coefficient address pins")
    _, before_users = connectivity(before["modules"]["mkTop"])
    original_address = before["modules"]["mkTop"]["netnames"][ENGINE + "_addressR"]["bits"]
    proof.update({"original_address_q_fanout": [len(before_users[wire]) for wire in original_address],
                  "protected_carry_cells_identical": "pass",
                  "whole_netlist_reverse_comparison": "pass", "rewired_coefficient_input_pins": rewired,
                  "other_cells_parameters_ports_nets_attributes": "byte-value identical"})
    return proof
