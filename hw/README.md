# MARS baseline for blueYosys

Dedicated-engine MARS inference for ULX3S-85F, using the frozen INT8 PTQ checkpoint in `model/`.

## Architecture

Patch embedding feeds two independent Mamba blocks and the regression head through FIFOs. Each block contains separate main/gate input projections, normalization, convolution, gate SiLU, delta-input/B/C projections, delta expansion, scan, and output projection. The complete kernel has 17 affine engines; no engine is shared across projections or blocks.

Main projection feeds convolution and its SiLU stage. Gate projection feeds an independent SiLU stage, then waits in an ordered FIFO for the scan result. Delta-input, B, and C projections run in independent engines and rejoin before scan. Each split preserves the original matrix rows and the original affine-then-branch requantization sequence.

The nonlinear INT8 lookup and checkpoint remain unchanged. Current recurrent state is INT24 and retained state is INT17. Affine accumulation uses INT24, convolution alignment INT18, recurrence alignment INT26, scan output accumulation INT35, and residual alignment INT10. Static bounds check the frozen scales before elaboration.

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
* `results/engine_refactor/`: validation for the independent-engine revision.
* `results/lut_rom/`: weight ROM verification and before/after synthesis statistics.
* `results/linear_output_registers/`: output register verification and before/after synthesis statistics.

## Validation

Run all three lane configurations and both testbenches with:

```sh
python3 hw/reference/check_refactor.py --backend iverilog
```

The runner builds isolated copies, checks every output against the fixed 14-frame / 798-coordinate fixtures, and tests rounding and saturation boundaries. Kernel cycle counts exclude intentional source/sink stalls and do not establish physical timing.

All three configurations pass both 14-frame tests, with all 798 outputs matching in each run. The 329,988 rounding/saturation cases also pass. Tests used BSC 2026.01 and Icarus Verilog 12.0. Default-configuration project-top Verilog generation passes. [Validation results](results/engine_refactor/validation.json) and [top compilation](results/engine_refactor/top_verilog.json) record the checked sources and scope. Historical records elsewhere in `results/`, including the 173.65% placement failure, describe the earlier fused implementation. They are not resource or timing measurements of this revision. Physical-board operation is untested.

Normal builds use the checked-in tables and fixtures. Regeneration requires NumPy/PyTorch and `python3 reference/generate.py` from this directory; it performs no training or calibration. The saved [integer-reference report](generated/reference_report.json) and [software contract verification](generated/software_contract_verification.json) describe the frozen numerical model.

The weight LUT-ROM update passes all 974,848 addresses across 119 banks, including padding and out-of-range zero values. Divisor 4 stress and kernel regressions pass all 1,596 outputs; every recorded output value and cycle matches the preceding implementation. [ROM verification](results/lut_rom/weight_rom_verification.json), [regression](results/lut_rom/validation.json), and [cycle comparison](results/lut_rom/timing_comparison.json) record this check.

The weight LUT-ROM step reduced full `mkTop` LUT4 use from 67,982 to 64,204. Its [synthesis comparison](results/lut_rom/synthesis_comparison.json) records the preceding baseline.

The output register update passes all six stress/kernel tests at divisors 1, 2, and 4: 4,788 INT8 outputs match, and every recorded output cycle and BSC schedule is unchanged. [Regression results](results/linear_output_registers/validation.json) and [cycle/schedule comparison](results/linear_output_registers/timing_comparison.json) record these checks.

With the same blueYosys `3663e87`, BSC 2026.01, and Yosys 0.33 flow, full `mkTop` synthesis for ULX3S-85F at divisor 4 reduces LUT4 use from 64,204 to 60,517. Logic use before packing, calculated as `LUT4 + 2 * CCU2C + 6 * TRELLIS_DPR16X4`, falls from 92,426 (110.50%) to 88,739 (106.10%). FF falls from 47,887 to 47,751; DSP (78) and BRAM (2) are unchanged. The declared output storage remains 413 INT8 elements across 17 engines; synthesis removes the 136 unused register bits of the directly forwarded final groups. [Synthesis comparison](results/linear_output_registers/synthesis_comparison.json), [Yosys statistics](results/linear_output_registers/after.yosys.rpt), and [netlist audit](results/linear_output_registers/netlist_resource_audit.json) contain the evidence. Logic capacity is still exceeded by 5,099 sites; packing, placement/routing, and timing closure are not verified.
