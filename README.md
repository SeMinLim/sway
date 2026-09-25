# Sway

* A selective state-space model accelerator project for Lattice ECP5 FPGAs using Bluespec SystemVerilog (BSV).
* Includes a MARS pose-regression model, FP32 training, INT8 post-training quantization (PTQ), and a dedicated-engine baseline for ULX3S-85F.

## File structure

* hw/
  * Baseline hardware, UART interface, simulation, and FPGA build configuration.
  * `bsv/`: compute modules and parallelism settings.
  * `model/` & `generated/`: frozen checkpoint, parameter tables, and reference outputs.
  * `sim/` & `reference/`: testbenches, integer reference, and verification scripts.
  * `results/mars_v2/`: validation and synthesis results for the current baseline.
* sw/
  * Model implementation, training, quantization, and evaluation scripts.
  * `config/`: model settings and reconstruction choices.
  * `results/mars_ptq_20260925/`: corrected model checkpoint, INT8 exports, and training/evaluation records.

## Prerequisites & Dependencies

* blueYosys (Required for hardware builds)
  * Sway uses the shared build flow provided by [blueYosys](https://github.com/SeMinLim/blueyosys).
  * Keep blueYosys at the same level as Sway, or pass `ROOTDIR=/absolute/path/to/blueyosys` to Make.
* Environment Setup
  * Operating System: Linux with GNU Make, GCC/G++, and Python 3.
  * Compiler: Bluespec Compiler (BSC) with Bluesim.
  * FPGA Tools: Yosys, nextpnr-ecp5, and ecppack.
  * Icarus Verilog is required when using `SIM_BACKEND=iverilog`.
* Software Evaluation
  * Python 3.10 or newer, NumPy, and PyTorch.
  * Install the Python dependencies with `python -m pip install -r sw/requirements.txt`.

## How to build

Run commands from the Sway repository root. The default board is ULX3S-85F.

* Simulation (Bluesim)
  * Run the baseline regression with input bubbles and output stalls:
  * `make -C hw runsim`
* Simulation (Generated Verilog)
  * Run the same regression with Icarus Verilog:
  * `make -C hw runsim SIM_BACKEND=iverilog`
* Verilog Generation
  * Compile the BSV kernel and UART wrapper:
  * `make -C hw verilog`
* Actual Hardware Synthesis (Bitstream Generation)
  * Run synthesis, placement, routing, and bitstream generation:
  * `make -C hw synth`
  * Use `make -C hw netlist` or `make -C hw pnr` to stop at an intermediate stage.

## Baseline configuration

* Patch embedding, two independent Mamba blocks, and a regression head are connected through FIFOs.
* Each block uses separate main/gate input projections and separate delta-input/B/C projections. Gate SiLU runs in its own stage after the gate projection.
* Convolution feeds the SSM path without SiLU. Delta follows `Linear -> ReLU -> Linear`, retaining the signed second projection.
* The kernel has 17 affine engines, with no sharing across projections or blocks.
* Change `ParallelismDivisor` in [SwayTypes.bsv](hw/bsv/SwayTypes.bsv) to select parallelism:

| Divisor | Affine lanes per engine | Norm / Conv / Gate / Scan lanes |
| --- | ---: | ---: |
| 1 | 4 | 2 |
| 2 | 2 | 1 |
| 4 (Default) | 1 | 1 |

* Run `make -C hw clean` and rebuild after changing the divisor. Parameter tables do not need regeneration.
* See the [hardware README](hw/README.md) for interfaces, numerical formats, and verification commands.

## Software model

* `architecture_version=2` in [model.json](sw/config/model.json).
* Convolution output enters the SSM path directly. SiLU remains on the gate branch.
* Delta path: `Linear -> ReLU -> Linear`, without an activation after the second Linear.
* `hw/` uses the same version 2 checkpoint, with regenerated INT8 parameters, nonlinear tables, and reference outputs.

## How to run the checkpoint

Run from the repository root after installing the Python dependencies:

```sh
python sw/prepare_data.py --data-dir ../data/mars --download
python sw/evaluate_ptq.py \
  --data ../data/mars \
  --checkpoint sw/results/mars_ptq_20260925/final/checkpoint.pt \
  --output sw/results/mars_ptq_recheck \
  --batch-size 256 --threads 2
```

* Use a fresh output directory. This evaluates the frozen checkpoint without fitting or checkpoint selection.
* Evaluation covers exact FP32, piecewise-linear FP32, and INT8 PTQ, and exports the INT8 tensors.
* Training uses the official train/validation splits. Test evaluation follows checkpoint and PTQ scale selection.

## How to regenerate the checkpoint

```sh
DATA='../data/mars'
RUN='sw/results/my_mars_v2'

python sw/train.py \
  --data "$DATA" --output "$RUN/fp32" \
  --epochs 150 --batch-size 256 --threads 2 --seed 20260925 \
  --learning-rate 0.001 --weight-decay 0.0001 \
  --patience 25 --lr-patience 8 --min-delta 0.0001 --clip-grad 1 --device cpu

python sw/refit_head.py \
  --checkpoint "$RUN/fp32/best.pt" --data "$DATA" --output "$RUN/head_refit" \
  --ridges 0 0.0001 0.001 --batch-size 256 --threads 2 --device cpu

python sw/ptq_pwl.py \
  --checkpoint "$RUN/head_refit/best.pt" --data "$DATA" \
  --output "$RUN/prepared/pwl_fit.json" --output-checkpoint "$RUN/prepared/pwl_source.pt" \
  --samples 2048 --seed 0 --batch-size 256 --threads 1 --validate

python sw/calibrate_ptq.py \
  --checkpoint "$RUN/prepared/pwl_source.pt" --data "$DATA" --output "$RUN/calibration" \
  --calibration-samples 2048 --reconstruction-samples 1024 \
  --rounds 5 --seed 0 --batch-size 256 --threads 1

python sw/evaluate_ptq.py \
  --data "$DATA" --checkpoint "$RUN/prepared/pwl_source.pt" \
  --profile "$RUN/calibration/calibration.json" --output "$RUN/final" \
  --batch-size 256 --threads 2
```

* FP32 starts from random initialization. The head refit retains the original weights as a validation fallback.
* PWL uses both train-fitted functions: gate SiLU and exponential. `--validate` reports alternatives; it does not select their knots.
* PTQ selects scales by full-validation RMSE without updating model weights.

## Results

* Software Evaluation (Version 2)
  * Test RMSE: **8.1461 cm** for exact FP32, **8.1567 cm** for PWL FP32, and **9.3022 cm** for INT8 PTQ.
  * RMSE is the mean of 57 coordinatewise RMSE values over 7,984 official MARS test frames.
  * [Checkpoint](sw/results/mars_ptq_20260925/final/checkpoint.pt), [metrics](sw/results/mars_ptq_20260925/final/metrics.json), and [verification](sw/results/mars_ptq_20260925/verification.json).
  * All 36 software tests pass, including signed convolution outputs and `Linear -> ReLU -> Linear` checks in exact, PWL, PTQ, and QAT execution.
  * [Training protocol](sw/results/mars_ptq_20260925/protocol.json) & [architecture checks](sw/results/mars_ptq_20260925/architecture_verification.json).
  * INT8 retains the existing `Abar` scale of `2^-7`, with a maximum of `127/128`. Signed delta permits larger values. On the first 2,048 training frames, **39.89%** of PTQ `Abar` values require saturation; full counts and PWL range limits are recorded in the verification report.
* Hardware Verification (Version 2)
  * All three parallelism settings pass both the stall regression and continuous-input kernel test: **4,788 matching INT8 outputs** across six runs.
  * All **329,988** rounding/saturation cases and **974,848** affine ROM address checks pass.
  * [Simulation results](hw/results/mars_v2/validation.json), [ROM verification](hw/results/mars_v2/weight_rom_verification.json), and [standalone regeneration](hw/results/mars_v2/standalone_generation.json).
  * Integer-reference RMSE: **9.3015 cm** over all 7,984 test frames. All 455,088 outputs match v2 software when only normalization uses the baseline's exact-rational definition; original QDQ differs by at most 3 LSB.
  * [Integer reference](hw/generated/reference_report.json) & [software comparison](hw/generated/software_contract_verification.json).
* Hardware Synthesis (Version 2)
  * Full `mkTop` synthesis for ULX3S-85F at divisor 4 completes with blueYosys `3663e87`, BSC 2026.01, and Yosys 0.33.
  * LUT4: **57,345**; FF: **47,804**; DSP: **78**; BRAM: **2**.
  * Logic use before packing is **84,405 / 83,640 (100.91%)**, exceeding capacity by **765 sites**. Placement/routing and timing closure remain unverified.
  * [Synthesis results](hw/results/mars_v2/synthesis/report.json).

## Notes

* Maintained by Se-Min Lim.
* The checkpoint is independently trained from the published eMamba settings. Model details and reconstruction choices are recorded in [model.json](sw/config/model.json); dataset membership is recorded in [data_manifest.json](sw/results/data_manifest.json).
* Hardware uses exact rational range normalization. Its comparison with software QDQ normalization is recorded in [numerical verification](hw/generated/software_contract_verification.json).
* Logic use is calculated as `LUT4 + 2 * CCU2C + 6 * TRELLIS_DPR16X4`; it is not a packed `TRELLIS_COMB` measurement. Placement/routing, timing closure, and physical-board operation remain unverified for the current baseline.
