#!/usr/bin/env python3
"""Compare the coefficient evaluator with the reviewed official CCU2C model."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from check_mapped_weights import cell_values, reviewed_library, sha256


INPUT_PORTS = ("CIN", "A0", "B0", "C0", "D0", "A1", "B1", "C1", "D1")
OUTPUT_PORTS = ("S0", "S1", "COUT")
INPUT_COUNT = 1 << len(INPUT_PORTS)


def parameter_cases():
    # Basis/complement and mixed truth tables exercise both halves and all
    # injection modes. A separate unparameterized cell checks HDL defaults.
    pairs = [(0, 0), (0xffff, 0xffff), (0, 0xffff), (0xffff, 0),
             (0x6996, 0x6996), (0xaaaa, 0x5555), (0xf0f0, 0x0f0f)]
    pairs += [(1 << bit, 1 << (15 - bit)) for bit in range(16)]
    pairs += [(0xffff ^ (1 << bit), 0xffff ^ (1 << (15 - bit)))
              for bit in range(16)]
    cases = [{}]
    for first, second in pairs:
        for inject0 in ("YES", "NO"):
            for inject1 in ("YES", "NO"):
                cases.append({"INIT0": format(first, "016b"),
                              "INIT1": format(second, "016b"),
                              "INJECT1_0": inject0, "INJECT1_1": inject1})
    return cases


def testbench(cases):
    lines = ["module tb;", "reg [8:0] inputs;",
             f"wire [{len(cases) * 3 - 1}:0] outputs;", "integer vector;"]
    for index, parameters in enumerate(cases):
        overrides = []
        for name, value in parameters.items():
            literal = "16'b" + value if name.startswith("INIT") else '"' + value + '"'
            overrides.append("." + name + "(" + literal + ")")
        ports = [f".{name}(inputs[{bit}])" for bit, name in enumerate(INPUT_PORTS)]
        ports += [f".{name}(outputs[{3 * index + bit}])"
                  for bit, name in enumerate(OUTPUT_PORTS)]
        override_text = " #(" + ", ".join(overrides) + ")" if overrides else ""
        lines.append(f"CCU2C{override_text} dut{index} (" + ", ".join(ports) + ");")
    lines += ["initial begin", f"  for (vector = 0; vector < {INPUT_COUNT}; vector = vector + 1) begin",
              "    inputs = vector; #1;",
              '    $display("VECTOR %03x %h", inputs, outputs);',
              "  end", "  $finish;", "end", "endmodule"]
    return "\n".join(lines) + "\n"


def run_checked(command):
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=60)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(command) + "\n"
                           + result.stdout + result.stderr)
    return result.stdout


def check(args):
    library = args.cells_sim.resolve()
    version, sources = reviewed_library(library)
    hashes = {name: sha256(path) for name, path in sources.items()}
    cases = parameter_cases()
    inputs = {name: sum(1 << vector for vector in range(INPUT_COUNT)
                        if (vector >> bit) & 1)
              for bit, name in enumerate(INPUT_PORTS)}
    expected_planes = [cell_values("CCU2C", parameters, inputs) for parameters in cases]
    with tempfile.TemporaryDirectory(prefix="sway-ccu2c-") as directory:
        root = Path(directory)
        source, executable = root / "tb.v", root / "tb.vvp"
        source.write_text(testbench(cases))
        # Exclude unrelated FF/I/O headers; the pinned sources contain
        # the complete CCU2C and LUT definitions used by this test.
        run_checked([args.iverilog, "-g2012", "-DNO_INCLUDES", "-s", "tb", "-I", str(library.parent),
                     "-o", str(executable), str(library), str(source)])
        output = run_checked([args.vvp, str(executable)])
    rows = [line.split() for line in output.splitlines() if line.startswith("VECTOR ")]
    if len(rows) != INPUT_COUNT:
        raise RuntimeError("Official model did not return all 512 input vectors")
    for vector, row in enumerate(rows):
        if len(row) != 3 or int(row[1], 16) != vector:
            raise RuntimeError("Official model returned missing, duplicated, or unordered vectors")
        # int rejects any unknown/high-impedance model output.
        actual = int(row[2], 16)
        for index, planes in enumerate(expected_planes):
            expected = sum(((planes[port] >> vector) & 1) << bit
                           for bit, port in enumerate(OUTPUT_PORTS))
            observed = (actual >> (3 * index)) & 7
            if observed != expected:
                raise RuntimeError(f"CCU2C mismatch: case={cases[index]!r} inputs={vector:03x} "
                                   f"official={observed:03b} evaluator={expected:03b}")
    if any(sha256(path) != hashes[name] for name, path in sources.items()):
        raise RuntimeError("Official primitive source changed during verification")
    print(f"SWAY_CCU2C_EQUATIONS_PASS library={version} parameter_cases={len(cases)} "
          f"input_vectors={INPUT_COUNT} comparisons={len(cases) * INPUT_COUNT} mismatches=0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells-sim", type=Path, required=True)
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    args = parser.parse_args()
    try:
        check(args)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("SWAY_CCU2C_EQUATIONS_FAIL: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
