# Sway

* A selective state-space model accelerator project for Lattice ECP5 FPGAs using Bluespec SystemVerilog (BSV).
* Includes a MARS pose-regression model, FP32 training, INT8 post-training quantization (PTQ), and a dedicated-engine baseline for ULX3S-85F.

## File structure

* hw/
  * Baseline hardware, UART interface, simulation, and FPGA build configuration.
  * `bsv/`: compute modules and parallelism settings.
  * `model/` & `generated/`: frozen checkpoint, parameter tables, and reference outputs.
  * `sim/` & `reference/`: testbenches, integer reference, and verification scripts.
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
  * FPGA Tools: OSS CAD Suite **2026-07-11**, including Yosys **0.67+24 (`0e82bbefe`)**, nextpnr-ecp5 **0.10-82-g2b560ad0**, and ecppack **1.4-79-g56bb170**. The compatibility checks also retain the reviewed Yosys 0.33 primitive model.
  * The reproduced RTL uses BSC **2025.07 (282e82e9)**.
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
* Each affine lane reads its own fixed LUT-ROM combinationally through `linearWeight()`. Input, weight, and final-column metadata enter the operand FIFO together; the address advances only when that FIFO accepts them. There is no affine SRAM or synchronous ROM request/response. Affine alignment and bias addition remain registered before requantization.
* The default physical build copies selected head-hidden address registers and page decoders to reduce fan-out. Address copies update on the same edge as the original; coefficient truth tables, lookup latency, and engine ownership are unchanged. Independent netlist checks verify these transformations.
* Affine, normalization, convolution, gating, and scan products use native ECP5 DSPs with input, pipeline, and output registers. Eight reserved result slots per multiplier and ordered metadata queues retain products through backpressure.
* Convolution and gate operands shift through fixed lane positions; explicit issue flags and final-group comparisons bound each operation. FIFO boundaries separate selection, arithmetic, and collection. Numerical formats, checkpoint parameters, and engine parallelism are unchanged.
* Frame input, block `xR`/`gateR`/`gatedR`, normalization output, and scan output use per-element INT8 registers with explicit write-enables. The last input or result group feeds the next stage directly in the same cycle.
* `requantN` uses sign-extension bit checks for saturation and guard/sticky/retained-LSB checks for ties-to-even rounding, preserving the exact INT8 result without adding cycles.
* Change `ParallelismDivisor` in [SwayTypes.bsv](hw/bsv/SwayTypes.bsv) to select parallelism:

| Divisor | Affine lanes per engine | Norm / Conv / Gate / Scan lanes |
| --- | ---: | ---: |
| 1 | 4 | 2 |
| 2 | 2 | 1 |
| 4 (Default) | 1 | 1 |

* Run `make -C hw clean` and rebuild after changing the divisor. Parameter tables do not need regeneration.
* Physical validation targets divisor 4. The functional regression checks divisors 1, 2, and 4.

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
  * The regression checks all three lane settings against the same 14-frame / 798-coordinate fixtures, using both source/sink stalls and continuous kernel input. A separate test resets the active kernel and checks a complete restart.
  * Integer-reference RMSE: **9.3015 cm** over all 7,984 test frames. All 455,088 outputs match the software model when only normalization uses the baseline's exact-rational definition; original QDQ differs by at most 3 LSB.
  * [Integer reference](hw/generated/reference_report.json) & [software comparison](hw/generated/software_contract_verification.json).
* Hardware Placement and Routing
  * The physical flow selects `synth_ecp5 -noabc9`; Yosys 0.67 otherwise defaults to ABC9. This changes combinational technology mapping without changing BSV, RTL, weights, precision, engine parallelism, pipeline stages, or clocks.
  * The reported `physical-netlist` failure came from a checker that allowed only LUT/mux primitives between address FFs and coefficient outputs. Yosys also shares combinational `CCU2C` logic between the address incrementer and ROM decode. The revised auditor accepts the combinational primitive, keeps its complete input cone on the canonical address, and proves those cells remain identical after replication. Replica fan-out into control logic remains forbidden.
  * Page-decoder copy counts are derived independently from the actual mapped decoder closure. Whole-netlist reversal, same-cycle FF identity/startup checks, and exhaustive coefficient comparisons remain mandatory. No coefficient RAM or additional register stage is introduced.
  * `physical-netlist` checks all **139,264 addresses** (17 engines × 8,192 addresses), including zero padding, against frozen INT8 coefficients. It verifies the selected Yosys primitive model and its included files by SHA256. Set `YOSYS_CELLS_SIM=/absolute/path/to/ecp5/cells_sim.v` if the selected installation has no adjacent `yosys-config`.
  * nextpnr `2b560ad0` computes the HeAP placement retry limit by squaring a signed cell count; this overflows for this design. `--placer-heap-cell-placement-timeout 0` disables that retry guard. Placement legality and the strict **100 MHz core / 25 MHz UART** timing constraints remain enabled.
  * The supplied failing netlist passes the revised address/page audits and all **139,264 coefficient comparisons with zero mismatches**. The CCU2C evaluator also matches the official 0.67 model for **80,384 parameter/input cases**, with zero mismatches. These are functional/structural results.
  * Routed timing for this toolchain is still under verification. Passing `physical-netlist` alone does not establish timing closure or successful bitstream generation.
  * ECP5 shares one reset signal across eight PFU registers. The physical build folds proven reset inversions into FF reset-polarity settings and merges identical reset-only LUTs, preserving effective reset behavior, data, clocks, and enables.
  * A pre-placement hook places the existing core-reset output FF near the fabric center to shorten reset distribution. Reset logic and release latency are unchanged; both the physical checker and independent audit verify the actual placed BEL.

## Notes

* Maintained by Se-Min Lim.
* The checkpoint is independently trained from the published eMamba settings. Model details and reconstruction choices are recorded in [model.json](sw/config/model.json); dataset membership is recorded in [data_manifest.json](sw/results/data_manifest.json).
* Hardware uses exact rational range normalization. Its comparison with software QDQ normalization is recorded in [numerical verification](hw/generated/software_contract_verification.json).
* Packed resource use and routed timing come from nextpnr. Synthesis counts and simulation cycle measurements alone do not establish placement/routing success or physical-board operation.
