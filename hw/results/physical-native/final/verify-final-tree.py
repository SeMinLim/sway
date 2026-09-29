#!/usr/bin/env python3
"""Check that integration only changes evidence, README files, and the CI trigger."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', type=Path, required=True)
parser.add_argument('--candidate', required=True)
parser.add_argument('--workflow-reference', required=True)
parser.add_argument('--tree-json', type=Path, required=True)
parser.add_argument('--manual-workflow', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
raw = subprocess.check_output(['git', '-C', str(args.repo), 'ls-tree', '-r', args.candidate], text=True)
candidate = {}
for line in raw.splitlines():
    info, path = line.split('\t', 1)
    mode, kind, sha = info.split()
    candidate[path] = {'mode': mode, 'type': kind, 'sha': sha}
tree = json.loads(args.tree_json.read_text())
assert not tree.get('truncated'), 'Incomplete final tree'
final = {entry['path']: {k: entry[k] for k in ('mode', 'type', 'sha')}
         for entry in tree['tree'] if entry['type'] != 'tree'}
allowed_files = {'README.md', 'hw/README.md', '.github/workflows/baseline-pnr-validation.yml'}
def permitted(path):
    return path in allowed_files or path.startswith('hw/results/')
paths = sorted(set(candidate) | set(final))
changed = [p for p in paths if candidate.get(p) != final.get(p)]
assert all(permitted(p) for p in changed), changed
assert not any(p not in final for p in candidate), 'Integration deleted a candidate file'
workflow = subprocess.check_output(['git', '-C', str(args.repo), 'show',
                                  args.workflow_reference + ':.github/workflows/baseline-pnr-validation.yml'])
trigger = b'on:\n  push:\n    branches: [codex/baseline-pnr-validation]'
assert workflow.count(trigger) == 1
expected = workflow.replace(trigger, b'on:\n  workflow_dispatch:')
manual = args.manual_workflow.read_bytes()
assert manual == expected, 'CI changes beyond the manual trigger'
blob = hashlib.sha1(b'blob ' + str(len(manual)).encode() + b'\0' + manual).hexdigest()
assert final['.github/workflows/baseline-pnr-validation.yml']['sha'] == blob
identity = [{'path': p, **candidate[p]} for p in paths if not permitted(p)]
assert identity and all(candidate[p['path']] == final[p['path']] for p in identity)
report = {'status': 'pass', 'tested_commit': args.candidate,
          'scope': 'All committed paths outside evidence, the two README files, and the CI workflow retain identical mode/type/blob. The final workflow restores the full functional/native/physical jobs from the recorded workflow reference, changing only its validation-branch push trigger to manual dispatch.',
          'identical_paths': identity, 'identical_path_count': len(identity),
          'allowed_changed_paths': changed, 'files_deleted': 0,
          'manual_workflow_blob': blob, 'workflow_jobs_unchanged_from_reference': True,
          'workflow_reference': args.workflow_reference}
args.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'status': 'pass', 'identical_paths': len(identity), 'allowed_changes': len(changed)}))
