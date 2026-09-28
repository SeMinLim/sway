# Sway

* A selective state-space model accelerator project for Lattice ECP5 FPGAs using Bluespec SystemVerilog (BSV).
* Includes a MARS pose-regression model, FP32 training, INT8 post-training quantization (PTQ), and a dedicated-engine baseline for ULX3S-85F.

## File structure

* hw/
  * Baseline hardware, UART interface, simulation, and FPGA build configuration.
  * `bsv/`: compute modules and parallelism settings.
  * `model/` & `generated/`: frozen checkpoint, parameter tables, and reference outputs.
  * `sim/` & `reference/`: testbenches, integer reference, and verification scripts.
  * `results/baseline/`: validation and synthesis results for the current baseline.
* sw/
  * Model implementation, training, quantization, and evaluation scripts.
  * `config/`: model settings and reconstruction choices.
  * `results/mars/`: model checkpoint, INT8 exports, and training/evaluation records.

## Prerequisites & Dependencies

* blueYosys (Required for hardware builds)
  * Sway uses the shared build flow provided by [blueYosys](https://github.com/SeMinLim/blueyosys).
  * Keep blueYosys at the same level as Sway, or pass `ROOTDIR=/absolute/path/to/blueyosys` to Make.
* Environment Setup
  * Operating System: Linux with GNU Make, GCC/G++, and Python 3.
  * Compiler: Bluespec Compiler (BSC) with Bluesim.
  * FPGA Tools: Yosys, nextpnr-ecp5, and ecppack.
* Software Evaluation
  * Python 3.10 or newer, NumPy, and PyTorch.
  * Install the Python dependencies with `python -m pip install -r sw/requirements.txt`.

## How to build

Run commands from the Sway repository root. The default board is ULX3S-85F.

* Simulation (Bluesim)
  * Run the baseline regression with input bubbles and output stalls:
  * `make -C hw runsim`
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
* Frame input, block `xR`/`gateR`/`gatedR`, normalization output, and scan output use per-element INT8 registers with explicit write-enables. The last input or result group feeds the next stage directly in the same cycle.
* Change `ParallelismDivisor` in [SwayTypes.bsv](hw/bsv/SwayTypes.bsv) to select parallelism:

| Divisor | Affine lanes per engine | Norm / Conv / Gate / Scan lanes |
| --- | ---: | ---: |
| 1 | 4 | 2 |
| 2 | 2 | 1 |
| 4 (Default) | 1 | 1 |

* Run `make -C hw clean` and rebuild after changing the divisor. Parameter tables do not need regeneration.
* See the [hardware README](hw/README.md) for interfaces, numerical formats, and verification commands.

## Software model

* Model dimensions and reconstruction choices are defined in [model.json](sw/config/model.json).
* Convolution output enters the SSM path directly. SiLU remains on the gate branch.
* Delta path: `Linear -> ReLU -> Linear`, without an activation after the second Linear.
* `hw/` contains the frozen checkpoint and its generated INT8 parameters, nonlinear tables, and integer-reference outputs.

## Results

* Software Evaluation
  * Test RMSE: **8.1461 cm** for exact FP32, **8.1567 cm** for PWL FP32, and **9.3022 cm** for INT8 PTQ.
  * RMSE is the mean of 57 coordinatewise RMSE values over 7,984 official MARS test frames.
  * [Checkpoint](sw/results/mars/final/checkpoint.pt), [metrics](sw/results/mars/final/metrics.json), and [verification](sw/results/mars/verification.json).
  * All 35 software tests pass. [Cleanup verification](sw/results/mars/cleanup_verification.json) confirms unchanged FP32, PWL, and INT8 inference on 264 frames, including the eight real hardware fixtures.
  * [Training protocol](sw/results/mars/protocol.json) & [architecture checks](sw/results/mars/architecture_verification.json).
  * INT8 retains the existing `Abar` scale of `2^-7`, with a maximum of `127/128`. Signed delta permits larger values. On the first 2,048 training frames, **39.89%** of PTQ `Abar` values require saturation; full counts and PWL range limits are recorded in the verification report.
* Hardware Verification
  * All three parallelism settings pass both the stall regression and continuous-input kernel test: **4,788 matching INT8 outputs** across six runs. Output order and all recorded cycle counts are unchanged.
  * Generated RTL checks confirm per-element write-enables and final-result bypasses for all 11 modified storage arrays.
  * All **329,988** rounding/saturation cases and **974,848** affine ROM address checks pass.
  * [Simulation results](hw/results/baseline/validation.json), [ROM verification](hw/results/baseline/weight_rom_verification.json), and [standalone regeneration](hw/results/baseline/standalone_generation.json).
  * Integer-reference RMSE: **9.3015 cm** over all 7,984 test frames. All 455,088 outputs match the software model when only normalization uses the baseline's exact-rational definition; original QDQ differs by at most 3 LSB.
  * [Integer reference](hw/generated/reference_report.json) & [software comparison](hw/generated/software_contract_verification.json).
* Hardware Synthesis
  * Full `mkTop` synthesis for ULX3S-85F at divisor 4 completes with blueYosys `3663e87`, BSC 2026.01, and Yosys 0.33.
  * LUT4: **49,697**; FF: **47,716**; DSP: **78**; BRAM: **2**.
  * Logic use before packing is **76,757 / 83,640 (91.77%)**, leaving **6,883 sites** below the capacity limit. Placement/routing and timing closure remain unverified.
  * [Synthesis results](hw/results/baseline/synthesis/report.json).

## Notes

* Maintained by Se-Min Lim.
* The checkpoint is independently trained from the published eMamba settings. Model details and reconstruction choices are recorded in [model.json](sw/config/model.json); dataset membership is recorded in [data_manifest.json](sw/results/data_manifest.json).
* Hardware uses exact rational range normalization. Its comparison with software QDQ normalization is recorded in [numerical verification](hw/generated/software_contract_verification.json).
* Logic use is calculated as `LUT4 + 2 * CCU2C + 6 * TRELLIS_DPR16X4`; it is not a packed `TRELLIS_COMB` measurement. Placement/routing, timing closure, and physical-board operation remain unverified for the current baseline.
