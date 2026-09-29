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

import check_address_replicas
from check_address_replicas import audit_transition
from check_page_decoder_copies import FROZEN_ADDRESS_AUDITOR, audit as audit_page_decoders
from place_core_reset import CORE_RESET_CELL, REPORT_PREFIX
from check_lut_mapping import STATE_BLOCK_RAM, inspect_mapping, inspect_state_block_ram


RIPUP_MAKEFILE_LINE = "BOARD_PNR_FLAGS += --tmg-ripup\n"


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
               "sway/hw/reference/check_lut_mapping.py": hw / "reference/check_lut_mapping.py",
               "sway/hw/reference/replicate_rom_address.py": hw / "reference/replicate_rom_address.py",
               "sway/hw/reference/check_address_replicas.py": hw / "reference/check_address_replicas.py",
               "sway/hw/reference/replicate_page_decoders.py": hw / "reference/replicate_page_decoders.py",
               "sway/hw/reference/check_page_decoder_copies.py": hw / "reference/check_page_decoder_copies.py",
               "sway/hw/reference/merge_reset_controls.py": hw / "reference/merge_reset_controls.py",
               "sway/hw/reference/place_core_reset.py": hw / "reference/place_core_reset.py"}
    for directory in (hw, hw / "bsv", hw / "generated", hw / "rtl"):
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


def verify_state_block_ram_usage(mapping, stage, actual, evidence, instances=None):
    # Only the two independently verified scan-state DP16KD cells are permitted.
    # Coefficient cones are checked separately and cannot contain any memory.
    check = {"expected_dp16kd": len(STATE_BLOCK_RAM), "actual_dp16kd": actual, "evidence": evidence,
             "pass": type(actual) is int and actual == len(STATE_BLOCK_RAM)}
    if instances is not None:
        check["instances"] = instances
    mapping["checks"][stage] = check
    if not check["pass"]:
        raise RuntimeError(f"Expected only the two scan-state DP16KD cells at {stage}; found {actual!r}")


def verify_transform_evidence(build, sources, final_sha256):
    """Check every stored boundary before independently replaying both audits."""
    reset = json.loads((build / "reset_merge.json").read_text())
    address = json.loads((build / "address_replicas.json").read_text())
    page = json.loads((build / "page_decoders.json").read_text())
    page_audit = json.loads((build / "page_decoder_audit.json").read_text())
    raw_sha = sha256(build / "mkTop.before_address_replicas.json")
    mid_sha = sha256(build / "mkTop.before_page_decoders.json")
    rtl_sha = sha256(build / "mkTop.v")
    if sources["sway/hw/reference/check_address_replicas.py"] != FROZEN_ADDRESS_AUDITOR:
        raise RuntimeError("Original address FF startup/transition auditor is not the frozen reviewed version")
    if (reset.get("status") != "pass" or address.get("status") != "pass"
            or address.get("replica_registers") != 56
            or address["input_sha256"] != reset["output_sha256"]
            or raw_sha != address["input_sha256"]
            or address["output_sha256"] != mid_sha
            or address["rtl_sha256"] != rtl_sha
            or address["transform_sha256"] != sources["sway/hw/reference/replicate_rom_address.py"]
            or address["auditor_sha256"] != sources["sway/hw/reference/check_address_replicas.py"]):
        raise RuntimeError("Address FF evidence differs from the reset-sharing to56-replica chain")
    if (page.get("status") != "transformed_independent_audit_required"
            or page["input_sha256"] != mid_sha or page["output_sha256"] != final_sha256
            or sha256(build / "mkTop.json") != final_sha256
            or page["preserved_input"] != "mkTop.before_page_decoders.json"
            or page["rtl_sha256"] != rtl_sha
            or page["transform_sha256"] != sources["sway/hw/reference/replicate_page_decoders.py"]
            or page["auditor_sha256"] != sources["sway/hw/reference/check_page_decoder_copies.py"]
            or page["address_auditor_sha256"] != sources["sway/hw/reference/check_address_replicas.py"]):
        raise RuntimeError("Page-decoder transform evidence differs from the56-to91-replica chain")
    expected_inputs = {"before": mid_sha, "after": final_sha256, "rtl": rtl_sha,
                       "address-auditor": sources["sway/hw/reference/check_address_replicas.py"]}
    if (page_audit.get("status") != "pass" or page_audit.get("inputs_unchanged") is not True
            or page_audit["input_sha256"] != expected_inputs
            or page_audit["checker_sha256"] != sources["sway/hw/reference/check_page_decoder_copies.py"]):
        raise RuntimeError("Stored independent page-decoder proof does not match the current exact inputs")
    return reset, address, page, page_audit


def reuse_synthesis(previous, output, result):
    previous = previous.resolve()
    if previous == output or previous / "build" in output.parents:
        raise RuntimeError("Synthesis reuse requires a separate output directory")
    report_path = previous / "report.json"
    previous_sha = sha256(report_path)
    prior = json.loads(report_path.read_text())
    original = prior.get("commands", [{}])[0]
    provenance = {"directory": str(previous), "report_sha256": previous_sha,
                  "original_synthesis": original, "original_sway_commit": prior.get("sway_commit"),
                  "source_hash_differences": []}
    result["synthesis_reuse"] = provenance
    if prior.get("source_hashes_unchanged") is not True or original.get("exit_code") != 0:
        raise RuntimeError("Previous synthesis did not complete with unchanged sources")
    allowed = {"sway/hw/reference/check_physical.py", "sway/hw/reference/check_lut_mapping.py"}
    current, old = result["source_sha256"], prior["source_sha256"]
    differences = [{"path": name, "previous": old.get(name), "current": current.get(name)}
                   for name in sorted(current.keys() | old.keys()) if current.get(name) != old.get(name)]
    provenance["source_hash_differences"] = differences
    provenance["permitted_verification_file_changes"] = sorted(allowed)
    makefile_change = next((item for item in differences if item["path"] == "sway/hw/Makefile"), None)
    prior_makefile = None
    if makefile_change is not None:
        # Permit only this routing-only line; all synthesis inputs stay exact.
        previous_command = original.get("command")
        if (not isinstance(previous_command, list) or not previous_command
                or previous_command[0] != "make" or previous_command.count("-C") != 1
                or previous_command.count("physical-netlist") != 1):
            raise RuntimeError("Previous synthesis command cannot identify its source Makefile")
        directory_index = previous_command.index("-C") + 1
        if directory_index >= len(previous_command):
            raise RuntimeError("Previous synthesis command has no -C directory")
        previous_hw = Path(previous_command[directory_index]).resolve()
        if Path(original.get("cwd", "")).resolve() != previous_hw:
            raise RuntimeError("Previous synthesis -C directory and cwd differ")
        prior_makefile = previous_hw / "Makefile"
        current_makefile = Path(__file__).resolve().parent.parent / "Makefile"
        if sha256(prior_makefile) != old.get("sway/hw/Makefile"):
            raise RuntimeError("Previous source Makefile differs from the synthesis source hash")
        if sha256(current_makefile) != current.get("sway/hw/Makefile"):
            raise RuntimeError("Current Makefile changed during synthesis reuse verification")
        previous_bytes, current_bytes = prior_makefile.read_bytes(), current_makefile.read_bytes()
        added_line = RIPUP_MAKEFILE_LINE.encode()
        if (previous_bytes.count(added_line) != 0 or current_bytes.count(added_line) != 1
                or current_bytes.replace(added_line, b"", 1) != previous_bytes):
            raise RuntimeError("Makefile differs by more than the exact routing-only timing-ripup line")
        provenance["route_only_makefile_change"] = {
            "previous_source_makefile": str(prior_makefile),
            "previous_sha256": old["sway/hw/Makefile"],
            "current_sha256": current["sway/hw/Makefile"],
            "added_line": RIPUP_MAKEFILE_LINE.rstrip("\n"),
            "normalization": "Remove exactly one complete added routing-only line; remaining bytes equal the hash-verified previous source Makefile",
            "previous_command_source_verified": True,
            "all_other_makefile_bytes_identical": True,
            "synthesis_flags_changed": False}
        allowed.add("sway/hw/Makefile")
    if any(item["path"] not in allowed for item in differences):
        raise RuntimeError("Hardware/build sources differ from the previous synthesis")
    for tool in ("bsc", "yosys"):
        for field in ("version", "sha256"):
            if result["tools"][tool][field] != prior["tools"][tool][field]:
                raise RuntimeError("Synthesis reuse tool identity mismatch: " + tool + "." + field)
    provenance["bsc_yosys_identity_verified"] = True
    old_netlist = previous / "build/mkTop.json"
    old_log = previous / "synthesis.log"
    if sha256(old_netlist) != prior["netlist_sha256"] or sha256(old_log) != original["log_sha256"]:
        raise RuntimeError("Previous synthesized netlist or synthesis log changed")
    reset, replicas, pages, page_proof = verify_transform_evidence(previous / "build", old, prior["netlist_sha256"])
    if (reset != prior["reset_merge"]
            or sha256(previous / "build/address_replicas.json") != prior["address_replicas"]["report_sha256"]
            or sha256(previous / "build/page_decoders.json") != prior["page_decoders"]["transform_report_sha256"]
            or sha256(previous / "build/page_decoder_audit.json") != prior["page_decoders"]["independent_report_sha256"]):
        raise RuntimeError("Previous netlist does not match both independently audited replication stages")
    shutil.copytree(previous / "build", output / "build")
    shutil.copyfile(old_log, output / "synthesis.log")
    for name in ("mkTop.json", "mkTop.v", "reset_merge.json", "mkTop.before_address_replicas.json",
                 "address_replicas.json", "mkTop.before_page_decoders.json", "page_decoders.json",
                 "page_decoder_audit.json", "ulx3s.lpf"):
        if sha256(previous / "build" / name) != sha256(output / "build" / name):
            raise RuntimeError("Reused synthesis copy differs: " + name)
    if sha256(output / "build/mkTop.json") != prior["netlist_sha256"] or sha256(report_path) != previous_sha:
        raise RuntimeError("Previous synthesis evidence changed during copy")
    if sha256(output / "synthesis.log") != original["log_sha256"]:
        raise RuntimeError("Copied synthesis log differs")
    if prior_makefile is not None and sha256(prior_makefile) != old["sway/hw/Makefile"]:
        raise RuntimeError("Previous source Makefile changed during synthesis reuse")
    provenance["copied_netlist_sha256"] = prior["netlist_sha256"]
    provenance["status"] = "verified"
    return {"command": None, "execution": "reused", "exit_code": 0, "elapsed_seconds": 0,
            "log": "synthesis.log", "log_sha256": original["log_sha256"],
            "provenance": "synthesis_reuse; synthesis was not executed again"}


def property_bool(value):
    if not isinstance(value, str) or not value.strip() or set(value.strip()) - {"0", "1"}:
        return None
    parsed = int(value.strip(), 2)
    return bool(parsed) if parsed in (0, 1) else None


def property_true(value):
    return property_bool(value) is True


def verify_router_settings(placed_path, require_ripup):
    design = json.loads(placed_path.read_text())
    if set(design["modules"]) != {"top"}:
        raise RuntimeError("Expected one complete placed top module")
    settings = design["modules"]["top"]["settings"]
    for key, value in (("router", "router1"), ("arch.name", "ecp5"), ("arch.type", "lfe5u_85f"),
                       ("arch.package", "CABGA381"), ("arch.speed", "6"), ("placer", "heap")):
        if settings.get(key) != value:
            raise RuntimeError("Unexpected resumed physical setting: " + key)
    if not property_true(settings.get("timing_driven")) or property_bool(settings.get("auto_freq")) is not False:
        raise RuntimeError("Fixed-frequency timing-driven implementation required")
    if float(settings.get("target_freq", "nan")) != 100000000.0:
        raise RuntimeError("Resumed core frequency must remain 100 MHz")
    strict_overrides = {}
    for key in ("timing/allowFail", "timing/ignoreLoops", "timing/ignoreRelClk"):
        value = settings.get(key)
        if value is not None and property_bool(value) is not False:
            raise RuntimeError("Strict timing override is enabled or malformed: " + key)
        strict_overrides[key] = None if value is None else False
    ripup = settings.get("router/tmg_ripup")
    if (require_ripup and not property_true(ripup)) or (ripup is not None and not property_true(ripup)):
        raise RuntimeError("Checkpoint would disable timing-driven ripup")
    return {"router": settings["router"], "timing_driven": True, "target_freq_hz": 100000000,
            "router_tmg_ripup": None if ripup is None else True,
            "ripup_key_absent_or_true": True, "actual_settings_verified": True,
            "strict_timing_overrides_disabled": strict_overrides,
            "arch_type": settings["arch.type"], "speed_grade": settings["arch.speed"],
            "package": settings["arch.package"], "placer": settings["placer"],
            "recorded_seed_state": settings["seed"]}


def reuse_placement(previous, output, result):
    previous = previous.resolve()
    report_path = previous / "report.json"
    report_sha = sha256(report_path)
    prior = json.loads(report_path.read_text())
    original = prior["commands"][1]
    if (prior.get("source_hashes_unchanged") is not True or prior.get("placement_complete") is not True
            or original.get("exit_code") != 0 or original.get("execution") == "reused"):
        raise RuntimeError("Placement reuse requires a completed original placement with unchanged sources")
    if result["synthesis_reuse"]["report_sha256"] != report_sha:
        raise RuntimeError("Synthesis and placement reuse must come from the same unchanged report")
    if prior.get("blueyosys_commit") != result["blueyosys_commit"]:
        raise RuntimeError("Placement reuse blueYosys revision differs")
    for field in ("version", "sha256"):
        if prior["tools"]["nextpnr"][field] != result["tools"]["nextpnr"][field]:
            raise RuntimeError("Placement reuse nextpnr identity differs: " + field)
    if (prior["netlist_sha256"] != result["netlist_sha256"]
            or prior["constraints"] != result["constraints"]
            or prior["reset_placement"]["hook_sha256"] != result["reset_placement"]["hook_sha256"]):
        raise RuntimeError("Placement reuse mapped netlist, LPF or reset hook differs")
    if (prior["clock_targets_mhz"] != {"core": 100, "uart": 25}
            or prior["route_clocks"]["targets_mhz"] != ROUTE_CLOCKS
            or prior["seed"] != 1 or prior["router"] != "router1" or prior["speed_grade"] != 6):
        raise RuntimeError("Placement reuse physical targets differ")
    expected = [prior["tools"]["nextpnr"]["path"], "--85k", "--package", "CABGA381", "--speed", "6",
                "--seed", "1", "--freq", "100", "--router", "router1", "--lpf", str(previous / "build/ulx3s.lpf"),
                "--detailed-timing-report"]
    if "--tmg-ripup" in original["command"]:
        expected.append("--tmg-ripup")
    expected += ["--json", str(previous / "build/mkTop.json"), "--no-route", "--write", str(previous / "placed.json"),
                 "--pre-place", str(previous / "place_core_reset.py"), "--report", str(previous / "placement.json"),
                 "--log", str(previous / "placement.log")]
    if original["command"] != expected or original.get("cwd") != prior["commands"][0].get("cwd"):
        raise RuntimeError("Previous placement command differs from the reviewed seed-1 100 MHz flow")
    expected_hashes = {"placed.json": prior["placed_netlist_sha256"],
                       "placement.json": prior["placement_report_sha256"],
                       "placement.console.log": original["log_sha256"],
                       "placement.log": prior["evidence_sha256"]["placement.log"]}
    for name, digest in expected_hashes.items():
        if sha256(previous / name) != digest:
            raise RuntimeError("Previous placement evidence changed: " + name)
        shutil.copyfile(previous / name, output / name)
        if sha256(output / name) != digest:
            raise RuntimeError("Placement evidence copy differs: " + name)
    if (sha256(previous / "place_core_reset.py") != result["reset_placement"]["hook_sha256"]
            or sha256(previous / "route_clocks.py") != prior["route_clocks"]["hook_sha256"]
            or sha256(previous / "build/ulx3s.lpf") != result["constraints"]["sha256"]):
        raise RuntimeError("Prior hook or constraint evidence changed")
    settings = verify_router_settings(output / "placed.json", require_ripup=False)
    # Verify these again in the ordinary placement checks below. This function
    # never edits the checkpoint or substitutes a re-created placement report.
    if sha256(report_path) != report_sha:
        raise RuntimeError("Previous physical report changed during placement reuse")
    for name, digest in expected_hashes.items():
        if sha256(previous / name) != digest:
            raise RuntimeError("Previous placement evidence changed during reuse: " + name)
    result["placement_reuse"] = {"status": "verified", "directory": str(previous),
        "report_sha256": report_sha, "original_placement": original, "copied_sha256": expected_hashes,
        "settings": settings, "checkpoint_bytes_identical": True, "checkpoint_transformation": "none",
        "hardware_constraints_tools_and_hooks_identical": True,
        "prior_route_clocks_sha256": prior["route_clocks"]["hook_sha256"],
        "new_routing_option": "--tmg-ripup", "prior_routing_result_is_not_reused": True}
    return {"command": None, "execution": "reused", "exit_code": 0, "elapsed_seconds": 0,
            "log": "placement.console.log", "log_sha256": original["log_sha256"],
            "provenance": "placement_reuse; verified existing checkpoint, placement was not executed again"}


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
    parser.add_argument("--reuse-synthesis", type=Path, help="reuse verified synthesis from a previous evidence directory")
    parser.add_argument("--reuse-placement", type=Path,
                        help="reuse an original verified placed checkpoint; implies same-source synthesis reuse")
    args = parser.parse_args()
    if args.reuse_placement:
        if args.reuse_synthesis and args.reuse_synthesis.resolve() != args.reuse_placement.resolve():
            parser.error("--reuse-placement and --reuse-synthesis must identify the same original run")
        args.reuse_synthesis = args.reuse_placement
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
              "clock_targets_mhz": {"core": 100, "uart": 25}, "commands": [],
              "affine_weight_mapping": {
                  "status": "fail", "expected": "Combinational fixed-coefficient LUT cones; zero affine block RAM",
                  "scope": "All 17 affine coefficient cones; only the two named dynamic scan-state DP16KD cells are allowed",
                  "distributed_ram": "Permitted for dynamic FIFOs and scan state", "checks": {}}}
    started = time.monotonic()
    try:
        result["source_sha256"] = source_hashes(hw, rootdir)
        result["tools"] = {"bsc": tool_info(args.bsc, "-v"), "yosys": tool_info(args.yosys, "-V"),
                           "nextpnr": tool_info(args.nextpnr, "--version")}
        command = ["make", "-C", str(hw), "physical-netlist", f"ROOTDIR={rootdir}", "BOARD=ulx3s-85f",
                   f"BUILD_DIR={build}", "BSC=" + result["tools"]["bsc"]["path"],
                   "YOSYS=" + result["tools"]["yosys"]["path"]]
        synthesis = (reuse_synthesis(args.reuse_synthesis, output, result) if args.reuse_synthesis
                     else run_command(command, output / "synthesis.log", hw))
        result["commands"].append(synthesis)
        if synthesis["exit_code"]:
            raise RuntimeError("Compilation or synthesis failed; inspect synthesis.log")
        netlist_path = build / "mkTop.json"
        result["netlist_sha256"] = sha256(netlist_path)
        reset_report, replica_report, page_report, stored_page_audit = verify_transform_evidence(
            build, result["source_sha256"], result["netlist_sha256"])
        result["reset_merge"] = reset_report
        replica_before = build / "mkTop.before_address_replicas.json"
        page_before = build / "mkTop.before_page_decoders.json"
        rtl_text = (build / "mkTop.v").read_text()
        # Audit raw->mid56 first, releasing raw before loading the final design.
        raw = json.loads(replica_before.read_text())
        middle = json.loads(page_before.read_text())
        result["address_replicas"] = audit_transition(raw, middle, rtl_text)
        del raw
        result["address_replicas"]["report_sha256"] = sha256(build / "address_replicas.json")
        result["address_replicas"].update({"scope": "Reset-shared raw netlist to56 low-address FF replicas",
            "input_sha256": replica_report["input_sha256"], "output_sha256": replica_report["output_sha256"]})
        with netlist_path.open() as source:
            netlist = json.load(source)
        # JSON object keys (including owner-group integers) normalize to strings
        # in saved proofs, so compare the same serialized representation.
        page_proof = json.loads(json.dumps(audit_page_decoders(middle, netlist, rtl_text, check_address_replicas)))
        del middle
        if any(stored_page_audit.get(key) != value for key, value in page_proof.items()):
            raise RuntimeError("Replayed page-decoder proof differs from the stored independent audit")
        if page_report.get("prewrite_audit") != page_proof:
            raise RuntimeError("Page transform prewrite proof differs from the independently replayed audit")
        result["page_decoders"] = page_proof
        result["page_decoders"].update({"input_sha256": page_report["input_sha256"],
            "output_sha256": page_report["output_sha256"],
            "transform_report_sha256": sha256(build / "page_decoders.json"),
            "independent_report_sha256": sha256(build / "page_decoder_audit.json"),
            "transform_sha256": result["source_sha256"]["sway/hw/reference/replicate_page_decoders.py"],
            "checker_sha256": result["source_sha256"]["sway/hw/reference/check_page_decoder_copies.py"]})
        # Recheck the complete file/hash boundary after both independent audits.
        if verify_transform_evidence(build, result["source_sha256"], result["netlist_sha256"]) != (
                reset_report, replica_report, page_report, stored_page_audit):
            raise RuntimeError("Transform evidence changed during independent verification")
        result["transform_chain"] = {"status": "pass", "raw_sha256": replica_report["input_sha256"],
            "mid56_sha256": replica_report["output_sha256"], "final91_sha256": result["netlist_sha256"],
            "stages_independently_replayed": ["address_ff_copies", "pure_high_page_decoder_copies"]}
        result["synthesis_cells"] = dict(sorted(Counter(cell["type"] for cell in netlist["modules"]["mkTop"]["cells"].values()).items()))
        mapping = result["affine_weight_mapping"]
        rtl_path = build / "mkTop.v"
        mapping.update({"netlist_sha256": result["netlist_sha256"], "rtl_sha256": sha256(rtl_path),
                        "checker_sha256": result["source_sha256"]["sway/hw/reference/check_lut_mapping.py"]})
        inspect_mapping(netlist, rtl_path.read_text(), mapping)
        if sha256(rtl_path) != mapping["rtl_sha256"]:
            raise RuntimeError("Generated RTL changed during coefficient-cone inspection")
        verify_state_block_ram_usage(mapping, "mapped_netlist", mapping["dp16kd"]["actual_total"],
                                     "build/mkTop.json: all module cell instances", mapping["dp16kd"]["instances"])
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
                  "--detailed-timing-report", "--tmg-ripup"]
        command = [*common, "--json", str(netlist_path), "--no-route", "--write", str(placed_path),
                   "--pre-place", str(reset_hook), "--report", str(placement_report), "--log", str(placement_log)]
        placement = (reuse_placement(args.reuse_placement, output, result) if args.reuse_placement
                     else run_command(command, output / "placement.console.log", hw))
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
        verify_state_block_ram_usage(result["affine_weight_mapping"], "packed_placement",
                                     result["packed_utilization"].get("DP16KD", {}).get("used"),
                                     "placement.log: packed device utilization")
        state_ram = inspect_state_block_ram(json.loads(placed_path.read_text()))
        result["affine_weight_mapping"]["placed_dp16kd"] = state_ram
        if not state_ram["pass"]:
            raise RuntimeError("Placed DP16KD instances differ from the two allowed scan-state memories")
        result["placed_netlist_sha256"] = sha256(placed_path)
        result["placement_report_sha256"] = sha256(placement_report)
        result["routing_settings"] = {"requested_tmg_ripup": True,
            "checkpoint": verify_router_settings(placed_path, require_ripup=False),
            "checkpoint_not_modified": True}
        clock_hook = output / "route_clocks.py"
        clock_hook.write_text(route_clock_hook())
        result["route_clocks"] = {"targets_mhz": ROUTE_CLOCKS, "hook": clock_hook.name,
                                  "hook_sha256": sha256(clock_hook)}

        if (args.reuse_placement and result["route_clocks"]["hook_sha256"] !=
                result["placement_reuse"]["prior_route_clocks_sha256"]):
            raise RuntimeError("Reused placement route clock hook differs")
        routed_path = output / "routed.json"
        report_path, config_path, pnr_log = output / "nextpnr.json", output / "mkTop.config", output / "nextpnr.log"
        command = [*common, "--json", str(placed_path), "--no-pack", "--no-place",
                   "--pre-route", str(clock_hook),
                   "--report", str(report_path), "--log", str(pnr_log), "--textcfg", str(config_path),
                   "--write", str(routed_path)]
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
        if routed_path.is_file():
            result["routed_netlist_sha256"] = sha256(routed_path)
            result["routing_settings"]["actual_routed"] = verify_router_settings(routed_path, require_ripup=True)
        else:
            raise RuntimeError("No routed checkpoint to verify actual timing-ripup setting")
        rounds = re.findall(r"(\d+) arcs ripped up due to negative slack WNS=([-0-9.]+)ns TNS=([-0-9.]+)ns", pnr_log.read_text())
        # A route that already meets timing needs no negative-slack ripup round.
        # The actual routed setting is independently verified above.
        result["routing_settings"]["timing_ripup_rounds"] = [
            {"arcs_ripped_up": int(arcs), "wns_ns": float(wns), "tns_ns": float(tns)} for arcs, wns, tns in rounds]
        result["routing_settings"]["runtime_flag_verified"] = True
        result["routing_complete"] = physical["exit_code"] == 0 and config_path.is_file() and config_path.stat().st_size > 0
        if physical["exit_code"]:
            raise RuntimeError("Routing or timing failed; inspect nextpnr.log")
        if not result["routing_complete"]:
            raise RuntimeError("No routed configuration produced")
        result["routed_utilization"] = report.get("utilization", {})
        verify_state_block_ram_usage(result["affine_weight_mapping"], "routed_design",
                                     result["routed_utilization"].get("DP16KD", {}).get("used"),
                                     "nextpnr.json: utilization.DP16KD.used")
        result["affine_weight_mapping"]["status"] = "pass"
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
