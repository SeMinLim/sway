# Sway

Compact selective state-space model accelerator research for ECP5-class FPGAs. The repository contains a MARS pose-regression model with FP32 training and INT8 post-training quantization, plus a Bluespec implementation of the dedicated-engine baseline.

## FP32 and INT8 PTQ checkpoint

The [checkpoint](sw/results/mars_ptq_20260923/final/checkpoint.pt) contains FP32 reference weights, signed INT8 parameter tensors, and the frozen inference configuration and quantization scales. FP32 and INT8 evaluation use the same learned weights.

| Numerical model | Test RMSE (cm) | eMamba Table 4 (cm) | Difference (cm) |
|---|---:|---:|---:|
| FP32, exact SiLU/exp | 8.0681 | 7.85 | +0.2181 |
| FP32, fitted PWL inference | 8.0754 | — | — |
| INT8 PTQ | **8.3564** | 8.83 | -0.4736 |

RMSE is the mean of 57 coordinatewise RMSE values over all 7,984 official MARS test frames, in centimetres. Lower is better. Checkpoint reload, exported INT8 tensor equality, and independent metric recomputation from saved predictions pass: [metrics](sw/results/mars_ptq_20260923/final/metrics.json), [verification](sw/results/mars_ptq_20260923/verification.json).

All weight optimization uses exact FP32 SiLU and exponential functions. Training combines coordinate-RMSE refinement and an output-head ridge refit. After training, PWL coefficients and power-of-two quantization scales are calibrated on training frames with frozen weights. Validation selects the checkpoint and PTQ configuration before test evaluation. The selected PTQ configuration uses fitted PWL functions and five scale-reconstruction rounds.

## Model and dataset

The model uses D=20, E=2, P=2, M=2, N=8, 16 spatial tokens, range normalization, ReLU selective step sizes, and 15,717 learned parameters. Inputs are 8×8×5 radar features; outputs are 19 X, 19 Y, and 19 Z coordinates in metres. Implementation choices are recorded in [model.json](sw/config/model.json).

The [official MARS arrays](https://github.com/SizheAn/MARS/tree/dc902822f864ca0d5df90d559d53d3cd919d3c7c/feature) contain 24,066 training, 8,033 validation, and 7,984 test frames. Dataset identities and preprocessing are recorded in [data_manifest.json](sw/results/data_manifest.json).

This is a reconstruction of the published [eMamba configuration](https://arxiv.org/html/2508.10370v1). The official MARS membership is approximately 60/20/20, while eMamba states 64/16/20. Author split indices, checkpoint, regression-head details, and PWL coefficients were not supplied. The table therefore compares reported numbers across different implementation and split conditions.

The INT8 software path uses symmetric signed INT8 parameters and ordinary activation nodes, power-of-two scales, INT24 current state, and INT17 retained recurrent state. Affine/convolution operations and SSM recurrence use the integer reference; normalization and remaining nonlinear/elementwise operations use quantize/dequantize simulation.

## Run the checkpoint

Python 3.10 or newer and PyTorch are required. The measured environment used Python 3.12.14, PyTorch 2.8.0+cpu, and NumPy 2.3.5. From the repository root:

```sh
python -m pip install -r sw/requirements.txt
python sw/prepare_data.py --data-dir ../data/mars --download
python sw/evaluate_ptq.py \
  --data ../data/mars \
  --checkpoint sw/results/mars_ptq_20260923/final/checkpoint.pt \
  --output sw/results/mars_ptq_recheck \
  --batch-size 256 --threads 2
```

Use a fresh output directory. The command loads the frozen profile, evaluates exact FP32, PWL FP32, and INT8 PTQ, and exports the INT8 tensors. It performs no fitting or checkpoint selection.

[Training and PTQ reproduction commands](sw/results/mars_ptq_20260923/README.md#reproduce-training-and-ptq-selection) cover the included restart source, FP32 refinement, head refit, PWL fitting, calibration, and validation selection.

## Checkpoint artifacts

Artifacts are under [`sw/results/mars_ptq_20260923/`](sw/results/mars_ptq_20260923/README.md).

| Artifact | Contents |
|---|---|
| [final/checkpoint.pt](sw/results/mars_ptq_20260923/final/checkpoint.pt) | FP32 reference, INT8 tensors, model configuration, and frozen PTQ profile |
| [restart_head_refit/best.pt](sw/results/mars_ptq_20260923/restart_head_refit/best.pt) | Selected FP32 checkpoint before inference preparation |
| [final/metrics.json](sw/results/mars_ptq_20260923/final/metrics.json) | Test metrics, source/data hashes, and export checks |
| [final/calibration.json](sw/results/mars_ptq_20260923/final/calibration.json) | Quantization scales and calibration provenance |
| [final/export/](sw/results/mars_ptq_20260923/final/export/) | INT8 binary/hex tensors and layout metadata |
| `final/predictions_*.npy` | Saved predictions for all three arithmetic modes |
| [selection.json](sw/results/mars_ptq_20260923/selection.json) | Frozen validation-based checkpoint/profile selection |
| [verification.json](sw/results/mars_ptq_20260923/verification.json) | Metric recomputation, checkpoint checks, and software-test results |

## Baseline hardware

`hw/` is a self-contained blueYosys project implementing the dedicated-engine MARS baseline. Patch embedding feeds two Mamba blocks and the regression head through explicit FIFOs. All 11 affine layers retain separate four-lane engines; each block has its own normalization, convolution, delta projection, scan, and output projection. Input frames and flattened head inputs use the baseline register/FIFO organization.

The hardware uses the current PTQ checkpoint's INT8 parameters, scales, and fitted PWL functions. Its exact rational range normalization gives integer-reference test RMSE **8.3579082742 cm**, compared with **8.3563735004 cm** for the software QDQ path. [Numerical verification](hw/generated/software_contract_verification.json) checks the checkpoint/export, all PWL table entries, and normalization correspondence.

The source follows the supplied BSV style: explicit FIFO boundaries, numbered stage rules, visible control/counter updates, named static dimensions, and thin input/output methods. [Project README](hw/README.md) documents the architecture, files, and validation.

## Run the baseline in blueYosys

The matching project is [`blueyosys/projects/sway_observation`](https://github.com/SeMinLim/blueyosys/tree/main/projects/sway_observation). From the blueYosys repository root:

```sh
make runsim PROJECT=sway_observation BOARD=ulx3s-85f
make runsim PROJECT=sway_observation BOARD=ulx3s-85f SIM_BACKEND=iverilog
make netlist PROJECT=sway_observation BOARD=ulx3s-85f
make pnr PROJECT=sway_observation BOARD=ulx3s-85f
```

Or run the same hardware directory from Sway:

```sh
make -C hw runsim ROOTDIR=/absolute/path/to/blueyosys
```

Bluesim and Icarus pass the fixed 14-frame, 798-output test, including input bubbles, output backpressure, and per-frame state reset. The restored core matches the first baseline’s outputs, event cycles, and 115-rule compiler schedule. Project-top Verilog generation and ECP5 netlist synthesis also pass: [validation](hw/results/validation.json).

The physical build fails during placement on ULX3S-85F because **145,239 / 83,640 TRELLIS_COMB sites (173.65%)** are required. The core clock is correctly constrained to 100 MHz, but routing and post-route timing are not reached; no routed Fmax or slack is available. See the [physical result](hw/results/physical/result.json). Physical-board operation has not been tested.
