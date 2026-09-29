#!/usr/bin/env python3
"""Resume a verified placement checkpoint after a checker-only module-name fix.

This is a frozen evidence-continuation script, not an alternative timing policy.
The original synthesis/placement commands and every physical input are retained.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

REPOSITORY = "SeMinLim/sway"
PRIOR_COMMIT = "80fbfe4cdb635606c394f435b28c1102c9de5829"
PRIOR_RUN = 36506484774
PRIOR_JOB = 109208949816
ARTIFACT_ID = 11007703004
ARTIFACT_SHA256 = "da294c44d427df02ca932dda0c163cbb66c6a51fcda924d0721278abf9bd06db"
ARTIFACT_BYTES = 8572335
BLUEYOSYS_COMMIT = "3663e87b88146c248919923ea952e025944ca3e0"
CHECKER = "sway/hw/reference/check_physical.py"
CORRECTED_CHECKER_SHA256 = "a25abbc21a7a40de8e25a03d48bdd63683525c52bd4cee8fb4377070d3dab543"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(path, token):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPOSITORY + "/" + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootdir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--yosys", default="yosys")
    parser.add_argument("--nextpnr", default="nextpnr-ecp5")
    args = parser.parse_args()
    # The workflow invokes this script from the corrected, frozen checkout.
    hw = (Path.cwd() / "hw").resolve()
    sys.path.insert(0, str(hw / "reference"))
    import check_physical as check

    output, rootdir = args.output.resolve(), args.rootdir.resolve()
    started = time.monotonic()
    result = {"status": "fail", "commands": [], "sway_commit": check.git_head(hw),
              "started_utc": datetime.now(timezone.utc).isoformat()}
    current_sources = None
    try:
        require(not output.exists(), "Resume output must not already exist")
        require(check.git_head(rootdir) == BLUEYOSYS_COMMIT, "Wrong blueYosys checkout")
        current_sources = check.source_hashes(hw, rootdir)
        require(current_sources[CHECKER] == CORRECTED_CHECKER_SHA256,
                "Unexpected corrected physical checker")
        current_tools = {"bsc": check.tool_info(args.bsc, "-v"),
                         "yosys": check.tool_info(args.yosys, "-V"),
                         "nextpnr": check.tool_info(args.nextpnr, "--version")}
        token = os.environ["GH_TOKEN"]
        artifact = api(f"actions/artifacts/{ARTIFACT_ID}", token)
        require(artifact["id"] == ARTIFACT_ID and not artifact["expired"], "Wrong/expired artifact")
        require(artifact["name"] == "baseline-physical-" + PRIOR_COMMIT, "Wrong artifact name")
        require(artifact["digest"] == "sha256:" + ARTIFACT_SHA256, "Artifact API digest mismatch")
        require(artifact["size_in_bytes"] == ARTIFACT_BYTES, "Artifact API size mismatch")
        require(artifact["workflow_run"]["id"] == PRIOR_RUN and
                artifact["workflow_run"]["head_sha"] == PRIOR_COMMIT, "Artifact provenance mismatch")
        job = api(f"actions/jobs/{PRIOR_JOB}", token)
        require(job["run_id"] == PRIOR_RUN and job["head_sha"] == PRIOR_COMMIT and
                job["conclusion"] == "failure", "Original job provenance mismatch")
        with tempfile.TemporaryDirectory(prefix="sway-placement-") as temporary:
            staging = Path(temporary)
            archive = staging / "original.zip"
            download = subprocess.run([
                "curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3",
                "-H", "Authorization: Bearer " + token,
                "-H", "Accept: application/vnd.github+json",
                f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{ARTIFACT_ID}/zip",
                "--output", str(archive)], check=False)
            require(download.returncode == 0, "Original artifact download failed")
            require(archive.stat().st_size == ARTIFACT_BYTES, "Downloaded archive size mismatch")
            require(check.sha256(archive) == ARTIFACT_SHA256, "Downloaded archive SHA-256 mismatch")
            unpacked = staging / "unpacked"
            with zipfile.ZipFile(archive) as zipped:
                for entry in zipped.infolist():
                    path = Path(entry.filename)
                    require(not path.is_absolute() and ".." not in path.parts and
                            not stat.S_ISLNK(entry.external_attr >> 16), "Unsafe archive member")
                zipped.extractall(unpacked)
            prior_raw = (unpacked / "physical/report.json").read_bytes()
            prior = json.loads(prior_raw)
            require(prior["status"] == "fail" and prior.get("error") == "'mkTop'",
                    "Original failure was not the known module-name checker exception")
            require(prior["sway_commit"] == PRIOR_COMMIT and prior["blueyosys_commit"] == BLUEYOSYS_COMMIT,
                    "Original report commit mismatch")
            require(prior["source_hashes_unchanged"] and prior["placement_complete"],
                    "Original placement/source verification failed")
            require(len(prior["commands"]) == 2 and all(c["exit_code"] == 0 for c in prior["commands"]),
                    "Original synthesis and placement did not both succeed")
            require((prior["board"], prior["top"], prior["speed_grade"], prior["seed"], prior["router"]) ==
                    ("ulx3s-85f", "mkTop", 6, 1, "router1"), "Original physical configuration differs")
            require(prior["clock_targets_mhz"] == {"core": 100, "uart": 25}, "Original clock targets differ")
            require(len(prior["source_sha256"]) == 166, "Unexpected original source manifest size")
            require(set(prior["source_sha256"]) == set(current_sources), "Source manifest membership changed")
            changes = {name: {"before": old, "after": current_sources[name]}
                       for name, old in prior["source_sha256"].items() if current_sources[name] != old}
            require(set(changes) == {CHECKER}, "Only the physical checker may differ from placement sources")
            for name, original in prior["tools"].items():
                require(all(current_tools[name][key] == original[key] for key in ("sha256", "version")),
                        "Pinned tool differs from placement tool: " + name)
            original_physical = unpacked / "physical"
            original_files = {str(path.relative_to(original_physical)): check.sha256(path)
                              for path in sorted(original_physical.rglob("*")) if path.is_file()
                              and path != original_physical / "report.json"}
            for name, digest in prior["evidence_sha256"].items():
                require(check.sha256(original_physical / name) == digest, "Original evidence hash mismatch: " + name)
            for name, digest in prior["generated_rtl_sha256"].items():
                require(check.sha256(original_physical / "build" / name) == digest,
                        "Original generated RTL hash mismatch: " + name)
            require(check.sha256(original_physical / "build/mkTop.json") == prior["netlist_sha256"] ==
                    prior["reset_merge"]["output_sha256"], "Original synthesized netlist hash mismatch")
            require(json.loads((original_physical / "build/reset_merge.json").read_text()) == prior["reset_merge"],
                    "Original reset-sharing report mismatch")
            require(check.sha256(original_physical / prior["constraints"]["path"]) == prior["constraints"]["sha256"],
                    "Original LPF hash mismatch")
            hook = original_physical / prior["reset_placement"]["hook"]
            require(hook.name == "place_core_reset.py" and check.sha256(hook) ==
                    prior["reset_placement"]["hook_sha256"] == current_sources["sway/hw/reference/place_core_reset.py"],
                    "Original placement hook differs from current source")
            for command in prior["commands"]:
                require(check.sha256(original_physical / command["log"]) == command["log_sha256"],
                        "Original command log hash mismatch")
                require("--timing-allow-fail" not in command["command"] and "--force" not in command["command"],
                        "Original command used an impermissible timing override")
            place_command = prior["commands"][1]["command"]
            def option(flag):
                require(place_command.count(flag) == 1, "Missing/duplicate placement option: " + flag)
                return place_command[place_command.index(flag) + 1]
            require("--85k" in place_command and "--no-route" in place_command, "Wrong placement mode")
            for flag, value in (("--package", "CABGA381"), ("--speed", "6"), ("--seed", "1"),
                                ("--freq", "100"), ("--router", "router1")):
                require(option(flag) == value, "Original placement option differs: " + flag)
            require(Path(option("--pre-place")).name == hook.name, "Wrong original placement hook argument")
            require((unpacked / "sway-commit.txt").read_text().strip() == PRIOR_COMMIT,
                    "Original artifact checkout marker differs")
            # Copy the verified physical inputs only. Never overwrite the original
            # placement console, or the current workflow's open tee output.
            shutil.copytree(original_physical, output)
            (output / "prior-placement-report.json").write_bytes(prior_raw)
            for name in ("sway-commit.txt", "bsc-version.txt", "yosys-version.txt", "nextpnr-version.txt",
                         "blueyosys-commit.txt", "physical-run.log", "ci-summary.json"):
                original = unpacked / name
                if original.is_file():
                    shutil.copyfile(original, output / ("prior-" + name))
            (output.parent / "sway-commit.txt").write_text(check.git_head(hw) + "\n")
            result = copy.deepcopy(prior)
            result.update(status="fail", sway_commit=check.git_head(hw), source_sha256=current_sources,
                          tools=current_tools, started_utc=datetime.now(timezone.utc).isoformat())
            result.pop("error", None)
            result["resumed_from"] = {
                "commit": PRIOR_COMMIT, "run_id": PRIOR_RUN, "job_id": PRIOR_JOB,
                "artifact_id": ARTIFACT_ID, "artifact_sha256": ARTIFACT_SHA256,
                "artifact_size_bytes": ARTIFACT_BYTES, "artifact_api": artifact,
                "report_sha256": hashlib.sha256(prior_raw).hexdigest(),
                "report_path": "prior-placement-report.json",
                "source_sha256_before": prior["source_sha256"],
                "source_sha256_after": current_sources, "source_changes": changes,
                "tools_before": prior["tools"], "original_files_sha256": original_files,
                "reason": "The original checker assumed module mkTop; nextpnr writes the canonical module top. Exact synthesis and placed checkpoint are reused after validating the unique core-reset FF and BEL.",
            }
        build, placed_path = output / "build", output / "placed.json"
        lpf, netlist = build / "ulx3s.lpf", build / "mkTop.json"
        result["reset_placement"].update(check.verify_reset_placement(
            (output / "placement.console.log").read_text(errors="replace"), placed_path))
        require(result["packed_utilization"] and all(x["used"] <= x["available"]
                for x in result["packed_utilization"].values()), "Original capacity check failed")
        result["placed_netlist_sha256"] = check.sha256(placed_path)
        result["placement_report_sha256"] = check.sha256(output / "placement.json")
        clock_hook = output / "route_clocks.py"
        clock_hook.write_text(check.route_clock_hook())
        result["route_clocks"] = {"targets_mhz": check.ROUTE_CLOCKS, "hook": clock_hook.name,
                                  "hook_sha256": check.sha256(clock_hook)}
        report_path, config_path, pnr_log = output / "nextpnr.json", output / "mkTop.config", output / "nextpnr.log"
        require(not report_path.exists() and not config_path.exists(), "Original artifact unexpectedly contains routing outputs")
        command = [current_tools["nextpnr"]["path"], "--85k", "--package", "CABGA381", "--speed", "6",
                   "--seed", "1", "--freq", "100", "--router", "router1", "--lpf", str(lpf),
                   "--detailed-timing-report", "--json", str(placed_path), "--no-pack", "--no-place",
                   "--pre-route", str(clock_hook), "--report", str(report_path), "--log", str(pnr_log),
                   "--textcfg", str(config_path)]
        physical = check.run_command(command, output / "nextpnr.console.log", hw)
        result["commands"].append(physical)
        # Preserve every original file, including build outputs outside the
        # original top-level evidence manifest. The raw prior report is separate.
        for name, digest in original_files.items():
            require(check.sha256(output / name) == digest, "Original evidence changed during routing: " + name)
        for name, digest in prior["generated_rtl_sha256"].items():
            require(check.sha256(build / name) == digest, "Generated RTL changed during routing: " + name)
        require(check.sha256(netlist) == result["netlist_sha256"], "Synthesized netlist changed during routing")
        require(check.sha256(placed_path) == result["placed_netlist_sha256"], "Placed checkpoint changed during routing")
        require(check.sha256(lpf) == result["constraints"]["sha256"], "LPF changed during routing")
        require(check.sha256(clock_hook) == result["route_clocks"]["hook_sha256"], "Route clock hook changed")
        require(check.sha256(output / "place_core_reset.py") == result["reset_placement"]["hook_sha256"],
                "Placement hook changed during routing")
        timing = json.loads(report_path.read_text()) if report_path.is_file() else {}
        result["reported_clocks"] = timing.get("fmax", {})
        result["routing_complete"] = physical["exit_code"] == 0 and config_path.is_file() and config_path.stat().st_size > 0
        require(physical["exit_code"] == 0, "Routing or timing failed; inspect nextpnr.log")
        require(result["routing_complete"], "No routed configuration produced")
        result["clocks"] = check.verify_clocks(timing)
        require(all(clock["pass"] for clock in result["clocks"].values()),
                "The 100 MHz core and 25 MHz UART constraints were not both met")
        result["config_sha256"] = check.sha256(config_path)
        result["status"] = "pass"
    except Exception as exc:
        # Do not format subprocess arguments: they can contain the download token.
        result["status"] = "fail"
        result["error"] = str(exc)
    finally:
        output.mkdir(parents=True, exist_ok=True)
        try:
            result["source_hashes_unchanged"] = current_sources is not None and current_sources == check.source_hashes(hw, rootdir)
        except OSError:
            result["source_hashes_unchanged"] = False
        if not result["source_hashes_unchanged"]:
            result["status"] = "fail"
            result["source_error"] = "Sources changed during continuation or cannot be read"
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["evidence_sha256"] = {str(path.relative_to(output)): check.sha256(path)
                                     for path in sorted(output.rglob("*")) if path.is_file()
                                     and path != output / "report.json"}
        (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print("SWAY_PHYSICAL_" + result["status"].upper() + " report=" + str(output / "report.json"))
    if result["status"] != "pass":
        print(result.get("error", result.get("source_error", "Verification failed")), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
