#!/usr/bin/env python3
"""Reduce headHidden LUT-ROM address fanout without another pipeline stage."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from check_address_replicas import ENGINE, PREFIX, GROUPS, WIDTH, connectivity, coefficient_owners, audit_transition
from merge_reset_controls import atomic_json


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transform(netlist):
    result = deepcopy(netlist)
    module = result["modules"]["mkTop"]
    cells, nets = module["cells"], module["netnames"]
    if any(name.startswith(PREFIX) for name in cells) or any(name.startswith(PREFIX) for name in nets):
        raise RuntimeError("Address replication cannot be applied twice")
    drivers, _ = connectivity(module)
    address = nets[ENGINE + "_addressR"]["bits"]
    owners = coefficient_owners(module, {})
    used_wires = {wire for cell in cells.values() for bits in cell["connections"].values()
                  for wire in bits if type(wire) is int}
    used_wires.update(wire for net in nets.values() for wire in net["bits"] if type(wire) is int)
    used_wires.update(wire for port in module["ports"].values() for wire in port["bits"] if type(wire) is int)
    next_wire = max(used_wires) + 1
    copies = {(0, bit): wire for bit, wire in enumerate(address)}
    positions = {wire: bit for bit, wire in enumerate(address)}
    required_pairs = {(group, positions[wire]) for name, group in owners.items() if group
                      for port, direction in cells[name]["port_directions"].items() if direction == "input"
                      for wire in cells[name]["connections"][port] if wire in positions}
    for group, bit in sorted(required_pairs):
        name = PREFIX + str(group) + "_" + str(bit)
        original_name, port, index = drivers[address[bit]][0]
        if port != "Q" or index != 0:
            raise RuntimeError("Address source must be register Q")
        clone = deepcopy(cells[original_name])
        clone["connections"]["Q"] = [next_wire]
        cells[name] = clone
        nets[name] = {"hide_name": 0, "bits": [next_wire], "attributes": {}}
        copies[group, bit] = next_wire
        next_wire += 1
    positions = {wire: bit for bit, wire in enumerate(address)}
    for name, group in owners.items():
        for port, direction in cells[name]["port_directions"].items():
            if direction == "input":
                cells[name]["connections"][port] = [copies[group, positions[wire]] if wire in positions else wire
                                                    for wire in cells[name]["connections"][port]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("netlist", type=Path)
    parser.add_argument("--rtl", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if len({args.netlist.resolve(), args.before.resolve(), args.report.resolve(), args.rtl.resolve()}) != 4:
        raise RuntimeError("Netlist, preserved input, report and RTL paths must be distinct")
    original_sha = sha256(args.netlist)
    rtl_sha = sha256(args.rtl)
    before = json.loads(args.netlist.read_text())
    after = transform(before)
    proof = audit_transition(before, after, args.rtl.read_text())
    if sha256(args.netlist) != original_sha or sha256(args.rtl) != rtl_sha:
        raise RuntimeError("Replication inputs changed during audit")
    shutil.copyfile(args.netlist, args.before)
    if sha256(args.before) != original_sha:
        raise RuntimeError("Pre-replication copy differs")
    atomic_json(args.netlist, after)
    proof.update({"input_sha256": original_sha, "output_sha256": sha256(args.netlist),
                  "rtl_sha256": rtl_sha, "preserved_input": args.before.name,
                  "transform_sha256": sha256(Path(__file__)),
                  "auditor_sha256": sha256(Path(__file__).with_name("check_address_replicas.py"))})
    args.report.write_text(json.dumps(proof, indent=2) + "\n")
    print("SWAY_ADDRESS_REPLICAS_PASS replicas=" + str(proof["replica_registers"]) + " report=" + str(args.report))


if __name__ == "__main__":
    main()
