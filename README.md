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
  * Linux with GNU Make, GCC/G++, and Python 3.
  * Bluespec Compiler **2025.07** with Bluesim.
  * OSS CAD Suite **2026-07-11**: Yosys **0.67+24**, nextpnr-ecp5, and ecppack.
  * Icarus Verilog for primitive-model tests.
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

### Software Evaluation

* Test RMSE: **8.1461 cm** for exact FP32, **8.1567 cm** for PWL FP32, and **9.3022 cm** for INT8 PTQ.
* RMSE is the mean of 57 coordinatewise RMSE values over 7,984 official MARS test frames.
* [Checkpoint](sw/results/mars/final/checkpoint.pt), [metrics](sw/results/mars/final/metrics.json), and [verification](sw/results/mars/verification.json).
* All 35 software tests pass. [Cleanup verification](sw/results/mars/cleanup_verification.json) confirms unchanged FP32, PWL, and INT8 inference on 264 frames, including the eight real hardware fixtures.
* [Training protocol](sw/results/mars/protocol.json) & [architecture checks](sw/results/mars/architecture_verification.json).
* INT8 retains the existing `Abar` scale of `2^-7`, with a maximum of `127/128`. Signed delta permits larger values. On the first 2,048 training frames, **39.89%** of PTQ `Abar` values require saturation; full counts and PWL range limits are recorded in the verification report.

### Hardware Verification

* Regression covers three lane settings, 14 frames / 798 coordinates, input/output stalls, continuous input, and reset/restart.
* All **139,264 coefficient addresses**, including zero padding, match the frozen INT8 coefficients.
* Integer-reference RMSE: **9.3015 cm** over 7,984 frames. [Integer reference](hw/generated/reference_report.json) · [Software comparison](hw/generated/software_contract_verification.json).

### Hardware Placement and Routing

* **PASS:** `make -C hw synth` completes placement, routing, and bitstream generation for ULX3S-85F (CABGA381, speed grade 6, divisor 4).
* Baseline architecture, precision, parallelism, and pipeline latency are unchanged. The design uses two scan-state DP16KD blocks and no affine block RAM.
* Build settings, reports, and bitstream: [validated CI run](https://github.com/SeMinLim/sway/actions/runs/36624329587).

### Hardware Timing

| Clock | Target (MHz) | Routed Fmax (MHz) | Result |
| --- | ---: | ---: | --- |
| Core | 100 | 100.321 | PASS |
| UART | 25 | 123.031 | PASS |

### Observation

The unchanged divisor-4 baseline passes **56 continuous frames / 3,192 outputs**
at **18,592 cycles/frame**. Trace-based sharing preserves all output values and cycles.

| Resource | Baseline | Within-stage sharing | Cross-stage sharing | Reclaimable |
| --- | ---: | ---: | ---: | ---: |
| Signed 18×18 multipliers | 45 | 45 | 25 | 20 (44.44%) |
| Affine INT24 accumulation adders | 17 | 17 | 13 | 4 (23.53%) |

Both schedules hold in the same execution, retaining stage-local `sumR` state,
FIFO contents, memory accesses, and operation/result cycles. Each reservation
leaves one full idle cycle before reuse. **6,515,040 additions** and their captured
results pass; 13 simultaneous additions establish the fixed-cycle adder minimum.
[Method, evidence, and reproduction](hw/reference/observation/README.md).
Shared hardware area and routed timing have not been measured.

## Notes

* Maintained by Se-Min Lim.
* The checkpoint is independently trained from the published eMamba settings. Model details and reconstruction choices are recorded in [model.json](sw/config/model.json); dataset membership is recorded in [data_manifest.json](sw/results/data_manifest.json).
* Hardware uses exact rational range normalization. Its comparison with software QDQ normalization is recorded in [numerical verification](hw/generated/software_contract_verification.json).
* Packed resource use and routed timing come from nextpnr. Synthesis counts and simulation cycle measurements alone do not establish placement/routing success or physical-board operation.
