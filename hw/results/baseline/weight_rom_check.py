#!/usr/bin/env python3
"""Independently interpret emitted BSV weight truth tables against frozen bytes."""

import argparse
import hashlib
import json
from pathlib import Path
import re


WEIGHTS = [
    "embedding.weight", "blocks.0.inWeight", "blocks.0.xWeight",
    "blocks.0.dtWeight", "blocks.0.outWeight", "blocks.1.inWeight",
    "blocks.1.xWeight", "blocks.1.dtWeight", "blocks.1.outWeight",
    "headHidden.weight", "headOutput.weight",
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def parse_page(name, signature, body):
    require(signature == "Bit#(8) addr", "Page signature: " + name)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    require(lines.pop(0) == "Bit#(8) result = 0;", "Page initializer: " + name)
    require(lines.pop() == "return unpack(result);", "Signed page return: " + name)
    values = [0] * 256
    seen_bits = set()
    declarations = 0
    while lines:
        truth = re.fullmatch(r"Bit#\(256\) truth([0-7]) = 256'h([0-9a-f]{64});", lines.pop(0))
        require(truth, "Bit-plane truth table declaration: " + name)
        bit, mask = int(truth[1]), int(truth[2], 16)
        require(bit not in seen_bits, "Duplicate result bit: " + name)
        seen_bits.add(bit)
        expected = f"result[{bit}] = truth{bit}[addr];"
        require(lines and lines.pop(0) == expected, "Bit-plane address selector: " + name)
        declarations += 1
        for address in range(256):
            values[address] |= ((mask >> address) & 1) << bit
    return values, declarations


def parse_bank(name, signature, body, pages):
    require(signature == "Bit#(13) addr", "Bank signature: " + name)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    require(lines[:2] == ["Int#(8) result = 0;", "case ( addr[12:8] )"],
            "Bank initializer/selector: " + name)
    require(lines[-3:] == ["default: result = 0;", "endcase", "return result;"],
            "Bank default/return: " + name)
    values = [0] * 8192
    used = set()
    for line in lines[2:-3]:
        case = re.fullmatch(r"5'd(\d+): result = (linearWeightBank\d+Page\d+)\(addr\[7:0\]\);", line)
        require(case, "Bank page syntax: " + name)
        page, page_name = int(case[1]), case[2]
        require(0 <= page < 32 and page not in used and page_name == f"{name}Page{page}",
                "Bank page mapping: " + name)
        require(page_name in pages, "Undefined page: " + page_name)
        used.add(page)
        values[256 * page:256 * (page + 1)] = pages[page_name]
    return values, {f"{name}Page{page}" for page in used}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument("--hardware", type=Path, default=base.parents[1])
    parser.add_argument("--output", type=Path, default=base / "weight_rom_verification.json")
    args = parser.parse_args()
    hardware = args.hardware.resolve()
    source_path = hardware / "generated/SwayParameters.bsv"
    source_bytes = source_path.read_bytes()
    source = source_bytes.decode()
    marker = b"function Bool layerSliceSupported("
    require(source_bytes.count(marker) == 1, "Non-affine boundary")
    suffix = source_bytes.split(marker, 1)[1]
    prefix = source.split(marker.decode(), 1)[0]
    header, functions = prefix.split("package SwayParameters;", 1)
    require(all(not line or line.startswith("//") for line in header.splitlines()), "Package header")
    definitions = list(re.finditer(r"function Int#\(8\) (\w+)\(([^\n]*)\);\n(.*?)\nendfunction", functions, re.S))
    remainder = re.sub(r"function Int#\(8\) (\w+)\(([^\n]*)\);\n(.*?)\nendfunction", "", functions, flags=re.S)
    require(not remainder.strip(), "Unparsed generated affine source")
    pages, banks, dispatch, used_pages = {}, {}, [], set()
    seen_functions = set()
    truth_tables = 0
    for match in definitions:
        name, signature, body = match.groups()
        require(name not in seen_functions, "Duplicate function: " + name)
        seen_functions.add(name)
        if re.fullmatch(r"linearWeightBank\d+Page\d+", name):
            pages[name], count = parse_page(name, signature, body)
            truth_tables += count
        elif re.fullmatch(r"linearWeightBank\d+", name):
            banks[name], used = parse_bank(name, signature, body, pages)
            used_pages |= used
        else:
            require(name == "linearWeight" and not dispatch, "Unexpected function: " + name)
            require(signature == "Integer laneCount, Integer layerId, Integer rowOffset, Integer outputNum, Integer lane, Bit#(13) addr",
                    "Dispatch signature")
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            require(lines.pop(0) == "Int#(8) result = 0;" and lines.pop() == "return result;", "Dispatch defaults")
            for line in lines:
                entry = re.fullmatch(r"if \( laneCount == (\d+) && layerId == (\d+) && rowOffset == (\d+) && outputNum == (\d+) && lane == (\d+) \) result = (linearWeightBank\d+)\(addr\);", line)
                require(entry, "Dispatch condition syntax")
                dispatch.append((tuple(map(int, entry.groups()[:5])), entry[6]))
    require(used_pages == set(pages), "Unreferenced page functions")
    require(len(dispatch) == len(banks) == 119, "Expected 119 banks and dispatch entries")
    require(len({key for key, name in dispatch}) == len(dispatch), "Duplicate dispatch conditions")
    require({name for key, name in dispatch} == set(banks), "Unreferenced bank functions")

    export = hardware / "model/export"
    manifest_bytes = (export / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    tensors = {entry["parameterName"]: entry for entry in manifest["tensors"]}
    expected_dispatch = []
    valid_addresses = 0
    padded_addresses = 0
    zero_addresses = 0
    tensor_hashes = {}
    for layer, parameter in enumerate(WEIGHTS):
        entry = tensors[parameter]
        require(entry["dtype"] in ("|i1", "int8"), "Expected signed byte tensor")
        rows, columns = entry["shape"]
        raw = (export / entry["binary"]).read_bytes()
        require(len(raw) == rows * columns == entry["bytes"] and digest(raw) == entry["sha256"],
                "Frozen tensor hash/size: " + parameter)
        require(bytes(int(word, 16) for word in (export / entry["hex"]).read_text().split()) == raw,
                "Frozen binary/hex: " + parameter)
        tensor_hashes[parameter] = digest(raw)
        if parameter.endswith(".inWeight"):
            require(rows % 2 == 0, "Two equal input projections")
            slices = [(0, rows // 2), (rows // 2, rows // 2)]
        elif parameter.endswith(".xWeight"):
            rank, state = manifest["modelConfig"]["dt_rank"], manifest["modelConfig"]["N"]
            require(rows == rank + 2 * state, "Delta/B/C output dimensions")
            slices = [(0, rank), (rank, state), (rank + state, state)]
        else:
            slices = [(0, rows)]
        for offset, outputs in slices:
            for lanes in (1, 2, 4):
                for lane in range(lanes):
                    key = (lanes, layer, offset, outputs, lane)
                    name = "linearWeightBank" + str(len(expected_dispatch))
                    expected_dispatch.append((key, name))
                    require(name in banks, "Missing weight bank: " + name)
                    for address, actual in enumerate(banks[name]):
                        group, column = divmod(address, columns)
                        row = group * lanes + lane
                        group_limit = (outputs + lanes - 1) // lanes
                        expected = raw[(offset + row) * columns + column] if row < outputs else 0
                        require(actual == expected,
                                f"Weight mismatch bank={name} key={key} address={address} expected={expected} actual={actual}")
                        if row < outputs:
                            valid_addresses += 1
                        elif group < group_limit:
                            padded_addresses += 1
                        else:
                            zero_addresses += 1
    require(dispatch == expected_dispatch, "Dispatch bank identity/order differs from fixed matrix slices")
    require(source_bytes == source_path.read_bytes(), "Source changed during verification")
    report = {
        "status": "pass", "method": "Independent strict BSV parser of 256-bit bit-plane truth tables and direct bit-selector interpreter; direct frozen export bytes; generator not imported",
        "lanes_checked": [1, 2, 4], "banks_checked": len(banks), "pages_checked": len(pages),
        "bit_plane_truth_tables_checked": truth_tables,
        "addresses_per_bank": 8192, "coefficient_addresses_checked": len(banks) * 8192,
        "valid_weight_addresses": valid_addresses, "padded_lane_zero_addresses": padded_addresses,
        "out_of_range_zero_addresses": zero_addresses,
        "dispatch_and_selectors": "All function signatures, complete statement grammar, direct 8-bit address selects, signed return, page/bank identity, default-zero behavior, dispatch tuple and order checked",
        "non_affine_suffix_sha256": digest(marker + suffix),
        "source_sha256": digest(source_bytes),
        "checkpoint_sha256": digest((hardware / "model/checkpoint.pt").read_bytes()),
        "manifest_sha256": digest(manifest_bytes), "frozen_weight_tensor_sha256": tensor_hashes,
        "checker_sha256": digest(Path(__file__).read_bytes()),
        "scope": "Exhaustive combinational ROM interpretation; HDL compilation and kernel regression are separate checks",
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "banks_checked", "pages_checked", "coefficient_addresses_checked", "valid_weight_addresses", "padded_lane_zero_addresses", "out_of_range_zero_addresses")}))


if __name__ == "__main__":
    main()
