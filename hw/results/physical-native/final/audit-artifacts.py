#!/usr/bin/env python3
"""Independently audit routed continuation and frozen functional/native evidence.

Run from the tested checkout with GH_TOKEN, SWAY_TESTED_COMMIT,
SWAY_CI_RUN_ID, SWAY_PHYSICAL_JOB_ID, SWAY_FUNCTIONAL_COMMIT and
SWAY_FUNCTIONAL_RUN_ID set. Fails closed on missing or mismatched evidence.
"""

import re

def inspect_operand_control(rtl, expected_operands=None):
    """Inspect BSC continuous-assignment cones at native DSP operand ports.

    Keep write-presence signals outside the operand cones: the compiler may
    share those signals with valid-bit generation. Unresolved identifiers are
    reported as leaves; this function does not parse procedural always blocks.
    The focused test has direct assignments from registered ROM data outputs.
    """
    names = sorted(set(re.findall(r"(?<![\w$])[\w$]*[ab]Wire[$_]whas\b", rtl)))
    drivers = dict(re.findall(r"\bassign\s+([\w$]+)\s*=\s*(.*?);", rtl, re.S))
    ports = re.findall(r"\.dataa[xy]\s*\(\s*([\w$]+)\s*\)", rtl)
    cones = {}
    for signal in ports:
        pending, seen = [signal], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in drivers:
                pending.extend(re.findall(r"[A-Za-z_][\w$]*", drivers[current]))
        cones[signal] = {
            "signals": sorted(seen),
            "assignments": {s: drivers[s] for s in sorted(seen) if s in drivers},
            "leaf_identifiers": sorted(seen - drivers.keys()),
            "write_presence_dependencies": sorted(set(names) & seen),
        }
    report = {
        "status": "pass",
        "operand_ports": ports,
        "operand_assignments": [
            "assign " + signal + " = " + drivers[signal] + ";"
            for signal in ports if signal in drivers
        ],
        "operand_dependency_cones": cones,
        "retained_write_presence_signals": names,
        "retained_write_presence_uses": [
            line.strip() for line in rtl.splitlines()
            if any(name in line for name in names)
        ],
        "scope": "Continuous-assignment operand cones; dollar and underscore identifiers supported. Presence signals outside operand cones are permitted and reported.",
    }
    assert ports and all(signal in drivers for signal in ports), "Operand drivers missing"
    if expected_operands is not None:
        assert len(ports) == expected_operands, "Unexpected operand port count"
    assert all(not cone["write_presence_dependencies"] for cone in cones.values()), "Operand data cone depends on write-presence control"
    return report

import ast,hashlib,json,math,os,re,subprocess,sys,tempfile,urllib.request,zipfile
from pathlib import Path
from datetime import datetime,timezone
repo="SeMinLim/sway"
commit=os.environ["SWAY_TESTED_COMMIT"]
run_id=int(os.environ["SWAY_CI_RUN_ID"])
job_id=int(os.environ["SWAY_PHYSICAL_JOB_ID"])
functional_commit=os.environ["SWAY_FUNCTIONAL_COMMIT"]
functional_run_id=int(os.environ["SWAY_FUNCTIONAL_RUN_ID"])
original_physical_artifact_id=11007703004
original_physical_artifact_digest="da294c44d427df02ca932dda0c163cbb66c6a51fcda924d0721278abf9bd06db"
assert all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, functional_commit))
assert run_id > 0 and job_id > 0 and functional_run_id > 0
assert functional_commit == "80fbfe4cdb635606c394f435b28c1102c9de5829"
assert functional_run_id == 36506484774
token=os.environ["GH_TOKEN"]
def api(path):
    request=urllib.request.Request("https://api.github.com/repos/"+repo+"/"+path,headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)
artifacts=api(f"actions/runs/{run_id}/artifacts")["artifacts"]
functional_artifacts=api(f"actions/runs/{functional_run_id}/artifacts")["artifacts"]
artifact=next(a for a in artifacts if a["name"]=="baseline-physical-"+commit)
assert not artifact["expired"]
url=f'https://api.github.com/repos/{repo}/actions/artifacts/{artifact["id"]}/zip'
download=subprocess.run(["curl","--fail","--silent","--show-error","--location","--retry","3","-H","Authorization: Bearer "+token,"-H","Accept: application/vnd.github+json",url,"--output","evidence.zip"],check=False)
assert download.returncode==0,"Artifact download failed"
sha=lambda data: hashlib.sha256(data).hexdigest()
archive=Path("evidence.zip").read_bytes()
assert sha(archive)==artifact["digest"].removeprefix("sha256:")
original_artifact=next(a for a in functional_artifacts if a["id"]==original_physical_artifact_id)
assert original_artifact["name"] == "baseline-physical-" + functional_commit
assert not original_artifact["expired"]
assert original_artifact["digest"] == "sha256:" + original_physical_artifact_digest
original_url=f'https://api.github.com/repos/{repo}/actions/artifacts/{original_physical_artifact_id}/zip'
assert subprocess.run(["curl","--fail","--silent","--show-error","--location","--retry","3",
                       "-H","Authorization: Bearer "+token,"-H","Accept: application/vnd.github+json",
                       original_url,"--output","original-placement.zip"],check=False).returncode==0, "Original artifact download failed"
original_archive=Path("original-placement.zip").read_bytes()
assert sha(original_archive) == original_physical_artifact_digest
assert len(original_archive) == original_artifact["size_in_bytes"]
original_job=api("actions/jobs/109208949816")
assert original_job["conclusion"] == "failure"
assert original_job["run_id"] == functional_run_id and original_job["head_sha"] == functional_commit
assert next(step for step in original_job["steps"] if step["name"] == "Generate bitstream after strict physical pass")["conclusion"] == "skipped"
with zipfile.ZipFile("original-placement.zip") as original_zip:
    original_report_raw=original_zip.read("physical/report.json")
    original_report=json.loads(original_report_raw)
    original_files_sha256 = {name.removeprefix("physical/"): sha(original_zip.read(name))
                             for name in original_zip.namelist()
                             if name.startswith("physical/") and not name.endswith("/")
                             and name != "physical/report.json"}
    assert original_zip.read("sway-commit.txt").decode().strip() == functional_commit
    assert original_report["sway_commit"] == functional_commit
    assert original_report["status"] == "fail" and original_report["error"] == "'mkTop'"
    assert original_report["source_hashes_unchanged"] and original_report["placement_complete"]
    assert len(original_report["commands"]) == 2 and all(c["exit_code"] == 0 for c in original_report["commands"])
    assert not original_report.get("routing_complete", False)
    assert "physical/mkTop.bit" not in original_zip.namelist()
    for name,h in original_report["evidence_sha256"].items():
        assert sha(original_zip.read("physical/" + name)) == h, name
    for name,h in original_report["generated_rtl_sha256"].items():
        assert sha(original_zip.read("physical/build/" + name)) == h, name
    assert sha(original_zip.read("physical/build/mkTop.json")) == original_report["netlist_sha256"] == original_report["reset_merge"]["output_sha256"]
    assert sha(original_zip.read("physical/" + original_report["constraints"]["path"])) == original_report["constraints"]["sha256"]

job=api(f"actions/jobs/{job_id}")
assert job["conclusion"]=="success"
bit_step=next(s for s in job["steps"] if s["name"]=="Generate bitstream after strict physical pass")
assert bit_step["conclusion"]=="success"
assert subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==commit
subprocess.run(["git", "fetch", "--quiet", "--depth=1", "origin", functional_commit], check=True)
changed_hw=subprocess.check_output(["git", "diff", "--name-only", functional_commit, commit, "--", "hw"], text=True).splitlines()
changed_production = [name for name in changed_hw if not name.startswith("hw/results/")]
assert changed_production == ["hw/reference/check_physical.py"], changed_production
resume_script = Path("hw/results/physical-native/reset-placement/resume-routing.py").read_bytes()
assert sha(resume_script) == "92eb2fe1b82314c353b7f7127a09705e2a01fdee7951bee260bd65244ceef9b2"
old_checker=subprocess.check_output(["git", "show", functional_commit + ":hw/reference/check_physical.py"])
assert sha(old_checker) == original_report["source_sha256"]["sway/hw/reference/check_physical.py"]

ast.parse(Path("hw/reference/generate.py").read_text())
subprocess.run(["git","clone","--quiet","https://github.com/SeMinLim/blueyosys.git","blueyosys-evidence"],check=True)
subprocess.run(["git","-C","blueyosys-evidence","checkout","--quiet","--detach","3663e87b88146c248919923ea952e025944ca3e0"],check=True)
with zipfile.ZipFile("evidence.zip") as z:
    report_raw=z.read("physical/report.json")
    r=json.loads(report_raw)
    assert r["status"]=="pass" and r["sway_commit"]==commit
    assert z.read("sway-commit.txt").decode().strip()==commit
    assert r["blueyosys_commit"]=="3663e87b88146c248919923ea952e025944ca3e0"
    assert r["source_hashes_unchanged"] and r["placement_complete"] and r["routing_complete"]
    resumed=r["resumed_from"]
    assert resumed["commit"] == functional_commit and resumed["run_id"] == functional_run_id
    assert resumed["job_id"] == 109208949816 and resumed["artifact_id"] == original_physical_artifact_id
    assert resumed["artifact_sha256"] == original_physical_artifact_digest
    assert resumed["artifact_size_bytes"] == len(original_archive)
    assert resumed["report_sha256"] == sha(original_report_raw)
    assert resumed["report_path"] == "prior-placement-report.json"
    assert resumed["artifact_api"]["id"] == original_physical_artifact_id
    assert resumed["artifact_api"]["digest"] == "sha256:" + original_physical_artifact_digest
    assert z.read("physical/prior-placement-report.json") == original_report_raw
    assert r["commands"][:2] == original_report["commands"]
    assert resumed["source_sha256_before"] == original_report["source_sha256"]
    assert resumed["source_sha256_after"] == r["source_sha256"]
    assert set(r["source_sha256"]) == set(original_report["source_sha256"])
    source_changes={name: {"before": original_report["source_sha256"][name], "after": value}
                    for name,value in r["source_sha256"].items()
                    if value != original_report["source_sha256"][name]}
    assert set(source_changes) == {"sway/hw/reference/check_physical.py"}
    assert resumed["source_changes"] == source_changes
    assert resumed["tools_before"] == original_report["tools"]
    assert resumed["original_files_sha256"] == original_files_sha256
    for name, digest in original_files_sha256.items():
        assert sha(z.read("physical/" + name)) == digest, "Original file changed: " + name
    for tool in original_report["tools"]:
        for key in ("version", "sha256"):
            assert r["tools"][tool][key] == original_report["tools"][tool][key]
    for key in ("netlist_sha256", "reset_merge", "synthesis_cells", "generated_rtl_sha256", "constraints", "packed_utilization"):
        assert r[key] == original_report[key], key
    for name,h in original_report["evidence_sha256"].items():
        assert sha(z.read("physical/" + name)) == h, "Original placement evidence changed: " + name
    assert r["placed_netlist_sha256"] == original_report["evidence_sha256"]["placed.json"]
    assert r["placement_report_sha256"] == original_report["evidence_sha256"]["placement.json"]

    assert (r["board"],r["top"],r["speed_grade"],r["seed"],r["router"])==("ulx3s-85f","mkTop",6,1,"router1")
    assert len(r["commands"])==3 and all(c["exit_code"]==0 for c in r["commands"])
    assert all("--timing-allow-fail" not in c["command"] and "--force" not in c["command"] for c in r["commands"])
    def option(command, flag):
        assert command.count(flag) == 1, flag
        return command[command.index(flag) + 1]
    place_command, route_command = [c["command"] for c in r["commands"][1:]]
    for command in (place_command, route_command):
        assert "--85k" in command
        for flag, value in (("--package", "CABGA381"), ("--speed", "6"),
                            ("--seed", "1"), ("--freq", "100"), ("--router", "router1")):
            assert option(command, flag) == value
    assert "--no-route" in place_command
    assert "--no-pack" in route_command and "--no-place" in route_command
    assert Path(option(place_command, "--json")).name == "mkTop.json"
    assert Path(option(route_command, "--json")).name == "placed.json"
    for name,h in r["evidence_sha256"].items():
        assert sha(z.read("physical/"+name))==h,name
    for name,h in r["generated_rtl_sha256"].items():
        assert sha(z.read("physical/build/"+name))==h,name
    assert sha(z.read("physical/build/mkTop.json"))==r["netlist_sha256"]==r["reset_merge"]["output_sha256"]
    assert sha(z.read("physical/placed.json"))==r["placed_netlist_sha256"]
    reset = r["reset_placement"]
    assert reset["hook"] == "place_core_reset.py"
    reset_hook = z.read("physical/" + reset["hook"])
    assert sha(reset_hook) == reset["hook_sha256"]
    assert reset_hook == Path("hw/reference/place_core_reset.py").read_bytes()
    assert r["source_sha256"]["sway/hw/reference/place_core_reset.py"] == reset["hook_sha256"]
    assert Path(option(place_command, "--pre-place")).name == reset["hook"]
    assert "--pre-place" not in route_command
    assert '--pre-place "$(PROJECT_DIR)/reference/place_core_reset.py"' in Path("hw/Makefile").read_text()
    reset_prefix = "SWAY_RESET_PLACEMENT "
    reset_lines = [line[len(reset_prefix):] for line in z.read("physical/placement.console.log").decode().splitlines()
                   if line.startswith(reset_prefix)]
    assert len(reset_lines) == 1
    selected_reset = json.loads(reset_lines[0])
    assert all(reset[key] == value for key, value in selected_reset.items())
    assert reset["cell"] == "clocks_coreReset.OUT_RST_TRELLIS_FF_Q"
    assert reset["cell_type"] == "TRELLIS_FF"
    assert reset["selection"] == "nearest-available-FF-to-grid-center"
    assert reset["placed_bel_verified"] and reset["placed_bel"] == reset["bel"]
    assert 0 < reset["available_ff_sites"] <= reset["ff_sites"]
    x, y, zloc = reset["location"]
    xmin, ymin, xmax, ymax = reset["ff_grid_bounds"]
    assert xmin <= x <= xmax and ymin <= y <= ymax
    assert reset["center"] == [(xmin + xmax) / 2, (ymin + ymax) / 2]
    assert re.match(r"X" + str(x) + r"/Y" + str(y) + r"/", reset["bel"])
    placed = json.loads(z.read("physical/placed.json"))
    reset_cells = [(name, module["cells"][reset["cell"]])
                   for name, module in placed["modules"].items()
                   if reset["cell"] in module.get("cells", {})]
    assert len(reset_cells) == 1, "Expected exactly one placed core-reset FF across modules"
    reset_module, reset_cell = reset_cells[0]
    assert reset["placed_module"] == reset_module
    assert reset_cell["type"] == "TRELLIS_FF"
    assert reset_cell["attributes"]["NEXTPNR_BEL"] == reset["bel"]

    assert sha(z.read("physical/"+r["constraints"]["path"]))==r["constraints"]["sha256"]
    assert sha(z.read("physical/"+r["route_clocks"]["hook"]))==r["route_clocks"]["hook_sha256"]
    assert sha(z.read("physical/mkTop.config"))==r["config_sha256"]
    assert r["route_clocks"]["targets_mhz"]=={"$glbnet$clocks_pll_clk_100mhz":100.0,"$glbnet$CLK_clk_25mhz$TRELLIS_IO_IN":25.0}
    raw_timing = z.read("physical/nextpnr.json")
    routed_timing = json.loads(raw_timing)
    assert routed_timing["fmax"] == r["reported_clocks"]
    assert routed_timing["fmax"]
    for clock in routed_timing["fmax"].values():
        assert math.isfinite(clock["achieved"]) and math.isfinite(clock["constraint"])
        assert clock["constraint"] > 0 and clock["achieved"] >= clock["constraint"]
    for domain,minimum in [("core",100.0),("uart",25.0)]:
        c=r["clocks"][domain]
        assert c["pass"] and c["required_mhz"]==minimum
        assert abs(c["constraint_mhz"]-minimum)<.001 and c["achieved_mhz"]>=minimum
        raw_clock = routed_timing["fmax"][c["net"]]
        assert c["achieved_mhz"] == raw_clock["achieved"]
        assert c["constraint_mhz"] == raw_clock["constraint"]
    assert all(v["used"]<=v["available"] for v in r["packed_utilization"].values())
    for name,h in r["source_sha256"].items():
        prefix,relative=name.split("/",1)
        assert ".." not in Path(relative).parts
        path=Path(relative) if prefix=="sway" else Path("blueyosys-evidence")/relative
        assert prefix in ("sway","blueyosys")
        assert sha(path.read_bytes())==h,name
    sys.path.insert(0, str(Path("hw/reference").resolve()))
    from check_physical import source_hashes
    assert r["source_sha256"] == source_hashes(Path("hw").resolve(), Path("blueyosys-evidence").resolve())
    full_rtl=z.read("physical/build/mkTop.v")
    full_control=inspect_operand_control(full_rtl.decode())
    full_control["generated_rtl_sha256"]=sha(full_rtl)
    full_dsp_instances=len(re.findall(r"sway_mult18x18d\s+[\w$]+\s*\(",full_rtl.decode()))
    assert full_dsp_instances>0
    assert len(full_control["operand_ports"])==2*full_dsp_instances,"DSP operand coverage incomplete"
    bit=z.read("physical/mkTop.bit")
    assert len(bit)>0 and sha(bit)==z.read("physical/bitstream.sha256").decode().split()[0]
    timing=z.read("physical/timing_summary.json")
    timing_summary = json.loads(timing)
    assert timing_summary["detailed_report_sha256"] == sha(raw_timing)
    assert timing_summary["fmax"] == routed_timing["fmax"]
    assert timing_summary["critical_paths"] == routed_timing["critical_paths"]
    audit={"status":"pass","tested_commit":commit,"ci_run_id":run_id,"physical_job_id":job_id,
           "functional_commit":functional_commit,"functional_ci_run_id":functional_run_id,
           "continuation_provenance": {"original_commit": functional_commit, "original_run_id": functional_run_id,
               "original_job_id": 109208949816, "original_job_conclusion": "failure", "original_checker_error": "'mkTop'",
               "original_artifact_id": original_physical_artifact_id, "original_artifact_sha256": original_physical_artifact_digest,
               "original_report_sha256": sha(original_report_raw), "original_archive_digest_independently_verified": True,
               "original_synthesis_and_placement_commands_verified": True, "original_placement_evidence_unchanged": True,
               "original_physical_files_verified": len(original_files_sha256),
               "source_changes": source_changes, "hardware_tree_delta": changed_hw,
               "production_tree_delta": changed_production,"resume_script_sha256":sha(resume_script)},
           "audited_utc":datetime.now(timezone.utc).isoformat(),
           "artifact":{"id":artifact["id"],"bytes":len(archive),"sha256":sha(archive),"api_digest_verified":True},
           "physical_report_sha256":sha(report_raw),"source_files_verified":len(r["source_sha256"]),
           "sway_source_files_verified":sum(k.startswith("sway/") for k in r["source_sha256"]),
           "evidence_files_verified":len(r["evidence_sha256"]),
           "generated_rtl_files_verified":len(r["generated_rtl_sha256"]),
           "full_top_operand_control":dict(full_control,native_dsp_instances=full_dsp_instances),
           "clocks":r["clocks"],"packed_utilization":r["packed_utilization"],
           "reset_placement":dict(reset, hook_matches_frozen_source=True, console_report_verified=True, placed_bel_verified_independently=True),
           "config_sha256":r["config_sha256"],"timing_summary_sha256":sha(timing),
           "detailed_timing_sha256":sha(raw_timing),"raw_timing_cross_checked":True,
           "bitstream":{"bytes":len(bit),"sha256":sha(bit),"ecppack_step_conclusion":bit_step["conclusion"]},
           "scope":"Independent ZIP integrity, frozen checkout and dependency source hashes, strict physical gates, reset placement hook/source identity, selected and actual placed BEL, generated RTL/netlists/constraints/configuration and bitstream digest. No physical board test."}
run=api(f"actions/runs/{run_id}")
assert run["head_sha"]==commit and run["conclusion"]=="success"
jobs=api(f"actions/runs/{run_id}/jobs?per_page=100")["jobs"]
assert jobs and all(j["conclusion"]=="success" for j in jobs)
assert any(j["id"] == job_id for j in jobs)
functional_run=api(f"actions/runs/{functional_run_id}")
assert functional_run["head_sha"] == functional_commit
functional_jobs=api(f"actions/runs/{functional_run_id}/jobs?per_page=100")["jobs"]
required_functional_names = {"Functional divisor 1", "Functional divisor 2", "Functional divisor 4",
                             "Native multiplier boundary, stall and reset"}
required_functional_jobs = [j for j in functional_jobs if j["name"] in required_functional_names]
assert len(required_functional_jobs) == 4
assert {j["name"] for j in required_functional_jobs} == required_functional_names
assert all(j["conclusion"] == "success" for j in required_functional_jobs)
audit["jobs"] = [{"id": j["id"], "name": j["name"], "conclusion": j["conclusion"], "run_id": run_id}
                 for j in jobs]
audit["functional_jobs"] = [{"id": j["id"], "name": j["name"], "conclusion": j["conclusion"],
                              "run_id": functional_run_id} for j in required_functional_jobs]
audit["functional_run_conclusion"] = functional_run["conclusion"]
audit["functional_run_scope"] = "Four successful functional/native jobs; its original physical job is retained as a failed checker run, not a physical pass."
sys.path.insert(0,str(Path("hw/reference").resolve()))
from check_sim import check_log
from check_refactor import check_kernel
audit["functional_artifacts"]=[]
def check_sources(hashes):
    for name,h in hashes.items():
        assert ".." not in Path(name).parts and not Path(name).is_absolute()
        assert sha((Path("hw")/name).read_bytes())==h,name
for divisor in (1,2,4):
    a=next(a for a in functional_artifacts if a["name"]==f"baseline-functional-divisor-{divisor}-{functional_commit}")
    assert not a["expired"]
    filename=f"functional-{divisor}.zip"
    url=f'https://api.github.com/repos/{repo}/actions/artifacts/{a["id"]}/zip'
    assert subprocess.run(["curl","--fail","--silent","--show-error","--location","--retry","3","-H","Authorization: Bearer "+token,"-H","Accept: application/vnd.github+json",url,"--output",filename],check=False).returncode==0, "Artifact download failed"
    data=Path(filename).read_bytes()
    assert sha(data)==a["digest"].removeprefix("sha256:")
    item={"divisor":divisor,"artifact_id":a["id"],"bytes":len(data),"sha256":sha(data),"api_digest_verified":True}
    with zipfile.ZipFile(filename) as z:
        assert z.read("sway-commit.txt").decode().strip()==functional_commit
        raw=z.read("functional/validation.json")
        v=json.loads(raw)
        assert v["status"]=="pass" and len(v["configurations"])==2
        check_sources(v["source_sha256"])
        assert len(v["source_sha256"])==137
        item["report_sha256"]=sha(raw)
        item["source_files_verified"]=len(v["source_sha256"])
        item["configurations"]=[]
        configured,count=re.subn(r"typedef \d+ ParallelismDivisor;",f"typedef {divisor} ParallelismDivisor;",Path("hw/bsv/SwayTypes.bsv").read_text())
        assert count==1
        assert {c["mode"] for c in v["configurations"]}=={"stress","kernel"}
        with tempfile.TemporaryDirectory() as tmp:
            for c in v["configurations"]:
                assert c["status"]=="pass" and c["parallelism_divisor"]==divisor
                assert c["configured_types_sha256"]==sha(configured.encode())
                assert (c["frames_checked"],c["scalar_outputs_checked"],c["input_words"])==(14,798,4480)
                assert c["post_completion_drain_cycles"]>=2048
                log=z.read(f'functional/divisor{divisor}_{c["mode"]}/simulation.log')
                assert sha(log)==c["log_sha256"]
                logpath=Path(tmp)/"simulation.log"
                logpath.write_bytes(log)
                checker=check_log if c["mode"]=="stress" else check_kernel
                recomputed=checker(logpath,Path("hw/generated/test_input.hex"),Path("hw/generated/test_expected.hex"),"iverilog")
                assert all(c[k]==value for k,value in recomputed.items())
                item["configurations"].append({"mode":c["mode"],"status":"pass","frames":14,"outputs":798,"finish_cycle":c["finish_cycle"],"log_sha256":sha(log),"log_reparsed_against_frozen_fixtures":True})
        if divisor==4:
            raw=z.read("warm-reset/result.json")
            w=json.loads(raw)
            assert w["status"]=="pass" and w["source_unchanged"]
            check_sources(w["source_sha256"])
            assert len(w["source_sha256"])==138
            assert (w["reset_after_cycles"],w["reset_hold_cycles"])==(40000,5)
            assert w["before_reset"]["in_flight_work_confirmed"]
            post=w["post_reset"]
            assert post["status"]=="pass" and (post["frames_checked"],post["scalar_outputs_checked"])==(14,798)
            assert post["post_completion_drain_cycles"]>=2048
            assert all(c["returncode"]==0 for c in w["commands"])
            for c in w["commands"]:
                assert sha(z.read("warm-reset/"+Path(c["log"]).name))==c["log_sha256"]
            assert sha(z.read("warm-reset/warm_reset_main.v"))==w["wrapper_sha256"]
            post_log=z.read("warm-reset/post_reset.log")
            assert sha(post_log)==post["log_sha256"]
            with tempfile.TemporaryDirectory() as tmp:
                logpath=Path(tmp)/"post_reset.log"
                logpath.write_bytes(post_log)
                recomputed=check_kernel(logpath,Path("hw/generated/test_input.hex"),Path("hw/generated/test_expected.hex"),"iverilog")
                assert all(post[k]==value for k,value in recomputed.items())
            item["warm_reset"]={"status":"pass","report_sha256":sha(raw),"source_files_verified":138,"before_reset":w["before_reset"],"post_reset_frames":14,"post_reset_outputs":798,"post_reset_log_reparsed_against_frozen_fixtures":True,"finish_cycle":post["finish_cycle"]}
    audit["functional_artifacts"].append(item)
audit["scope"]+=" Also verifies all three functional ZIP digests from the preceding frozen RTL commit against the current checkout, 137 frozen source hashes per divisor, recomputes six output/cycle reports from raw simulation logs and fixtures, and checks 138 warm-reset source hashes and log integrity."

a=next(a for a in functional_artifacts if a["name"]=="baseline-native-"+functional_commit)
assert not a["expired"]
url=f'https://api.github.com/repos/{repo}/actions/artifacts/{a["id"]}/zip'
assert subprocess.run(["curl","--fail","--silent","--show-error","--location","--retry","3","-H","Authorization: Bearer "+token,"-H","Accept: application/vnd.github+json",url,"--output","native.zip"],check=False).returncode==0, "Artifact download failed"
data=Path("native.zip").read_bytes()
assert sha(data)==a["digest"].removeprefix("sha256:")
with zipfile.ZipFile("native.zip") as z:
    assert z.read("sway-commit.txt").decode().strip()==functional_commit
    raw=z.read("native/report.json")
    n=json.loads(raw)
    assert n["status"]=="pass"
    source_paths={"bsv/SwayMultiply.bsv":"hw/bsv/SwayMultiply.bsv","rtl/sway_mult18x18d.v":"hw/rtl/sway_mult18x18d.v","bsv/TbNativeMultiply.bsv":"hw/reference/native_multiply/TbNativeMultiply.bsv","main.v":"hw/reference/native_multiply/main.v"}
    assert set(n["source_sha256"])==set(source_paths)
    for name,h in n["source_sha256"].items():
        assert sha(Path(source_paths[name]).read_bytes())==h
        assert sha(z.read("native/snapshot/"+name))==h
    assert sha(z.read("native/snapshot/vectors.hex"))==n["vectors_sha256"]
    sys.path.insert(0,str(Path("hw/reference/native_multiply").resolve()))
    from check_native_multiply import check_log as check_multiply_log
    vectors=json.loads(z.read("native/vectors.json"))
    assert set(n["backends"])=={"bluesim","iverilog","iverilog_bsim"}
    for backend,result in n["backends"].items():
        recomputed=check_multiply_log(z.read("native/"+backend+"-simulation.log").decode(),vectors)
        assert json.loads(json.dumps(recomputed))==result
        assert result["products"]==256 and result["first_product_latency_cycles"]==4
    rtl=z.read("native/snapshot/iverilog/mkTbNativeMultiply.v")
    native_control=inspect_operand_control(rtl.decode(),expected_operands=2)
    isolation=json.loads(z.read("native/operand-control-isolation.json"))
    assert isolation["status"]=="pass" and isolation["generated_rtl_sha256"]==sha(rtl)
    audit["native_multiplier"]={"status":"pass","artifact_id":a["id"],"bytes":len(data),"sha256":sha(data),"api_digest_verified":True,"report_sha256":sha(raw),"source_files_verified":4,"backends":{name:{"products":r["products"],"first_product_latency_cycles":r["first_product_latency_cycles"],"final_cycle":r["final_cycle"]} for name,r in n["backends"].items()},"generated_rtl_sha256":sha(rtl),"operand_control":native_control,"log_results_independently_recomputed":True}

Path("artifact-audit.json").write_text(json.dumps(audit,indent=2)+"\n")
print("SWAY_ARTIFACT_AUDIT "+json.dumps(audit,sort_keys=True))

