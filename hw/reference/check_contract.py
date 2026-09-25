#!/usr/bin/env python3
"""Compare the frozen integer baseline with the current MARS PTQ software.

This is a verification command, not training or calibration. Both graphs see
the same INT8 input. Replacing only software range normalization isolates the
baseline's existing exact-rational arithmetic contract.
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import torch

from integer_model import GENERATED, MODEL, ROOT, IntegerModel, file_hash
from generate import make_tables, piecewise, verify_model_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Original MARS test arrays")
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    torch.set_num_threads(2)
    source = ROOT.parent / "sw"
    sys.path.insert(0, str(source))
    from evaluate_ptq import loadCheckpoint
    from model import forwardModel, piecewise as software_piecewise
    from quantize import QuantizationObserver

    provenance = verify_model_bundle(MODEL / "export")
    checkpoint, software, profile, _ = loadCheckpoint(MODEL / "checkpoint.pt")
    reference = IntegerModel()
    tables, table_metadata = make_tables(MODEL / "export")
    if not np.array_equal(tables, reference.tables):
        raise AssertionError("Checked-in nonlinear table differs from generation")
    for index, definition in enumerate(table_metadata["tables"]):
        inputs = torch.arange(-128, 128, dtype=torch.float32) * 2.0 ** definition["inputExponent"]
        knots = profile["pwlKnots"][definition["operation"] + "_knots"]
        expected = software_piecewise(inputs, knots, definition["operation"])
        actual = piecewise(inputs, knots, definition["operation"])
        if not torch.equal(expected, actual):
            raise AssertionError("Frozen PWL definition differs from source software")
        expected_int = torch.round(expected / 2.0 ** definition["outputExponent"]).clamp(-128, 127)
        if not np.array_equal(expected_int.numpy(), tables[index]):
            raise AssertionError("Nonlinear table differs from source software")

    class RationalObserver(QuantizationObserver):
        def rangeNorm(self, name, value, weight, bias, epsilon):
            prefix = name.rsplit(".", 1)[0]
            input_name = "embedding" if prefix == "blocks.0" else "blocks.0.residual"
            integer = torch.round(value / 2.0 ** self.exponent(input_name)).to(torch.int64).numpy()
            normalized = reference.normalization(integer, prefix)
            return torch.from_numpy(normalized).to(value.dtype) * 2.0 ** self.exponent(name)

    metadata = json.loads((MODEL / "metrics.json").read_text())
    for name, digest in metadata["dataset_test_sha256"].items():
        if file_hash(args.data / name) != digest:
            raise ValueError("Test data hash mismatch: " + name)
    features = np.load(args.data / "featuremap_test.npy", allow_pickle=False).astype(np.float32)
    saved = np.load(MODEL / "predictions_int8_ptq.npy", allow_pickle=False)
    generated = np.load(GENERATED / "predictions_integer.npy", allow_pickle=False)
    output_scale = 2.0 ** reference.exponent("output")
    divisors = np.asarray(reference.config.get("input_channel_divisors", [1.0] * 5), dtype=np.float32)
    ordinary_outputs = []
    integer_outputs = []
    signed_delta = [0, 0]
    negative_conv = [0, 0]
    with torch.no_grad():
        for start in range(0, len(features), args.batch_size):
            raw = features[start:start + args.batch_size]
            integer_input = reference.quantize_inputs(raw)
            canonical_input = integer_input.astype(np.float32) * 2.0 ** reference.exponent("input") * divisors
            batch = torch.from_numpy(canonical_input)
            ordinary = forwardModel(software, batch, observer=QuantizationObserver(profile), usePWL=True).numpy()
            isolated = forwardModel(software, batch, observer=RationalObserver(profile), usePWL=True).numpy()
            trace = {}
            integer = reference.forward_integer(integer_input, trace)
            if not np.array_equal(integer.astype(np.float32) * output_scale, isolated):
                count = np.count_nonzero(integer.astype(np.float32) * output_scale != isolated)
                raise AssertionError("Non-normalization contract mismatch at frame %d: %d outputs" % (start, count))
            for block in range(2):
                prefix = "blocks.%d." % block
                if not np.array_equal(trace[prefix + "conv"], trace[prefix + "x"]):
                    raise AssertionError("Convolution output was changed before the SSM")
                if np.any(trace[prefix + "deltaInput"] < 0):
                    raise AssertionError("First delta projection did not pass through ReLU")
                if not np.array_equal(trace[prefix + "deltaProjection"], trace[prefix + "delta"]):
                    raise AssertionError("Signed second delta projection was changed")
                signed_delta[block] += int(np.count_nonzero(trace[prefix + "delta"] < 0))
                negative_conv[block] += int(np.count_nonzero(trace[prefix + "x"] < 0))
            ordinary_outputs.append(ordinary)
            integer_outputs.append(integer)
    ordinary = np.concatenate(ordinary_outputs)
    integer = np.concatenate(integer_outputs)
    if not np.array_equal(ordinary, saved):
        raise AssertionError("Current source QDQ output differs from frozen predictions")
    if not np.array_equal(integer, generated):
        raise AssertionError("Current integer reference differs from generated predictions")
    difference = integer - np.rint(ordinary.astype(np.float64) / output_scale).astype(np.int64)
    report = {
        "schemaVersion": 2,
        "checkpointSHA256": file_hash(MODEL / "checkpoint.pt"),
        "sourceCommit": provenance["sourceCommit"],
        "sourceSHA256": {"sw/" + name: file_hash(source / name) for name in ["model.py", "quantize.py", "evaluate_ptq.py"]},
        "referenceSHA256": {"reference/" + name: file_hash(Path(__file__).parent / name)
                            for name in ["generate.py", "integer_model.py", "check_contract.py"]},
        "nonlinearTableSHA256": file_hash(GENERATED / "nonlinear_tables.npy"),
        "nonlinearVerification": {"entries": int(tables.size), "float32PWLValuesMatch": True,
                                  "int8TableEntriesMatch": True, "tableOrder": "block0 gate, block0 exp, block1 gate, block1 exp"},
        "normalizationIsolation": {
            "frames": len(features), "totalOutputElements": int(integer.size),
            "integerVersusSwWithOnlyNormalizationReplacedMatches": int(integer.size),
            "currentSwVersusSavedQDQMatches": int(ordinary.size),
            "input": "Identical host-quantized INT8 values, reconstructed at the software input boundary",
            "result": "Every integer output matches the PTQ software graph after replacing only float32 range normalization with exact rational normalization"},
        "integerVersusOriginalQDQ": {
            "matchedOutputElements": int(np.count_nonzero(difference == 0)),
            "totalOutputElements": int(difference.size),
            "matchedFrames": int(np.count_nonzero(np.all(difference == 0, axis=1))),
            "maxOutputDifferenceLSB": int(np.abs(difference).max()),
            "meanAbsoluteOutputDifferenceLSB": float(np.abs(difference).mean()),
            "differenceRMSE_cm": float(np.sqrt(np.mean((difference * output_scale) ** 2)) * 100.0),
            "comparison": "Exact-rational hardware normalization differs from original float32 QDQ; no blanket bit-exact SW claim"},
        "activationVerification": {"convToXUnchanged": True, "deltaInputNonnegative": True,
                                   "deltaProjectionToDeltaUnchanged": True,
                                   "negativeDeltaElementsByBlock": signed_delta,
                                   "negativeConvOutputElementsByBlock": negative_conv},
        "testUsedForTuning": False,
        "checkpointOrCalibrationChanged": False,
    }
    (GENERATED / "software_contract_verification.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
