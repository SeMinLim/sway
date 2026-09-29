#!/usr/bin/env python3
"""Focused reset-fold fixtures; pass --helper to test another checkout."""
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def lut(source=10, output=20, init=0x00ff, inputs=None, attributes=None):
    return {"type": "LUT4", "parameters": {"INIT": f"{init:016b}"},
            "attributes": {} if attributes is None else attributes,
            "port_directions": {"A": "input", "B": "input", "C": "input", "D": "input", "Z": "output"},
            "connections": dict(zip(("A", "B", "C", "D"),
                                    [[bit] for bit in (inputs or ["0", "0", "0", source])]), Z=[output])}


def ff(mux="LSR", wire=20, mode="LSR_OVER_CE"):
    params = {"SRMODE": mode, "REGSET": "RESET", "CEMUX": "CE", "CLKMUX": "CLK", "GSR": "DISABLED"}
    if mux is not None:
        params["LSRMUX"] = mux
    return {"type": "TRELLIS_FF", "parameters": params, "attributes": {"src": "fixture"},
            "port_directions": {"LSR": "input", "CLK": "input", "CE": "input", "DI": "input", "M": "input", "Q": "output"},
            "connections": {"LSR": [wire], "CLK": [1], "CE": [2], "DI": [3], "M": [4], "Q": [30]}}


def module(cells=None):
    return {"attributes": {}, "cells": cells or {"inv": lut(), "ff": ff()},
            "ports": {"rst_n": {"direction": "input", "bits": [10]}},
            "netnames": {"rst_n": {"bits": [10], "attributes": {}},
                         "rst": {"bits": [20], "attributes": {}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", type=Path, default=Path(__file__).with_name("merge_reset_controls.py"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("merge_reset_controls", args.helper)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    cases, truth_checks = [], 0

    def run(name, value):
        before = deepcopy(value)
        report = helper.transform_module(value)
        cases.append(name)
        for cell_name, cell in before["cells"].items():
            if cell["type"] == "TRELLIS_FF":
                actual = deepcopy(value["cells"][cell_name])
                expected = deepcopy(cell)
                actual["connections"].pop("LSR")
                expected["connections"].pop("LSR")
                actual["parameters"].pop("LSRMUX", None)
                expected["parameters"].pop("LSRMUX", None)
                assert actual == expected, name
        return report

    for inputs in itertools.product(("0", "1", 10), repeat=4):
        if 10 not in inputs:
            continue
        source_port = inputs.index(10)
        init = sum((1 - ((index >> source_port) & 1)) << index for index in range(16))
        for mux, mode in itertools.product((None, "LSR", "INV"), ("LSR_OVER_CE", "ASYNC")):
            value = module({"inv": lut(init=init, inputs=inputs), "ff": ff(mux, mode=mode)})
            report = run(f"truth-{inputs}-{mux}-{mode}", value)
            assert report["reset_inverter_ffs_folded"] == 1
            assert "inv" not in value["cells"] and "rst" not in value["netnames"]
            new = value["cells"]["ff"]
            assert new["connections"]["LSR"] == [10]
            for source in (0, 1):
                index = sum((source if type(bit) is int else int(bit)) << i for i, bit in enumerate(inputs))
                old_reset = ((init >> index) & 1) ^ (mux == "INV")
                new_reset = source ^ (new["parameters"]["LSRMUX"] == "INV")
                assert old_reset == new_reset
                for enable, data, old_q, reset_q in itertools.product((0, 1), repeat=4):
                    old_result = reset_q if old_reset else data if enable else old_q
                    new_result = reset_q if new_reset else data if enable else old_q
                    assert old_result == new_result
                    truth_checks += 1

    value = module()
    value["cells"]["dsp"] = {"type": "MULT18X18D", "parameters": {}, "attributes": {},
                                  "port_directions": {"RST0": "input"}, "connections": {"RST0": [20]}}
    dsp = deepcopy(value["cells"]["dsp"])
    report = run("mixed-dsp-fanout", value)
    assert report["reset_inverter_ffs_folded"] == 1 and "inv" in value["cells"]
    assert value["cells"]["dsp"] == dsp and value["netnames"]["rst"]["bits"] == [20]

    value = module()
    value["ports"]["reset_output"] = {"direction": "output", "bits": [20]}
    run("top-output-keeps-inverter", value)
    assert "inv" in value["cells"] and value["ports"]["reset_output"]["bits"] == [20]

    value = module()
    value["netnames"]["mixed"] = {"bits": [20, 3], "attributes": {}}
    run("mixed-vector-name-keeps-inverter", value)
    assert "inv" in value["cells"] and value["netnames"]["mixed"]["bits"] == [20, 3]

    value = module()
    value["cells"]["ff"]["connections"]["CE"] = [20]
    run("non-lsr-ff-load-keeps-inverter", value)
    assert "inv" in value["cells"] and value["cells"]["ff"]["connections"]["CE"] == [20]

    for label, change in [
        ("unsupported-mux", lambda m: m["cells"]["ff"]["parameters"].update(LSRMUX="UNKNOWN")),
        ("constrained-lut", lambda m: m["cells"]["inv"]["attributes"].update(BEL="X1/Y1/SLICEA")),
        ("kept-lut", lambda m: m["cells"]["inv"]["attributes"].update(keep="1")),
        ("constrained-output-net", lambda m: m["netnames"]["rst"]["attributes"].update(keep="1")),
        ("constrained-source-net", lambda m: m["netnames"]["rst_n"]["attributes"].update(keep="1")),
        ("noninverse", lambda m: m["cells"]["inv"]["parameters"].update(INIT=f"{0xff00:016b}")),
        ("unknown-init", lambda m: m["cells"]["inv"]["parameters"].update(INIT="xxxxxxxx11111111")),
        ("unknown-input", lambda m: m["cells"]["inv"]["connections"].update(A=["x"])),
        ("constant-only", lambda m: m["cells"]["inv"]["connections"].update(D=["0"])),
        ("two-variables", lambda m: m["cells"]["inv"]["connections"].update(A=[11])),
        ("self-cycle", lambda m: m["cells"]["inv"]["connections"].update(D=[20])),
        ("lut-cycle", lambda m: m["cells"].update(loop=lut(source=20, output=10))),
        ("duplicate-driver", lambda m: m["cells"].update(another=lut())),
    ]:
        value = module()
        change(value)
        before = deepcopy(value)
        report = run(label, value)
        assert report["reset_inverter_ffs_folded"] == 0 and value == before, label

    metadata = {"src": "fixture.bsv:1", "hdlname": "core reset_n",
                "force_downto": "00000000000000000000000000000001", "unused_bits": "0"}
    for net_name, (attribute, attribute_value) in itertools.product(("rst_n", "rst"), metadata.items()):
        value = module()
        value["netnames"][net_name]["attributes"][attribute] = attribute_value
        report = run(f"metadata-{net_name}-{attribute}", value)
        assert report["reset_inverter_ffs_folded"] == 1 and "inv" not in value["cells"]
        assert value["cells"]["ff"]["connections"]["LSR"] == [10]
        if net_name == "rst_n":
            assert value["netnames"][net_name]["attributes"] == {attribute: attribute_value}

    for net_name, attribute in itertools.product(("rst_n", "rst"), ("keep", "BEL", "LOC", "future_constraint")):
        value = module()
        value["netnames"][net_name]["attributes"].update(metadata)
        value["netnames"][net_name]["attributes"][attribute] = "protected"
        before = deepcopy(value)
        report = run(f"metadata-with-constraint-{net_name}-{attribute}", value)
        assert report["reset_inverter_ffs_folded"] == 0 and value == before

    value = module()
    value["cells"]["ff2"] = ff("UNKNOWN")
    run("partial-supported-mux", value)
    assert value["cells"]["ff"]["connections"]["LSR"] == [10]
    assert value["cells"]["ff2"]["connections"]["LSR"] == [20] and "inv" in value["cells"]

    value = module({"and1": lut(init=0x8888, inputs=[10, 11, "0", "0"]),
                    "and2": lut(output=21, init=0x8888, inputs=[10, 11, "0", "0"]),
                    "ff": ff(), "ff2": ff(wire=21)})
    value["netnames"]["rst2"] = {"bits": [21], "attributes": {}}
    report = run("canonical-noninverter-merge", value)
    assert report["reset_inverter_ffs_folded"] == 0 and report["reset_only_luts_removed"] == 1
    assert value["cells"]["ff2"]["connections"]["LSR"] == [20]
    assert value["netnames"]["rst2"]["bits"] == [20]

    with tempfile.TemporaryDirectory() as directory:
        netlist, output = Path(directory) / "netlist.json", Path(directory) / "report.json"
        netlist.write_text(json.dumps({"modules": {"mkTop": module()}}))
        original_hash = hashlib.sha256(netlist.read_bytes()).hexdigest()
        subprocess.run([sys.executable, str(args.helper), str(netlist), "--report", str(output)], check=True,
                       capture_output=True, text=True)
        evidence = json.loads(output.read_text())
        assert evidence["status"] == "pass" and evidence["input_sha256"] == original_hash
        assert evidence["output_sha256"] == hashlib.sha256(netlist.read_bytes()).hexdigest()
        cases.append("cli-hashes-and-atomic-output")

    report = {"status": "pass", "fixture_count": len(cases), "truth_checks": truth_checks,
              "helper_sha256": hashlib.sha256(args.helper.read_bytes()).hexdigest(),
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "coverage": ["one-variable truth tables with constants/repeated inputs", "LSR/INV/default polarity",
                           "synchronous/asynchronous mode preservation", "all FF non-reset wiring/parameters exact",
                           "DSP/top-output/non-LSR fanout retained", "unsupported mux and constrained attributes skipped",
                           "src/hdlname/force_downto/unused_bits accepted on source and output aliases",
                           "known metadata never bypasses keep/BEL/LOC/unknown attribute protection",
                           "noninverse/unknown/constants/multiple variables skipped", "LUT cycles/duplicate drivers rejected",
                           "canonical reset-only merge retained", "CLI source/output hashes"],
              "commands": [[sys.executable, str(Path(__file__)), "--helper", str(args.helper), "--report", str(args.report)]],
              "limitations": "Focused Python truth/structural fixtures; no new BSC/Yosys/nextpnr run."}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
