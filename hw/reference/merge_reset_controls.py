#!/usr/bin/env python3
"""Fold reset inversions into ECP5 FFs, then share identical reset-only LUTs."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        json.dump(value, output, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def connectivity(module):
    users, drivers = defaultdict(list), defaultdict(list)
    for name, cell in module["cells"].items():
        for port, bits in cell["connections"].items():
            target = drivers if cell["port_directions"][port] == "output" else users
            for bit in bits:
                if type(bit) is int:
                    target[bit].append((name, port))
    return users, drivers


def unconstrained_lut(cell):
    # These two attributes are Yosys provenance, not placement constraints.
    return cell["type"] == "LUT4" and not (
        set(cell.get("attributes", {})) - {"src", "module_not_derived"})


def has_lut_cycle(bit, cells, drivers, active=None, complete=None):
    """Conservatively reject any LUT4 feedback in the candidate's input cone."""
    active = set() if active is None else active
    complete = set() if complete is None else complete
    if type(bit) is not int or bit in complete:
        return False
    if bit in active:
        return True
    active.add(bit)
    for name, _ in drivers[bit]:
        cell = cells[name]
        if cell["type"] == "LUT4":
            for port in ("A", "B", "C", "D"):
                if any(has_lut_cycle(source, cells, drivers, active, complete)
                       for source in cell["connections"].get(port, [])):
                    return True
    active.remove(bit)
    complete.add(bit)
    return False


def inverter_source(cell):
    """Prove INIT[A + 2*B + 4*C + 8*D] is NOT one unique input wire."""
    if not unconstrained_lut(cell) or set(cell["parameters"]) != {"INIT"}:
        return None
    init = cell["parameters"]["INIT"]
    if not isinstance(init, str) or len(init) != 16 or set(init) - {"0", "1"}:
        return None
    inputs = []
    for port in ("A", "B", "C", "D"):
        bits = cell["connections"].get(port, [])
        if len(bits) != 1 or not (type(bits[0]) is int or bits[0] in ("0", "1")):
            return None
        inputs.append(bits[0])
    variables = {bit for bit in inputs if type(bit) is int}
    if len(variables) != 1:
        return None
    source = variables.pop()
    for value in (0, 1):
        index = sum((value if type(bit) is int else int(bit)) << shift
                    for shift, bit in enumerate(inputs))
        if (int(init, 2) >> index) & 1 != 1 - value:
            return None
    return source


def transform_module(module):
    cells = module["cells"]
    original = deepcopy(cells)
    original_ports = deepcopy(module["ports"])
    before = Counter(cell["type"] for cell in cells.values())
    users, drivers = connectivity(module)
    port_bits = {bit for port in module["ports"].values() for bit in port["bits"]}
    aliases_by_bit = defaultdict(list)
    for name, net in module["netnames"].items():
        for bit in set(net["bits"]):
            aliases_by_bit[bit].append(name)
    # Yosys attaches source names and vector-layout metadata to reset aliases.
    # Preserve those attributes, but do not treat them as placement constraints.
    net_metadata = {"src", "hdlname", "force_downto", "unused_bits"}
    constrained_bits = {bit for net in module["netnames"].values()
                        if set(net.get("attributes", {})) - net_metadata
                        for bit in net["bits"]}
    expected = deepcopy(cells)
    folded, folded_luts, dead_aliases = [], [], []
    lsr_before = {tuple(cell["connections"]["LSR"]) for cell in cells.values()
                  if cell["type"] == "TRELLIS_FF" and cell["connections"].get("LSR")
                  and type(cell["connections"]["LSR"][0]) is int}
    for name, cell in sorted(cells.items()):
        source = inverter_source(cell)
        output = cell["connections"].get("Z", [])
        if source is None or len(output) != 1 or type(output[0]) is not int:
            continue
        bit = output[0]
        if (source == bit or drivers[bit] != [(name, "Z")]
                or bit in constrained_bits or source in constrained_bits
                or has_lut_cycle(bit, cells, drivers)):
            continue
        rewritten = []
        for user, port in users[bit]:
            ff = cells[user]
            mux = ff["parameters"].get("LSRMUX", "LSR")
            if (ff["type"] != "TRELLIS_FF" or port != "LSR"
                    or ff["connections"][port] != [bit] or mux not in ("LSR", "INV")):
                continue
            new_mux = "INV" if mux == "LSR" else "LSR"
            # Yosys ecp5/cells_sim.v: muxlsr = LSRMUX == "INV" ? ~LSR : LSR.
            # nextpnr ecp5/pack.cc preserves FF_LSRINV; bitstream.cc emits it.
            # (~source) XOR old_inv == source XOR !old_inv, for either SRMODE.
            ff["connections"][port] = [source]
            ff["parameters"]["LSRMUX"] = new_mux
            expected[user]["connections"][port] = [source]
            expected[user]["parameters"]["LSRMUX"] = new_mux
            rewritten.append((user, port))
            folded.append({"cell": user, "inverter": name, "old_wire": bit,
                           "new_wire": source, "old_mux": mux, "new_mux": new_mux})
        if not rewritten:
            continue
        remaining = set(users[bit]) - set(rewritten)
        # Never alias a complemented wire to its source. Preserve mixed-vector
        # netnames by retaining their inverter, even when it has no cell loads.
        names = aliases_by_bit[bit]
        scalar_aliases = all(set(module["netnames"][alias]["bits"]) == {bit}
                             for alias in names)
        if not remaining and bit not in port_bits and scalar_aliases:
            folded_luts.append(name)
            for alias in names:
                del module["netnames"][alias]
                dead_aliases.append(alias)
    for name in folded_luts:
        del cells[name]
        del expected[name]

    # Preserve the reset-only canonical merge for other LUT functions.
    users, drivers = connectivity(module)
    canonical, aliases, removed = {}, {}, []
    for name, cell in sorted(cells.items()):
        if not unconstrained_lut(cell):
            continue
        output = cell["connections"]["Z"]
        if len(output) != 1 or type(output[0]) is not int:
            continue
        bit = output[0]
        if (bit in port_bits or bit in constrained_bits or not users[bit]
                or drivers[bit] != [(name, "Z")]):
            continue
        if any(cells[user]["type"] != "TRELLIS_FF" or port != "LSR"
               or cells[user]["connections"][port] != [bit] for user, port in users[bit]):
            continue
        if has_lut_cycle(bit, cells, drivers):
            continue
        signature = (tuple(sorted(cell["parameters"].items())),
                     tuple((port, tuple(cell["connections"][port])) for port in ("A", "B", "C", "D")))
        if signature in canonical:
            aliases[bit] = canonical[signature]
            removed.append(name)
        else:
            canonical[signature] = bit
    for old, new in aliases.items():
        for name, port in users[old]:
            cells[name]["connections"][port] = [new]
            expected[name]["connections"][port] = [new]
    for name in removed:
        del cells[name]
        del expected[name]
    for net in module["netnames"].values():
        net["bits"] = [aliases.get(bit, bit) for bit in net["bits"]]
    after = Counter(cell["type"] for cell in cells.values())
    # Exact cell comparison limits every surviving edit to the recorded LSR
    # wires/polarities: FF DI/M/CLK/CE, all other parameters, and all DSPs stay exact.
    assert cells == expected
    assert module["ports"] == original_ports
    assert set(original) - set(cells) == set(folded_luts + removed)
    assert before["TRELLIS_FF"] == after["TRELLIS_FF"]
    assert before - after == Counter({"LUT4": len(folded_luts) + len(removed)})
    assert not (after - before)
    lsr_after = {tuple(cell["connections"]["LSR"]) for cell in cells.values()
                 if cell["type"] == "TRELLIS_FF" and cell["connections"].get("LSR")
                 and type(cell["connections"]["LSR"][0]) is int}
    return {"reset_only_luts_removed": len(removed),
            "reset_inverter_luts_removed": len(folded_luts),
            "reset_inverter_ffs_folded": len(folded), "folded_ffs": folded,
            "dead_inverter_netnames_removed": len(dead_aliases),
            "nonconstant_lsr_nets_before": len(lsr_before),
            "nonconstant_lsr_nets_after": len(lsr_after),
            "cells_before": dict(sorted(before.items())), "cells_after": dict(sorted(after.items())),
            "scope": "Proven one-variable NOT LUTs folded into TRELLIS_FF.LSRMUX; identical reset-only LUTs shared; FF data/clock/CE and every other parameter/cell unchanged"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("netlist", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    before_hash = digest(args.netlist)
    design = json.loads(args.netlist.read_text())
    report = transform_module(design["modules"]["mkTop"])
    # Compact diagnostic metadata so generated netlists remain manageable.
    for item in design["modules"].values():
        item.get("attributes", {}).pop("src", None)
        for category in ("cells", "netnames"):
            for value in item.get(category, {}).values():
                value.get("attributes", {}).pop("src", None)
    atomic_json(args.netlist, design)
    report.update(status="pass", input_sha256=before_hash, output_sha256=digest(args.netlist))
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Folded {report['reset_inverter_ffs_folded']} FF reset inversions; "
          f"shared {report['reset_only_luts_removed']} reset LUTs; "
          f"{report['nonconstant_lsr_nets_after']} nonconstant LSR nets remain")


if __name__ == "__main__":
    main()
