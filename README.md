# Sway

Compact selective state-space model accelerator research for ECP5-class FPGAs. The repository contains a MARS pose-regression model with FP32 training and INT8 post-training quantization, plus a Bluespec implementation with a shared delta-projection engine.

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

## Hardware

The shared-delta configuration targets ULX3S-85F and implements patch embedding, two selective-SSM blocks, and the regression head. FIFO token handoff connects the layer engines. Engine-specific affine lane allocation uses 20 lanes in total, with per-block `xDelayQ=2` and `residualQ=5`.

Both blocks use one scalar delta-projection engine. Each block has independent request/result queues. Round-robin arbitration starts a job only when its destination result slot is free; the block and token remain fixed until all 40 outputs finish. A blocked consumer cannot reserve the other block's output slot. Recurrent state uses INT24 current values and INT17 retained values.

The hardware results use the checked-in `hw/generated/` ROMs and fixtures. The PTQ checkpoint above has software validation and has not been installed or validated in this RTL build.

| Shared-delta hardware metric | Result |
|---|---:|
| Validated operating clock | 60 MHz |
| Continuous completion interval | 7,600 cycles/frame |
| Derived kernel throughput | 7,894.74 frames/s |
| Isolated-frame latency | 20,763 cycles |
| Packed logic, `TRELLIS_COMB` | 43,538 |
| Flip-flops, `TRELLIS_FF` | 44,555 |
| Block RAMs, `DP16KD` | 41 |
| DSPs, `MULT18X18D` | 0 |

Throughput is derived from functional-simulation cycles and passing post-route timing at 60 MHz. Resource counts include the kernel and board wrapper. These are not physical-board or UART-transfer measurements. Evidence: [kernel performance](hw/results/resource_comparison/clock60/shared/perf/summary.json), [physical result](hw/results/resource_comparison/clock60/shared/physical/physical_result.json).

Validation checks 57 isolated-frame outputs and 3,648 continuous-run outputs against the integer reference. Bluesim and native-ROM Verilator stress tests each check 798 outputs with input bubbles and output stalls. The [delta-equivalence check](hw/results/resource_comparison/delta-equivalence/summary.json) exhausts all 65,536 INT8 input pairs for both layers.

## Build the hardware

Use [blueyosys](https://github.com/SeMinLim/blueyosys/tree/3ea0afea56c7c73b8ed59ec0edd256c409466788), BSC/Bluesim, Verilator, and the ECP5 OSS CAD tools on `PATH`. The Makefile includes blueyosys `build.mk`. Select the shared 60 MHz configuration explicitly:

```sh
make -C hw perf runsim check-verilator synth \
  BLUEYOSYS=/absolute/path/to/blueyosys \
  INPUT_PROJECTION_VARIANT=B2 RESOURCE_VARIANT=shared CORE_MHZ=60
```

This configuration writes to `hw/results-B2-shared-60mhz/` with separate build directories. `perf` records simulation cycles; `synth` requires completed routing, bitstream packing, and passing core/UART timing checks. The core PLL produces 60 MHz and the UART constraint is 25 MHz. Synthesis uses `-nodsp` and control replication with structural-equivalence checks.
