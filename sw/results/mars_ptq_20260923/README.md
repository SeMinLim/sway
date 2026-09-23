# MARS FP32 and PTQ checkpoint (2026-09-23)

[final/checkpoint.pt](final/checkpoint.pt) is the self-contained checkpoint for the paired FP32 and INT8 PTQ evaluation. It includes actual signed INT8 parameter tensors and the frozen inference profile, as well as the corresponding FP32 reference weights. The model retains D=20, E=2, P=2, M=2, N=8 and 15,717 learned parameters.

| Numerical model | This checkpoint test RMSE (cm) | eMamba Table 4 (cm) | Difference (cm) |
|---|---:|---:|---:|
| FP32, exact SiLU/exp | 8.0681 | 7.85 | +0.2181 |
| FP32, fitted PWL inference | 8.0754 | — | — |
| INT8 PTQ | **8.3564** | 8.83 | -0.4736 |

The reported metric is the mean of 57 coordinatewise RMSE values over all 7,984 official MARS test frames, in centimetres. Exact FP32, PWL FP32, and INT8 PTQ use the same selected weights. [final/metrics.json](final/metrics.json) records each metric, axis errors, data/source hashes, and checkpoint/export verification. Saved prediction arrays allow independent metric recomputation.

## Training and PTQ protocol

Weights are trained only in ordinary FP32 with exact SiLU and exponential functions. Coordinate-RMSE refinement continues the prior validation-selected FP32 model. The focused restart runs for 160 epochs and selects epoch 157 at validation RMSE 7.9731855171 cm. The final linear-head refit selects ridge 0.001 at validation RMSE 7.9596001163 cm: [restart_head_refit/best.pt](restart_head_refit/best.pt). These are validation measurements, not test results.

Head refitting solves a training-only least-squares/ridge problem with penalties 0, 0.0001, and 0.001, retaining the untouched source as a fallback. It does not use quantized predictions. The selected lineage uses neither QAT nor PWL weight training. Files in `diagnostics/` include exploratory comparisons against historical PWL-trained checkpoints; those checkpoints are not used in this final lineage. Training histories and run metadata record selected epochs and source checkpoint hashes.

After FP32 training, `ptq_pwl.py` observes 2,048 training frames to fit SiLU and exponential knot locations, retaining 17 and 11 segments. Validation compares the original knots, fitted exponential knots only, and both fitted functions. PWL calibration changes inference coefficients, not learned parameter tensors or exact FP32 outputs.

`ptq_equalize.py` provides function-preserving candidates for PTQ: division of input channels by `[1, 1, 1, 1, 4]` with compensation in the embedding weights, and power-of-two equalization across the ReLU head. Any selected input divisors are stored in `config` and applied by `forwardModel`; the caller supplies the original MARS arrays. Head equalization rescales adjacent layers together. The preparation step checks unchanged FP32/PWL predictions on training frames. These are algebraic parameter transformations, with no optimizer or gradient updates.

`calibrate_ptq.py` compares train-calibrated clipping profiles and performs discrete power-of-two scale search. Its reconstruction objective matches the frozen exact FP32 outputs on 1,024 training frames. Both PWL-only and equalized candidates are configured for up to five reconstruction rounds, stopping earlier if no scale changes improve reconstruction. Full-validation RMSE selects among the recorded profiles, rounds, and the two candidates. No model parameter is updated during this search. Each profile records source hashes and frozen-weight provenance; each calibration directory's `reconstruction_history.json` records the explicit unchanged-parameter check. The selected configuration is frozen before the test arrays are opened.

The final [validation selection](selection.json) chose the PWL-only candidate at reconstruction round 5 (validation RMSE 8.2583008208 cm); input/head equalization was not selected. Its learned weights remain bit-identical to `restart_head_refit/best.pt`. The paired test result is FP32 8.0680969482 cm and INT8 PTQ 8.3563735004 cm. [Verification](verification.json) also independently recomputes these metrics from the saved predictions. Lower RMSE is better; the table compares reported numbers across the different conditions described below.

The train/validation/test memberships remain 24,066/8,033/7,984. The INT8 path uses symmetric signed INT8 parameters and ordinary activation nodes, with INT24 current state and INT17 retained recurrent state. Affine/convolution accumulators use the integer reference; normalization and remaining nonlinear/elementwise operations use the documented quantize/dequantize simulation. This checkpoint is a software numerical artifact.

## Checkpoint contents

| Key or artifact | Contents |
|---|---|
| `model`, `config` | FP32 reference parameters and model/inference configuration |
| `int8_parameters` | Signed `torch.int8` deployment tensors, including `A=-exp(A_log)` |
| `ptq_profile`, `ptq_profile_sha256` | Frozen quantization scales, bit widths, calibration provenance, and digest |
| `quantization_method` | `PTQ` |
| `source_checkpoint_sha256`, `dataset_sha256` | Source and dataset identity |
| `final/export/` | Matching binary/hex INT8 tensors, scales, and layout metadata |
| `final/predictions_*.npy` | Exact FP32, PWL FP32, and INT8 PTQ test predictions |

The bundle loader checks profile/source compatibility and verifies that saved INT8 tensors equal quantization of its FP32 reference. Final evaluation checks bundle reload output equality and exported tensor equality. The bundle is for inference, not optimizer resume.

## Evaluate the frozen checkpoint

From the repository root, with the official arrays already prepared:

```sh
python -m pip install -r sw/requirements.txt
python sw/evaluate_ptq.py \
  --data ../data/mars \
  --checkpoint sw/results/mars_ptq_20260923/final/checkpoint.pt \
  --output sw/results/mars_ptq_recheck \
  --batch-size 256 --threads 2
```

Use a new output directory. This command loads the existing profile, evaluates all three arithmetic modes, and exports the frozen INT8 tensors. It performs no training, calibration, or checkpoint selection. Supplying `--profile` is unnecessary for the self-contained bundle.

To evaluate the selected pre-PTQ FP32 checkpoint separately:

```sh
python sw/evaluate.py \
  --data ../data/mars \
  --checkpoint sw/results/mars_ptq_20260923/restart_head_refit/best.pt \
  --output sw/results/mars_fp32_recheck \
  --batch-size 256 --threads 2
```

The paired bundle evaluation is the authoritative comparison for the PTQ result.

## Reproduce training and PTQ selection

The measured environment was Python 3.12.14, CPU PyTorch 2.8.0+cpu, and NumPy 2.3.5. The commands below start from the included frozen restart source, preserve the model architecture, and write to a fresh output directory. They reproduce the 160-epoch restart directly; the recorded run reached the same total through a resumable continuation. Set `DATA` to the official MARS array directory and choose an unused `RUN` directory.

```sh
DATA='../data/mars'
RUN='sw/results/my_ptq_reproduction'

python sw/refine_fp32.py \
  --checkpoint sw/results/mars_ptq_20260923/fp32_restart_source.pt \
  --data "$DATA" --output "$RUN/fp32_restart" \
  --epochs 160 --batch-size 128 --threads 2 --seed 20260923 \
  --learning-rate 0.0003 --weight-decay 0.0001 --clip-grad 1 \
  --patience 25 --lr-patience 6 --min-delta 0.0001 --device cpu

python sw/refit_head.py \
  --checkpoint "$RUN/fp32_restart/best.pt" \
  --data "$DATA" --output "$RUN/restart_head_refit" \
  --ridges 0 0.0001 0.001 --batch-size 256 --threads 2 --device cpu

python sw/ptq_pwl.py \
  --checkpoint "$RUN/restart_head_refit/best.pt" --data "$DATA" \
  --output "$RUN/prepared/pwl_fit.json" \
  --output-checkpoint "$RUN/prepared/pwl_source.pt" \
  --samples 2048 --seed 0 --batch-size 256 --threads 1 --validate

python sw/ptq_equalize.py \
  --checkpoint "$RUN/prepared/pwl_source.pt" --data "$DATA" \
  --output-checkpoint "$RUN/prepared/equalized_source.pt" \
  --input-divisors 1 1 1 1 4 --equalize-head --threads 1

for CANDIDATE in pwl equalized; do
  python sw/calibrate_ptq.py \
    --checkpoint "$RUN/prepared/${CANDIDATE}_source.pt" --data "$DATA" \
    --output "$RUN/calibration_${CANDIDATE}" \
    --calibration-samples 2048 --reconstruction-samples 1024 \
    --rounds 5 --seed 0 --batch-size 256 --threads 1
done
```

Freeze the candidate with lower full-validation RMSE, then evaluate the test split once:

```sh
python - "$RUN" "$DATA" <<'PY_SELECT'
import json
from pathlib import Path
import subprocess
import sys

run = Path(sys.argv[1])
candidates = {}
for name in ['pwl', 'equalized']:
    report = json.loads((run / ('calibration_' + name) / 'selection.json').read_text())
    candidates[name] = report['best_validation_rmse_cm']
selected = min(candidates, key=candidates.get)
print('Selected by validation:', selected, candidates[selected], 'cm', flush=True)
subprocess.run([
    sys.executable, 'sw/evaluate_ptq.py', '--data', sys.argv[2],
    '--checkpoint', str(run / 'prepared' / (selected + '_source.pt')),
    '--profile', str(run / ('calibration_' + selected) / 'calibration.json'),
    '--output', str(run / 'final'), '--batch-size', '256', '--threads', '2',
], check=True)
PY_SELECT
```

`evaluate_ptq.py` creates the self-contained bundle before opening test arrays. Each calibration profile records the command arguments and source hashes. The [focused software tests](focused_tests.log) recorded 37 passes and one skipped legacy-checkpoint case.

## Relation to eMamba and historical results

[eMamba, Table 4](https://arxiv.org/html/2508.10370v1) reports FP32 RMSE 7.85 cm and INT8 PTQ RMSE 8.83 cm. This run uses PTQ. The earlier [2026-09-22 result](../mars_accuracy_20260922/README.md) of 8.8135 cm used a separate QAT branch and is not the PTQ checkpoint provided here.

Our official MARS split is approximately 60/20/20, while eMamba states 64/16/20. Author split indices, checkpoint, regression-head details, and PWL coefficients were not supplied. These differences prevent an exact reproduction claim. The hardware ROMs and historical RTL/resource/timing results are unchanged; this run does not establish new FPGA measurements.
