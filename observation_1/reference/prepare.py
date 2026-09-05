#!/usr/bin/env python3
"""Export a matched per-tensor fixed-point Mamba block and self-checking HDL vectors.

Default source: the pinned MambaLite-Micro KWS header and example MFCC input.
--fixture is an explicitly synthetic implementation test, never KWS accuracy.
No training, full-application timing, or paper bottleneck claim is made here.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import urllib.request
import numpy as np

REV = "44d51fce0f17ddadb6111c5e5554d1f7f6c67aff"
BASE = f"https://raw.githubusercontent.com/Whiten-Rock/MambaLite-Micro/{REV}/examples/mambakws-any-10/include/"
SOURCE_SHA = {"mamba_weights.h": "67eff33bf62e5f42adbf1ac506b425de83e67880", "sample_input.h": "d58558694184960cd3f9acf394a977eb1a0717d8"}
D, H, N, R, K, T = 64, 128, 16, 4, 4, 100
WEIGHT_FRACTION = 13
INPUT_FRACTION = 8
OUTPUT_FRACTION = 7


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def download(cache: Path, name: str) -> str:
    path = cache / name
    if not path.exists():
        with urllib.request.urlopen(BASE + name, timeout=60) as response:
            raw = response.read()
        if git_blob(raw) != SOURCE_SHA[name]:
            raise ValueError(f"Upstream blob hash mismatch: {name}")
        path.write_bytes(raw)
    raw = path.read_bytes()
    if git_blob(raw) != SOURCE_SHA[name]:
        raise ValueError(f"Cached source is not pinned version: {path}")
    return raw.decode("utf-8")


def arrays(text: str) -> dict[str, np.ndarray]:
    result = {}
    pattern = r"(?:const\s+)?float\s+(\w+)\s*\[[^;=]*?\]\s*=\s*\{(.*?)\};"
    for name, body in re.findall(pattern, text, re.S):
        body = re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S)
        nums = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", body)
        result[name] = np.asarray([float(v) for v in nums], dtype=np.float64)
    return result


def sat(x):
    return np.clip(x, -32768, 32767).astype(np.int64)


def quant(x, fraction):
    return sat(np.rint(np.asarray(x) * (1 << fraction)))


def mul(a, b, shift):
    return sat((np.asarray(a, dtype=np.int64) * np.asarray(b, dtype=np.int64)) >> shift)


def add(a, b):
    return sat(np.asarray(a, dtype=np.int64) + np.asarray(b, dtype=np.int64))


def mat(x, w, shift, bias=None):
    s = w @ x
    if bias is not None:
        s = s + (bias << shift)
    return sat(s >> shift)


def lookup(x, table):
    return table[(np.asarray(x, dtype=np.int64) + 32768) >> 4]


def tables():
    raw = np.arange(4096, dtype=np.float64) * 16 - 32768
    x = raw / 1024.0
    gate_x = raw / 256.0
    return {
        "silu": quant(x / (1.0 + np.exp(-x)), 10),
        "gate_silu": quant(gate_x / (1.0 + np.exp(-gate_x)), 8),
        "softplus": quant(np.logaddexp(0.0, x), 15),
        "exp": quant(np.exp(-np.arange(4096)/256.0), 15),
    }


def decay_lookup(x, table):
    index = np.clip((-np.asarray(x, dtype=np.int64) + 2) >> 2, 0, 4095)
    return table[index]


def packed_hex(path: Path, rows):
    with path.open("w") as out:
        for row in rows:
            words = np.asarray(row, dtype=np.int64).reshape(-1)
            out.write("".join(f"{int(v) & 65535:04x}" for v in reversed(words)) + "\n")


def export_linear(path: Path, w, bias=None):
    rows, cols = w.shape
    if rows % 4:
        raise ValueError("This fixed baseline requires output rows divisible by four")
    bias = np.zeros(rows, dtype=np.int64) if bias is None else bias
    words = []
    for base in range(0, rows, 4):
        for col in range(cols):
            words.append(w[base:base+4, col])
        words.append(bias[base:base+4])
    packed_hex(path, words)


def fixed_forward(x, w, lut):
    state = np.zeros((H, N), dtype=np.int64)
    hist = np.zeros((H, K-1), dtype=np.int64)
    outputs = []
    saturation_count = 0
    for token in x:
        projected = mat(token, w["in"], 13)
        u, z = projected[:H], projected[H:]
        window = np.concatenate([hist, u[:, None]], axis=1)
        conv = sat(((window * w["conv"]).sum(axis=1) + (w["cb"] << 11)) >> 11)
        hist = window[:, 1:].copy()
        u = lookup(conv, lut["silu"])
        gate = lookup(z, lut["gate_silu"])
        p = mat(u, w["xp"], 13)
        delta = lookup(mat(p[:R], w["dt"], 13, w["db"]), lut["softplus"])
        b, c = p[R:R+N], p[R+N:]
        decay = decay_lookup(mul(delta[:, None], w["a"], 15), lut["exp"])
        injection = mul(mul(delta[:, None], b[None, :], 13), u[:, None], 10)
        state = add(mul(decay, state, 15), injection)
        skip = mul(w["skip"], u, 13)
        y = sat(((state * c[None, :]).sum(axis=1) + (skip << 12)) >> 12)
        y = mul(y, gate, 13)
        out = mat(y, w["out"], 11)
        outputs.append(out)
        saturation_count += int(np.count_nonzero((state == -32768) | (state == 32767)))
    return np.asarray(outputs), saturation_count


def float_forward(x, w):
    state = np.zeros((H, N))
    hist = np.zeros((H, K-1))
    result = []
    for token in x:
        p = w["in"] @ token
        window = np.concatenate([hist, p[:H, None]], axis=1)
        conv = (window * w["conv"]).sum(1) + w["cb"]
        hist = window[:, 1:].copy()
        u = conv / (1 + np.exp(-np.clip(conv, -80, 80)))
        gate = p[H:] / (1 + np.exp(-np.clip(p[H:], -80, 80)))
        pars = w["xp"] @ u
        dt = np.logaddexp(0, w["dt"] @ pars[:R] + w["db"])
        state = np.exp(dt[:, None] * w["a"]) * state + dt[:, None] * pars[R:R+N] * u[:, None]
        y = ((state * pars[R+N:]).sum(1) + w["skip"] * u) * gate
        result.append(w["out"] @ y)
    return np.asarray(result)


def get_fixture():
    rng = np.random.default_rng(17029)
    sizes = {"in": (2*H, D), "conv": (H,K), "cb": (H,), "xp": (R+2*N,H),
             "dt": (H,R), "db": (H,), "a": (H,N), "skip": (H,), "out": (D,H)}
    w = {name: rng.normal(0, 0.15, size) for name, size in sizes.items()}
    w["a"] = -np.tile(np.arange(1,N+1), (H,1)).astype(float)
    w["db"] = rng.uniform(-3.0, -1.0, H)
    w["skip"] = np.ones(H)
    return rng.normal(0, 0.8, (T,D)), w


def get_upstream(cache):
    header = download(cache, "mamba_weights.h")
    expected = {"d_model": D, "d_state": N, "d_conv": K, "expand": 2, "dt_rank": R}
    for key, val in expected.items():
        m = re.search(r"#define\s+mamba_" + key + r"\s+(\d+)", header)
        if not m or int(m[1]) != val:
            raise ValueError(f"Unexpected model dimension: {key}")
    a = arrays(header)
    names = {"in": ("mamba_in_proj_weight", (2*H,D)), "conv": ("mamba_conv1d_weight", (H,K)),
             "cb": ("mamba_conv1d_bias", (H,)), "xp": ("mamba_x_proj_weight", (R+2*N,H)),
             "dt": ("mamba_dt_proj_weight", (H,R)), "db": ("mamba_dt_proj_bias", (H,)),
             "a": ("mamba_A_log", (H,N)), "skip": ("mamba_D", (H,)),
             "out": ("mamba_out_proj_weight", (D,H))}
    w = {key: a[name].reshape(shape) for key, (name, shape) in names.items()}
    w["a"] = -np.exp(w["a"])
    inp = arrays(download(cache, "sample_input.h"))
    candidates = [v for v in inp.values() if v.size == T*40]
    if len(candidates) != 1:
        raise ValueError(f"Expected one 100x40 sample array; got {[(k,v.shape) for k,v in inp.items()]}")
    features = candidates[0].reshape(T,40)
    # This application-specific projection is outside the accelerator boundary.
    x = features @ a["linear_in_weight"].reshape(D,40).T + a["linear_in_bias"]
    return x, w


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "upstream"
    cache.mkdir(exist_ok=True)
    x_float, weights_float = get_fixture() if args.fixture else get_upstream(cache)
    weights = {key: quant(v, 10 if key in ("cb", "db", "a") else 13)
               for key, v in weights_float.items()}
    x = quant(x_float, INPUT_FRACTION)
    lut = tables()
    y, saturated = fixed_forward(x, weights, lut)
    if not np.any(y):
        raise ValueError("All-zero expected outputs: cannot use as a meaningful equivalence test")
    yf = float_forward(x_float, weights_float)
    export_linear(out / "in_proj.hex", weights["in"])
    export_linear(out / "x_proj.hex", weights["xp"])
    export_linear(out / "dt_proj.hex", weights["dt"], weights["db"])
    export_linear(out / "out_proj.hex", weights["out"])
    packed_hex(out / "conv.hex", np.column_stack([weights["conv"],weights["cb"]]))
    packed_hex(out / "scan.hex", np.column_stack([weights["a"].reshape(-1),np.repeat(weights["skip"],N)]))
    for name, table in lut.items():
        packed_hex(out / (name + ".hex"), table[:,None])
    packed_hex(out / "input.hex", x)
    packed_hex(out / "expected.hex", y)
    error = y/(1 << OUTPUT_FRACTION) - yf
    manifest = {"source": "synthetic fixture" if args.fixture else "MambaLite-Micro public KWS sample",
                "revision": REV, "source_blobs": {} if args.fixture else SOURCE_SHA,
                "dimensions": {"d_model":D,"d_inner":H,"d_state":N,"dt_rank":R,"d_conv":K,"sequence":T},
                "numerics": "signed16 per-tensor fraction; 48-bit linear accumulation; floor shift then saturation; decay LUT nearest1/256",
                "state_saturation_count": saturated,
                "float_comparison": {"max_abs":float(np.max(np.abs(error))),"rmse":float(np.sqrt(np.mean(error**2)))},
                "fractions": {"input":8,"xz":8,"u":10,"gate":8,"parameters":10,"delta":15,"state":12,"gated":5,"output":7,"linear_weights":13,"A":10},
                "accuracy_evaluation": "NOT PERFORMED: one example and numeric equivalence are not task accuracy",
                "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("*.hex"))}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
