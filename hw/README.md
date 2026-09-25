# MARS baseline for blueYosys

Dedicated-engine MARS v2 inference for ULX3S-85F, using the frozen INT8 PTQ checkpoint in `model/` from `sw/results/mars_ptq_20260925/final/`.

## Architecture

Patch embedding feeds two independent Mamba blocks and the regression head through FIFOs. Each block contains separate main/gate input projections, normalization, convolution, gate SiLU, delta-input/B/C projections, delta expansion, scan, and output projection. The complete kernel has 17 affine engines; no engine is shared across projections or blocks.

Main projection feeds convolution; its quantized output enters the SSM path without SiLU. Gate projection feeds an independent SiLU stage, then waits in an ordered FIFO for the scan result. Delta-input, B, and C projections run in independent engines and rejoin before scan. Delta follows `Linear -> ReLU -> Linear`: ReLU applies before delta-input branch requantization, and delta expansion remains signed. Each split preserves the original matrix rows and affine-then-branch requantization sequence.

The four nonlinear INT8 tables contain gate SiLU and exponential values for each block. Current recurrent state is INT24 and retained state is INT17. Affine accumulation uses INT24, convolution alignment INT18, recurrence alignment INT26, scan output accumulation INT35, and residual alignment INT10. All 61 static bounds pass for the new frozen scales. The existing `Abar` scale remains `2^-7`, saturating at `127/128`, including when signed delta produces a larger exponential.

Affine weight ROM pages use fixed 256-bit truth tables for each output bit, followed by the existing upper-address page selection. Weight order, signed INT8 values, lane banks, and combinational read latency are unchanged.

Each affine engine retains its output vector in per-element INT8 registers with explicit group write enables. The final group feeds the output FIFO directly in the same cycle; earlier groups come from their registers. Input storage and selection, vector dimensions, arithmetic, and computation order are unchanged.

## Parallelism

Change one typedef in `bsv/SwayTypes.bsv`:

```bsv
typedef 4 ParallelismDivisor;
```

| Divisor | Affine lanes per engine | Norm / Conv / Gate / Scan lanes |
| --- | ---: | ---: |
| 1 | 4 | 2 |
| 2 | 2 | 1 |
| 4 (default) | 1 | 1 |

Counters, bank selection, state addresses, and reductions follow the selected divisor. The checked-in parameter tables support all three settings without regeneration. Clean and rebuild after changing it.

## Interfaces

* Input: 320 signed INT8 values per frame in HWC order.
* Output: 57 signed INT8 coordinates in X19/Y19/Z19 order.
* Scales: `model/export/quantization.json`.
* UART: send one complete frame, then receive its 57-byte reply, at 115200 baud.
* Configured core clock: 100 MHz.

## How to build

Install BSC/Bluesim and [blueYosys](https://github.com/SeMinLim/blueyosys). From the Sway repository root:

| Task | Command |
| --- | --- |
| Run the regression | `make -C hw runsim ROOTDIR=/path/to/blueyosys` |
| Run generated Verilog | `make -C hw runsim ROOTDIR=/path/to/blueyosys SIM_BACKEND=iverilog` |
| Generate Verilog | `make -C hw verilog ROOTDIR=/path/to/blueyosys` |
| Synthesize the netlist | `make -C hw netlist ROOTDIR=/path/to/blueyosys` |
| Place and route | `make -C hw pnr ROOTDIR=/path/to/blueyosys` |
| Generate the bitstream | `make -C hw synth ROOTDIR=/path/to/blueyosys` |

Icarus Verilog is required for generated-Verilog simulation. The FPGA flow also requires Yosys, nextpnr-ecp5, and ecppack. To build inside blueYosys, copy this directory into `projects/sway_observation/` and select `PROJECT=sway_observation`.

## File structure

* `HwMain.bsv`, `Top.bsv`: UART adapter, clock crossing, and PLL wrapper.
* `bsv/`: compute modules and clock wrapper.
* `model/`: frozen checkpoint, INT8 export, and provenance.
* `generated/`: parameter tables and fixed golden fixtures.
* `sim/TbSway.bsv`: regression with source bubbles, output stalls, repeated frames, and trailing-output checks.
* `sim/TbSwayKernel.bsv`: continuous-source, immediate-sink regression using the same golden outputs.
* `reference/`: integer reference, parameter generator, and test runners.
* `results/mars_v2/`: validation and synthesis for the current checkpoint and activation order.

## Validation

Run all three lane configurations and both testbenches with:

```sh
python3 hw/reference/check_refactor.py --backend iverilog --output hw/results/mars_v2/recheck
```

The runner builds isolated copies, checks every output against the fixed 14-frame / 798-coordinate fixtures, and tests rounding and saturation boundaries. Kernel cycle counts exclude intentional source/sink stalls and do not establish physical timing.

All three configurations pass both 14-frame tests: **4,788 INT8 outputs** match across six runs. All **329,988** rounding/saturation boundary cases pass. Tests use BSC 2026.01 and Icarus Verilog 12.0. [Regression results](results/mars_v2/validation.json) record the checked source hashes and test scope.

The new weight ROMs pass all **974,848 addresses** across 119 banks, including padding and out-of-range zeros. [ROM verification](results/mars_v2/weight_rom_verification.json) records the exact v2 weights. [Standalone regeneration](results/mars_v2/standalone_generation.json) reproduces the parameter BSV, nonlinear tables, and golden fixtures byte for byte without the software tree or original dataset.

Normal builds use the checked-in tables and fixtures. Regeneration requires NumPy/PyTorch and `python3 reference/generate.py` from this directory; it performs no training or calibration. With the official dataset, run `python3 hw/reference/check_contract.py --data /path/to/mars` from the repository root to reproduce the software comparison.

The frozen checkpoint SHA-256 is `5a9ebcc932923fc1e1b6e9e1d4376eed8e821b3d4d9559bc6af1a3b8571553aa`. Its parameters and scales are unchanged from the software bundle. The [integer-reference report](generated/reference_report.json) records generation and width checks; [software contract verification](generated/software_contract_verification.json) covers all 7,984 official test frames and all 1,024 nonlinear table entries.

All 455,088 integer outputs match the v2 software graph when only range normalization is replaced with the baseline's existing exact-rational definition. Against original float32 QDQ normalization, 435,831 outputs match exactly and the maximum difference is 3 LSB. Integer-reference coordinate-mean RMSE is **9.3015 cm**, versus **9.3022 cm** for software PTQ. This is reference evaluation; RTL regression uses the 14 fixed frames.

## Synthesis

Full `mkTop` synthesis for ULX3S-85F at divisor 4 completes with blueYosys `3663e87`, BSC 2026.01, and Yosys 0.33. The mapped design uses **57,345 LUT4**, **12,774 CCU2C**, **252 TRELLIS_DPR16X4**, **47,804 FF**, **78 DSP**, and **2 BRAM**.

Logic use before packing, calculated as `LUT4 + 2 * CCU2C + 6 * TRELLIS_DPR16X4`, is **84,405 / 83,640 (100.91%)**. This exceeds capacity by **765 sites**. [Synthesis results](results/mars_v2/synthesis/report.json) record the source hashes and measured counts. Packing, placement/routing, timing closure, and physical-board operation remain unverified.
