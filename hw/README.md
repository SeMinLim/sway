# MARS baseline for blueYosys

Dedicated-engine MARS inference for ULX3S-85F, using the frozen INT8 PTQ checkpoint in `model/`.

## Architecture

Patch embedding feeds two independent Mamba blocks and the regression head through FIFOs. Each block contains separate main/gate input projections, normalization, convolution, gate SiLU, delta-input/B/C projections, delta expansion, scan, and output projection. The complete kernel has 17 affine engines; no engine is shared across projections or blocks.

Main projection feeds convolution and its SiLU stage. Gate projection feeds an independent SiLU stage, then waits in an ordered FIFO for the scan result. Delta-input, B, and C projections run in independent engines and rejoin before scan. Each split preserves the original matrix rows and the original affine-then-branch requantization sequence.

The nonlinear INT8 lookup and checkpoint remain unchanged. Current recurrent state is INT24 and retained state is INT17. Affine accumulation uses INT24, convolution alignment INT18, recurrence alignment INT26, scan output accumulation INT35, and residual alignment INT10. Static bounds check the frozen scales before elaboration.

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
* `results/engine_refactor/`: validation for the independent-engine revision.

## Validation

Run all three lane configurations and both testbenches with:

```sh
python3 hw/reference/check_refactor.py --backend iverilog
```

The runner builds isolated copies, checks every output against the fixed 14-frame / 798-coordinate fixtures, and tests rounding and saturation boundaries. Kernel cycle counts exclude intentional source/sink stalls and do not establish physical timing.

All three configurations pass both 14-frame tests, with all 798 outputs matching in each run. The 329,988 rounding/saturation cases also pass. Tests used BSC 2026.01 and Icarus Verilog 12.0. Default-configuration project-top Verilog generation passes. [Validation results](results/engine_refactor/validation.json) and [top compilation](results/engine_refactor/top_verilog.json) record the checked sources and scope. Historical records elsewhere in `results/`, including the 173.65% placement failure, describe the earlier fused implementation. They are not resource or timing measurements of this revision. Physical-board operation is untested.

Normal builds use the checked-in tables and fixtures. Regeneration requires NumPy/PyTorch and `python3 reference/generate.py` from this directory; it performs no training or calibration. The saved [integer-reference report](generated/reference_report.json) and [software contract verification](generated/software_contract_verification.json) describe the frozen numerical model.
