import hashlib,json,os,re,subprocess,urllib.request,zipfile
from pathlib import Path
repo="SeMinLim/sway"
commit="439af73b51b9e3f7c01b820caf3d25de575eca1d"
run_id=36472552335
job_id=109098154180
token=os.environ["GH_TOKEN"]
def api(path):
    request=urllib.request.Request("https://api.github.com/repos/"+repo+"/"+path,headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)
sha=lambda b:hashlib.sha256(b).hexdigest()
audit={"status":"fail","candidate_commit":commit,"ci_run_id":run_id,"native_job_id":job_id}
try:
    assert subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==commit
    job=api(f"actions/jobs/{job_id}")
    assert job["conclusion"]=="success"
    artifact=next(a for a in api(f"actions/runs/{run_id}/artifacts")["artifacts"] if a["name"]=="baseline-native-"+commit)
    assert not artifact["expired"]
    url=f'https://api.github.com/repos/{repo}/actions/artifacts/{artifact["id"]}/zip'
    subprocess.run(["curl","--fail","--silent","--show-error","--location","--retry","3","-H","Authorization: Bearer "+token,"-H","Accept: application/vnd.github+json",url,"--output","native-evidence.zip"],check=True)
    raw=Path("native-evidence.zip").read_bytes()
    assert sha(raw)==artifact["digest"].removeprefix("sha256:")
    audit["artifact"]={"id":artifact["id"],"bytes":len(raw),"sha256":sha(raw),"api_digest_verified":True}
    with zipfile.ZipFile("native-evidence.zip") as z:
        assert z.read("sway-commit.txt").decode().strip()==commit
        report_raw=z.read("native/report.json")
        report=json.loads(report_raw)
        assert report["status"]=="pass"
        assert set(report["backends"])=={"bluesim","iverilog","iverilog_bsim"}
        reference=report["backends"]["bluesim"]
        assert reference["products"]==256 and reference["first_product_latency_cycles"]==4
        assert all(result==reference for result in report["backends"].values())
        mapping={"main.v":"hw/reference/native_multiply/main.v","bsv/TbNativeMultiply.bsv":"hw/reference/native_multiply/TbNativeMultiply.bsv","bsv/SwayMultiply.bsv":"hw/bsv/SwayMultiply.bsv","rtl/sway_mult18x18d.v":"hw/rtl/sway_mult18x18d.v"}
        assert set(report["source_sha256"])==set(mapping)
        for key,path in mapping.items():
            assert sha(Path(path).read_bytes())==report["source_sha256"][key]
            assert sha(z.read("native/snapshot/"+key))==report["source_sha256"][key]
        rtl_raw=z.read("native/snapshot/iverilog/mkTbNativeMultiply.v")
        rtl=rtl_raw.decode()
        prior=json.loads(z.read("native/operand-control-isolation.json"))
        assert sha(rtl_raw)==prior["generated_rtl_sha256"]
        names=sorted(set(re.findall(r"(?<![\w$])[\w$]*[ab]Wire[$_]whas\b",rtl)))
        assignments=re.findall(r"assign\s+[\w$]*dsp[\w$]*dataa[xy]\s*=.*?;",rtl,re.S)
        ports=re.findall(r"\.dataa[xy]\s*\([^)]*\)",rtl)
        all_assignments=dict(re.findall(r"\bassign\s+([\w$]+)\s*=\s*(.*?);",rtl,re.S))
        operand_signals=re.findall(r"\.dataa[xy]\s*\(\s*([\w$]+)\s*\)",rtl)
        cones={}
        for signal in operand_signals:
            pending=[signal]
            seen=set()
            while pending:
                current=pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                if current in all_assignments:
                    pending.extend(re.findall(r"[A-Za-z_][\w$]*",all_assignments[current]))
            cones[signal]={"signals":sorted(seen),"assignments":{s:all_assignments[s] for s in sorted(seen) if s in all_assignments},"write_presence_dependencies":sorted(set(names)&seen)}
        audit["operand_dependency_cones"]=cones
        audit["retained_write_presence_uses"]=[line.strip() for line in rtl.splitlines() if any(name in line for name in names)]
        audit.update({"native_report_sha256":sha(report_raw),"source_files_verified":len(mapping),"generated_rtl_sha256":sha(rtl_raw),"operand_whas_signals":names,"operand_assignments":assignments,"operand_ports":ports,"backends_equal":True,"products_per_backend":reference["products"],"first_product_latency_cycles":reference["first_product_latency_cycles"],"final_cycle":reference["final_cycle"],"scope":"Downloaded native ZIP digest, candidate source and snapshot hashes verified; actual generated RTL operand data cones inspected including dollar identifiers. Presence signals outside those cones are retained and reported. Functional report equality checked; no physical timing claim."})
        Path("native-mkTbNativeMultiply.v").write_bytes(rtl_raw)
        assert len(ports)==2 and len(assignments)==2, "Expected two operand ports and two assignments"
        assert len(cones)==2 and all(not c["write_presence_dependencies"] for c in cones.values()), "Operand data cone depends on write-presence control"
        audit["status"]="pass"
except Exception as error:
    audit["error"]=str(error)
    raise
finally:
    Path("native-artifact-audit.json").write_text(json.dumps(audit,indent=2)+"\n")
    print("SWAY_NATIVE_ARTIFACT_AUDIT "+json.dumps(audit,sort_keys=True))
