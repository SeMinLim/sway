#!/usr/bin/env python3
"""Validate actual simulator output only. Does not synthesize timing results."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def check_log(log_path: Path, metadata_path: Path, backend: str = "iverilog") -> dict:
    text = log_path.read_text()
    if 'FAIL,' in text or 'PASS,projection_partial_values,112,tokens,7' not in text:
        raise RuntimeError('RTL did not report a complete successful numerical test')
    meta = json.loads(metadata_path.read_text())
    quant = {}
    events = []
    outputs = []
    for line in text.splitlines():
        fields = line.split(',')
        if fields[0] == 'Q' and len(fields) == 6:
            token = int(fields[1])
            if token in quant:
                raise AssertionError('Duplicate quantized token')
            quant[token] = tuple(int(v,16) for v in fields[2:5]) + (int(fields[5]),)
        elif fields[0] == 'EV' and len(fields) == 5:
            events.append((int(fields[1]), fields[2], int(fields[3]), int(fields[4])))
        elif fields[0] == 'OUT' and len(fields) == 5:
            outputs.append(tuple(int(v) for v in fields[1:]))
    if set(quant) != set(range(7)) or len(outputs) != 28:
        raise AssertionError('Missing or extra token/output')
    for t in range(7):
        q = meta['quant'][t]
        expected = (q['scale'],q['inverse'],q['packed'],int(q['zero']))
        if quant[t] != expected:
            raise AssertionError(f'Quantization mismatch token={t}: {quant[t]} != {expected}')
    live = {}
    peak_live = 0
    # A retire and a new read may share an edge only if FIFO implementation permits it.
    for cyc,kind,t,slot in sorted(events, key=lambda x:(x[0],0 if x[1]=='retire' else 1)):
        if kind == 'read':
            if slot in live:
                raise AssertionError('Input slot reused before explicit retirement')
            live[slot] = t
            peak_live = max(peak_live,len(live))
        elif kind == 'retire':
            if live.get(slot) != t:
                raise AssertionError('Incorrect retirement tag or duplicate retirement')
            del live[slot]
    if live or peak_live > 2:
        raise AssertionError('Slot accounting failed')
    per_token = []
    for t in range(7):
        ev = [(c,k) for c,k,token,slot in events if token==t]
        def one(name: str) -> int:
            values=[c for c,k in ev if k==name]
            if len(values)!=1:
                raise AssertionError(f'Expected one {name} for token {t}')
            return values[0]
        issues=[c for c,k in ev if k.startswith('apot_issue_')]
        if len(issues)!=4:
            raise AssertionError('4->16 projection must issue exactly four 4x4 tiles')
        per_token.append(dict(token=t,read=one('read'),quant_ready=one('quant_ready'),
            prepare_cycles=one('quant_ready')-one('read'),
            reciprocal_observed_cycles=one('reciprocal_done')-one('reciprocal_put'),
            tile_issue_count=len(issues),tile_issue_span=max(issues)-min(issues)+1))
    return dict(evidence=('generated-Verilog simulation (Icarus)' if backend == 'iverilog' else 'BSV simulation (Bluesim)'), values_checked=112,
                quant_tokens_checked=7, peak_live_slots=peak_live, tokens=per_token,
                system_scope='diagnostic slice, NOT full projection/encoder')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('log',type=Path)
    parser.add_argument('metadata',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--backend', choices=['bluesim', 'iverilog'], required=True)
    parser.add_argument('--stderr', type=Path)
    a=parser.parse_args()
    # Never leave an earlier PASS result behind after a failed run.
    a.output.unlink(missing_ok=True)
    if a.stderr:
        error_text = a.stderr.read_text(errors='replace')
        if 'FAIL,' in error_text or 'Error:' in error_text or 'FATAL:' in error_text:
            raise RuntimeError('Simulator error; inspect ' + str(a.stderr))
    result=check_log(a.log,a.metadata,a.backend)
    result['fixture_sha256'] = __import__('hashlib').sha256(a.metadata.read_bytes()).hexdigest()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
