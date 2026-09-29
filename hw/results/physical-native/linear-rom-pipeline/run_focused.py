from pathlib import Path
import json, shutil, subprocess, time, hashlib, resource, re
root=Path(__file__).resolve().parent
hw=root.parent.parent/'sway/hw'
for name in ['bsv/SwayLinear.bsv','rtl/BRAM1Load.v']:
 shutil.copy2(hw/name,root/name)
commands=json.loads((root/'commands.json').read_text())
def limit():resource.setrlimit(resource.RLIMIT_AS,(6*1024**3,6*1024**3))
records=[]
for item in commands:
 phase=item['phase']; start=time.monotonic()
 with (root/(phase+'.log')).open('w') as log:
  result=subprocess.run(item['command'],cwd=root,stdout=log,stderr=subprocess.STDOUT,preexec_fn=limit)
 records.append(dict(phase=phase,command=item['command'],exit_code=result.returncode,elapsed_seconds=time.monotonic()-start))
 if result.returncode: raise RuntimeError(phase+' failed')
compile_log=(root/'compile.log').read_text(); sim=(root/'simulation.log').read_text(); sched=(root/'build/mkTbLinearSelect.sched').read_text()
assert 'Warning:' not in compile_log
assert 'LINEAR_FAIL' not in sim and 'LINEAR_PASS tokens=4 values=80' in sim
assert 'LINEAR_CONTROL issued=25600 max_consecutive_issue=320 restart_requests=76 restarts=76' in sim
assert all(v=='(none)' for v in re.findall(r'Blocking rules: (.*)',sched))
(root/'focused-report.json').write_text(json.dumps({'status':'PASS','commands':records,'source_sha256':{n:hashlib.sha256((hw/n).read_bytes()).hexdigest() for n in ['bsv/SwayLinear.bsv','rtl/BRAM1Load.v']},'simulation':sim,'warnings':0,'blocked_rules':0},indent=2)+'\n')
print(sim,end='')
