#!/usr/bin/env python3
"""Download and verify the original MARS feature arrays without resplitting."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

import numpy as np


MARS_COMMIT = "dc902822f864ca0d5df90d559d53d3cd919d3c7c"
MARS_REPOSITORY = "https://github.com/SizheAn/MARS"
MARS_BASE_URL = f"https://raw.githubusercontent.com/SizheAn/MARS/{MARS_COMMIT}/feature"
SPLIT_FRAMES = {"train": 24066, "validate": 8033, "test": 7984}
FILE_SPECS = {
    "featuremap_train.npy": (61609088, "f00c9266059e6367c688d4bff0a7f5add633994c50fe0addbc501ed6f3ea0462"),
    "featuremap_validate.npy": (20564608, "13b701b45fce07b2105b3ec5fa348f70f50a64909257e8a92772915266d30807"),
    "featuremap_test.npy": (20439168, "331551d42039e97a0afc260029d605dec29f4a0d11ede65e4c1328c49cb5c145"),
    "labels_train.npy": (10974224, "14cc4f3e56dd2135f677fae68ba33d5973615136917cdc6b66d0a960da5324c7"),
    "labels_validate.npy": (3663176, "613f6b83d4d4ca9a785be6693e4eee75b52f607b3ea887203a3114c77cb8d9da"),
    "labels_test.npy": (3640832, "a1f29ebee5d5b4a63810997e9eda811af97bd222dcf4d4766777ccaea1b256e8"),
}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_file(path, url):
    temporary_path = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as source:
            with temporary_path.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def prepare_data(data_dir, download=False):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, (expected_size, expected_sha256) in FILE_SPECS.items():
        path = data_dir / name
        url = f"{MARS_BASE_URL}/{name}"
        if not path.exists():
            if not download:
                raise FileNotFoundError(f"Missing {path}; rerun with --download.")
            print(f"[STEP 1] Downloading {name}", flush=True)
            download_file(path, url)
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise ValueError(f"{path}: expected {expected_size} bytes, found {actual_size}.")
        actual_sha256 = sha256_file(path)
        if actual_sha256 != expected_sha256:
            raise ValueError(f"{path}: SHA-256 mismatch; refusing changed input data.")
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        split = name.removesuffix(".npy").split("_")[-1]
        sample_shape = (8, 8, 5) if name.startswith("featuremap_") else (57,)
        expected_shape = (SPLIT_FRAMES[split],) + sample_shape
        if array.shape != expected_shape or array.dtype != np.dtype("float64"):
            raise ValueError(f"{path}: unexpected shape or dtype: {array.shape}, {array.dtype}.")
        if not np.isfinite(array).all():
            raise ValueError(f"{path}: nonfinite values found.")
        records[name] = {
            "source_url": url,
            "bytes": actual_size,
            "sha256": actual_sha256,
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "all_finite": True,
        }
        print(f"[STEP 1] Verified {name}: shape={array.shape}, SHA-256={actual_sha256}", flush=True)

    manifest = {
        "dataset": "MARS: mmWave-based Assistive Rehabilitation System for Smart Healthcare",
        "repository": MARS_REPOSITORY,
        "commit": MARS_COMMIT,
        "license": "MIT",
        "source_readme": f"{MARS_REPOSITORY}/blob/{MARS_COMMIT}/README.md",
        "source_evaluation_code": f"{MARS_REPOSITORY}/blob/{MARS_COMMIT}/MARS_model.py",
        "split_protocol": "Use the six published feature arrays unchanged; no resplitting.",
        "split_frames": SPLIT_FRAMES,
        "total_frames": sum(SPLIT_FRAMES.values()),
        "split_discrepancy": (
            "Published arrays have approximately 60/20/20 train/validation/test proportions. "
            "eMamba arXiv:2508.10370v1 Section 5.1 describes 64/16/20. "
            "The exact eMamba split indices are not supplied; these are the original MARS splits."
        ),
        "input_layout": "NHWC: [frames, 8, 8, 5]",
        "input_channels": ["x", "y", "z", "Doppler velocity", "intensity"],
        "label_layout": "[frames, 57]; x[0:19], y[19:38], z[38:57]",
        "label_units": "metres; multiply coordinate errors by 100 to report centimetres",
        "preprocessing": "Use upstream feature values unchanged; float32 conversion at model input only.",
        "rmse_convention": (
            "Original MARS result table averages 57 per-coordinate RMSE values, "
            "each computed over frames. This differs from pooled RMSE over all frames and coordinates."
        ),
        "files": records,
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[STEP 2] Dataset manifest written to {manifest_path}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true", help="Download missing official arrays.")
    parser.add_argument("--manifest-out", type=Path, help="Optional second location for the manifest.")
    args = parser.parse_args()
    manifest = prepare_data(args.data_dir, download=args.download)
    if args.manifest_out:
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
