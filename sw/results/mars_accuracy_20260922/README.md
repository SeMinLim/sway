# MARS accuracy retraining, 2026-09-22

This is the historical FP32/PWL/QAT experiment. Its QAT checkpoints pin the inference source hashes from [commit 0d2ceea](https://github.com/SeMinLim/sway/commit/0d2ceea72062772ceb0e320bba58b8ecb33889ab); run the archived QAT commands with that revision. The current [PTQ checkpoint](../mars_ptq_20260923/README.md) has a separate frozen-profile evaluator.

| Numerical model | Previous RMSE (cm) | New RMSE (cm) | Decrease |
|---|---:|---:|---:|
| FP32, exact SiLU/exp | 8.9582 | 8.4884 | 5.25% |
| FP32, PWL SiLU/exp | 9.6793 | 8.5476 | 11.69% |
| INT8, PTQ to QAT | 10.4598 | 8.8135 | 15.74% |

All local scores use the same 7,984 official test frames and the same 57-coordinate RMSE convention. Test membership and labels were not changed. Models, learning rates, and clipping profiles were selected on validation only.

eMamba Table 4 reports FP32 7.85 cm and INT8 8.83 cm using post-training quantization (PTQ). Our 8.8135 cm INT8 result uses quantization-aware training (QAT), with weight updates under simulated quantization. This is a different training method: the numerical proximity does not reproduce eMamba's PTQ accuracy or establish an equivalent-method comparison. These are publication reference values, not a matched reproduction. The published MARS arrays use 24,066/8,033/7,984 train/validation/test frames; eMamba states 64/16/20 percentages without exact indices. The regression head, some block details, training recipe, and PWL coefficients are unpublished. The paper applies PWL at inference but does not explicitly bind the Table 4 FP32 result to exact or PWL nonlinearities, so both local FP32 paths are reported. Source: https://arxiv.org/html/2508.10370v1

## Changes and runs

The 15,717-parameter architecture and PWL knots remain the same. FP32 MSE training resumed beyond the old 150-epoch limit. A single additional FP32 candidate minimizes minibatch mean coordinate RMSE with exact nonlinearities. PWL adaptation and INT8 QAT use separate frozen source checkpoints, documented in protocol.json. They are parallel training branches; the final INT8 model is not a quantization of the final RMSE-refined FP32 weights.

QAT adds clipped straight-through gradients while retaining the integer-reference forward values, including INT24 current state and arithmetic truncation to INT17 retained state. The train-calibrated power-of-two scales remain frozen. QAT validation uses the inference reference, and evaluation rejects changed model/quantization source hashes. Epoch-0 fallback, optimizer/scheduler/RNG resume, and source/data/profile digests prevent silent model replacement.

| Run | Completed epochs | Selected epoch | Validation RMSE (cm) | Stop reason |
|---|---:|---:|---:|---|
| fp32 | 400 | 378 | 8.5364 | configured_epoch_limit |
| fp32_rmse | 60 | 57 | 8.4218 | configured_epoch_limit |
| fp32_pwl | 40 | 32 | 8.4914 | configured_epoch_limit |
| qat | 40 | 35 | 8.7637 | configured_epoch_limit |

With the same QAT source model and frozen profile, INT8 test RMSE changed from 10.4387 cm before QAT to 8.8135 cm after QAT.

Direct PTQ of the selected exact-FP32 model gave 11.1471 cm, worse than the original 10.4598 cm PTQ result. Thus the final INT8 improvement comes from the separate QAT branch, not from directly quantizing the final exact-FP32 checkpoint. Likewise, use the PWL-adapted checkpoint with PWL functions: switching that checkpoint back to exact functions gives 9.8772 cm.

## Artifacts and verification

- `selection.json` freezes validation choices before test evaluation; `summary.json` contains full metrics and reference gaps.
- Each run retains `best.pt`, `last.pt`, `history.csv`, `run.json`, and its training log. Frozen branch sources are included.
- `final_fp32`, `final_pwl`, and `final_int8` contain final metrics and predictions. INT8 exports include FP32 arrays, INT8 binary/hex arrays, scales, and hashes.
- Numerical checks and focused QAT/resume/evaluator/refinement tests passed. Final prediction metrics were recomputed from saved outputs. Exported FP32 and INT8 parameter values, binary/hex agreement, sizes, and hashes were checked against the selected checkpoints.

These are software numerical-reference results. The surrounding normalization/nonlinearity graph uses QDQ simulation. Existing RTL ROMs and physical measurements retain the original weights and scales; no new RTL or board accuracy/timing result is claimed.

## Evaluate the saved models

From the repository root:

```sh
python -m pip install -r sw/requirements.txt
python sw/prepare_data.py --data-dir ../data/mars --download
python sw/evaluate.py --data ../data/mars --checkpoint sw/results/mars_accuracy_20260922/fp32_rmse/best.pt --output sw/results/recheck_fp32 --quantize
python sw/evaluate.py --data ../data/mars --checkpoint sw/results/mars_accuracy_20260922/fp32_pwl/best.pt --output sw/results/recheck_pwl
python sw/evaluate.py --data ../data/mars --checkpoint sw/results/mars_accuracy_20260922/qat/best.pt --source-checkpoint sw/results/mars_accuracy_20260922/fp32_at_qat_start.pt --output sw/results/recheck_qat
```

Exact training arguments are retained in each run.json. Resume with the same settings, the matching last.pt in the same output directory, and a larger epoch limit. Changing the bound training source requires a new run. CPU PyTorch 2.8.0 was used.
