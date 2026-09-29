#!/usr/bin/env python3
"""Build and verify the complete ULX3S-85F baseline at 100 MHz / 25 MHz."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from place_core_reset import CORE_RESET_CELL, REPORT_PREFIX


ROUTE_CLOCKS = {"$glbnet$clocks_pll_clk_100mhz": 100.0,
                "$glbnet$CLK_clk_25mhz$TRELLIS_IO_IN": 25.0}


def route_clock_hook():
    # The placed JSON retains net locations but not propagated clock constraints.
    return ("# Restore the packed global clocks before route-only timing analysis.\n"
            + "clock_targets = " + repr(ROUTE_CLOCKS) + "\n"
            + "for name in clock_targets:\n"
            + "    if name not in ctx.nets:\n"
            + "        raise RuntimeError('Missing packed clock net: ' + name)\n"
            + "for name, frequency in clock_targets.items():\n"
            + "    ctx.addClock(name, frequency)\n")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hashes(hw, rootdir):
    sources = {"sway/hw/Makefile": hw / "Makefile",
               "sway/hw/reference/check_physical.py": Path(__file__).resolve(),
               "sway/hw/reference/merge_reset_controls.py": hw / "reference/merge_reset_controls.py",
               "sway/hw/reference/place_core_reset.py": hw / "reference/place_core_reset.py"}
    for directory in (hw, hw / "bsv", hw / "generated", hw / "rtl", hw / "generated/linear_rom"):
        for path in directory.glob("*"):
            if path.is_file() and path.suffix in {".bsv", ".v", ".hex"}:
                sources["sway/" + str(path.relative_to(hw.parent))] = path
    for directory in ("boards/ulx3s", "lib/bsv", "lib/rtl", "fpga/ecp5"):
        for path in (rootdir / directory).rglob("*"):
            if path.is_file() and path.suffix in {".bsv", ".v", ".lpf", ".mk"}:
                sources["blueyosys/" + str(path.relative_to(rootdir))] = path
    for name in ("build.mk", "boards/profiles.mk"):
        sources["blueyosys/" + name] = rootdir / name
    return {name: sha256(path) for name, path in sorted(sources.items())}


def git_head(directory):
    result = subprocess.run(["git", "-C", str(directory), "rev-parse", "HEAD"],
                            text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def tool_info(command, flag):
    executable = shutil.which(command)
    if executable is None:
        raise RuntimeError("Tool not found: " + command)
    result = subprocess.run([executable, flag], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError("Cannot read version: " + executable)
    return {"path": executable, "version": result.stdout.strip(), "sha256": sha256(Path(executable))}


def run_command(command, log, cwd):
    print("Running " + " ".join(command), flush=True)
    started = time.monotonic()
    with log.open("w") as output:
        result = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, check=False)
    return {"command": command, "cwd": str(cwd), "exit_code": result.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "log": log.name, "log_sha256": sha256(log)}


def packed_utilization(log):
    pattern = re.compile(r"^Info:\s+([A-Za-z0-9_]+):\s+(\d+)\s*/\s*(\d+)", re.M)
    return {name: {"used": int(used), "available": int(available)}
            for name, used, available in pattern.findall(log)}


def verify_reset_placement(console_log, placed_path):
    reports = [json.loads(line[len(REPORT_PREFIX):]) for line in console_log.splitlines()
               if line.startswith(REPORT_PREFIX)]
    if len(reports) != 1 or reports[0].get("cell") != CORE_RESET_CELL:
        raise RuntimeError("Missing or unexpected core-reset placement report")
    report = reports[0]
    placed = json.loads(placed_path.read_text())
    matches = [(name, module["cells"][CORE_RESET_CELL])
               for name, module in placed["modules"].items()
               if CORE_RESET_CELL in module.get("cells", {})]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one placed core-reset FF")
    module_name, cell = matches[0]
    actual = cell.get("attributes", {}).get("NEXTPNR_BEL")
    if cell["type"] != "TRELLIS_FF" or actual != report["bel"]:
        raise RuntimeError("Core-reset FF was not placed at the selected BEL")
    report["placed_bel"] = actual
    report["placed_module"] = module_name
    report["placed_bel_verified"] = True
    return report


def verify_clocks(report):
    clocks = report.get("fmax", {})
    if not clocks:
        raise RuntimeError("No clock timing results in nextpnr report")
    results = {}
    for domain, fragment, required in (("core", "clk_100mhz", 100.0), ("uart", "CLK_clk_25mhz", 25.0)):
        matches = [name for name in clocks if fragment in name]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one {domain} clock, found {matches}")
        name = matches[0]
        achieved = float(clocks[name]["achieved"])
        constraint = float(clocks[name]["constraint"])
        results[domain] = {"net": name, "achieved_mhz": achieved, "constraint_mhz": constraint,
                           "required_mhz": required, "pass": math.isfinite(achieved)
                           and math.isclose(constraint, required, rel_tol=0, abs_tol=0.001)
                           and achieved >= constraint and achieved >= required}
    for name, values in clocks.items():
        achieved, constraint = float(values["achieved"]), float(values["constraint"])
        if not (math.isfinite(achieved) and math.isfinite(constraint) and constraint > 0 and achieved >= constraint):
            raise RuntimeError(f"Clock timing failed: {name}: {achieved} / {constraint} MHz")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new or empty evidence directory")
    parser.add_argument("--bsc", default=os.environ.get("BSC", "bsc"))
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--nextpnr", default="nextpnr-ecp5")
    args = parser.parse_args()
    hw = Path(__file__).resolve().parent.parent
    rootdir, output = args.rootdir.resolve(), args.output.resolve()
    if not (rootdir / "build.mk").is_file():
        parser.error("blueYosys build.mk not found")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error("--output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    build = output / "build"
    result = {"status": "fail", "board": "ulx3s-85f", "top": "mkTop", "speed_grade": 6,
              "seed": 1, "router": "router1", "started_utc": datetime.now(timezone.utc).isoformat(),
              "sway_commit": git_head(hw), "blueyosys_commit": git_head(rootdir),
              "clock_targets_mhz": {"core": 100, "uart": 25}, "commands": []}
    started = time.monotonic()
    try:
        result["source_sha256"] = source_hashes(hw, rootdir)
        result["tools"] = {"bsc": tool_info(args.bsc, "-v"), "yosys": tool_info(args.yosys, "-V"),
                           "nextpnr": tool_info(args.nextpnr, "--version")}
        command = ["make", "-C", str(hw), "physical-netlist", f"ROOTDIR={rootdir}", "BOARD=ulx3s-85f",
                   f"BUILD_DIR={build}", "BSC=" + result["tools"]["bsc"]["path"],
                   "YOSYS=" + result["tools"]["yosys"]["path"]]
        synthesis = run_command(command, output / "synthesis.log", hw)
        result["commands"].append(synthesis)
        if synthesis["exit_code"]:
            raise RuntimeError("Compilation or synthesis failed; inspect synthesis.log")
        netlist_path = build / "mkTop.json"
        result["netlist_sha256"] = sha256(netlist_path)
        result["reset_merge"] = json.loads((build / "reset_merge.json").read_text())
        if result["reset_merge"]["output_sha256"] != result["netlist_sha256"]:
            raise RuntimeError("Netlist differs from the reset-sharing result")
        with netlist_path.open() as source:
            netlist = json.load(source)
        result["synthesis_cells"] = dict(sorted(Counter(cell["type"] for cell in netlist["modules"]["mkTop"]["cells"].values()).items()))
        del netlist
        result["generated_rtl_sha256"] = {path.name: sha256(path) for path in sorted(build.glob("*.v"))}
        lpf = build / "ulx3s.lpf"
        result["constraints"] = {"path": "build/ulx3s.lpf", "sha256": sha256(lpf)}
        reset_hook = output / "place_core_reset.py"
        shutil.copyfile(hw / "reference/place_core_reset.py", reset_hook)
        result["reset_placement"] = {"hook": reset_hook.name, "hook_sha256": sha256(reset_hook)}
        placed_path, placement_log = output / "placed.json", output / "placement.log"
        placement_report = output / "placement.json"
        common = [result["tools"]["nextpnr"]["path"], "--85k", "--package", "CABGA381", "--speed", "6",
                  "--seed", "1", "--freq", "100", "--router", "router1", "--lpf", str(lpf),
                  "--detailed-timing-report"]
        command = [*common, "--json", str(netlist_path), "--no-route", "--write", str(placed_path),
                   "--pre-place", str(reset_hook), "--report", str(placement_report), "--log", str(placement_log)]
        placement = run_command(command, output / "placement.console.log", hw)
        result["commands"].append(placement)
        if sha256(netlist_path) != result["netlist_sha256"]:
            raise RuntimeError("Netlist changed during placement")
        if sha256(reset_hook) != result["reset_placement"]["hook_sha256"]:
            raise RuntimeError("Core-reset placement hook changed during placement")
        log_text = placement_log.read_text(errors="replace") if placement_log.is_file() else ""
        result["packed_utilization"] = packed_utilization(log_text)
        result["placement_complete"] = placement["exit_code"] == 0 and placed_path.is_file() and placed_path.stat().st_size > 0
        if placement["exit_code"]:
            raise RuntimeError("Packing or placement failed; inspect placement.log")
        if not result["placement_complete"]:
            raise RuntimeError("No placed checkpoint produced")
        result["reset_placement"].update(verify_reset_placement(
            (output / "placement.console.log").read_text(errors="replace"), placed_path))
        if not result["packed_utilization"] or any(item["used"] > item["available"] for item in result["packed_utilization"].values()):
            raise RuntimeError("Missing utilization evidence or capacity exceeded")
        result["placed_netlist_sha256"] = sha256(placed_path)
        result["placement_report_sha256"] = sha256(placement_report)
        clock_hook = output / "route_clocks.py"
        clock_hook.write_text(route_clock_hook())
        result["route_clocks"] = {"targets_mhz": ROUTE_CLOCKS, "hook": clock_hook.name,
                                  "hook_sha256": sha256(clock_hook)}

        report_path, config_path, pnr_log = output / "nextpnr.json", output / "mkTop.config", output / "nextpnr.log"
        command = [*common, "--json", str(placed_path), "--no-pack", "--no-place",
                   "--pre-route", str(clock_hook),
                   "--report", str(report_path), "--log", str(pnr_log), "--textcfg", str(config_path)]
        physical = run_command(command, output / "nextpnr.console.log", hw)
        result["commands"].append(physical)
        if sha256(clock_hook) != result["route_clocks"]["hook_sha256"]:
            raise RuntimeError("Route clock constraints changed during routing")
        if sha256(reset_hook) != result["reset_placement"]["hook_sha256"]:
            raise RuntimeError("Core-reset placement hook changed during routing")
        if sha256(placed_path) != result["placed_netlist_sha256"]:
            raise RuntimeError("Placed checkpoint changed during routing")
        if sha256(netlist_path) != result["netlist_sha256"]:
            raise RuntimeError("Synthesized netlist changed during routing")
        if sha256(lpf) != result["constraints"]["sha256"]:
            raise RuntimeError("Constraints changed during placement and routing")
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
        result["reported_clocks"] = report.get("fmax", {})
        result["routing_complete"] = physical["exit_code"] == 0 and config_path.is_file() and config_path.stat().st_size > 0
        if physical["exit_code"]:
            raise RuntimeError("Routing or timing failed; inspect nextpnr.log")
        if not result["routing_complete"]:
            raise RuntimeError("No routed configuration produced")
        result["clocks"] = verify_clocks(report)
        if not all(clock["pass"] for clock in result["clocks"].values()):
            raise RuntimeError("The 100 MHz core and 25 MHz UART constraints were not both met")
        result["config_sha256"] = sha256(config_path)
        result["status"] = "pass"
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        result["error"] = str(exc)
    finally:
        try:
            result["source_hashes_unchanged"] = result.get("source_sha256") == source_hashes(hw, rootdir)
        except OSError:
            result["source_hashes_unchanged"] = False
        if not result["source_hashes_unchanged"]:
            result["status"] = "fail"
            result["source_error"] = "Sources changed during build or cannot be read"
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["evidence_sha256"] = {path.name: sha256(path) for path in sorted(output.iterdir())
                                     if path.is_file() and path.name != "report.json"}
        (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print("SWAY_PHYSICAL_" + result["status"].upper() + f" report={output / 'report.json'}")
    if result["status"] != "pass":
        print(result.get("error", result.get("source_error", "Verification failed")), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
