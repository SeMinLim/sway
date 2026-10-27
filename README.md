# Sway

* A selective state-space model accelerator project for Lattice ECP5 FPGAs using Bluespec SystemVerilog (BSV).
* Includes a MARS pose-regression model, FP32 training, INT8 post-training quantization (PTQ), a dedicated-engine baseline for ULX3S-85F, and a parallel GAP8 software implementation.

## File structure

* `hw/`
  * Baseline hardware, UART interface, simulation, and FPGA build configuration.
  * `bsv/`: compute modules and parallelism settings.
  * `model/` & `generated/`: frozen checkpoint, parameter tables, and reference outputs.
  * `sim/` & `reference/`: testbenches, integer reference, and verification scripts.
* `sw/python/`: model implementation, training, quantization, and evaluation.
  * `config/`: model settings and reconstruction choices.
  * `results/mars/`: model checkpoint, INT8 exports, and training/evaluation records.
* `sw/c/`: exact integer inference for GAP8 and host correctness checks.
  * `sway.c`, `sway.h`, `sway_math.h`: inference kernels, workspaces, and integer arithmetic.
  * `generated/`: frozen parameters, derived lookup tables, fixtures, and SHA256 provenance.
  * `tools/`, `tests/`: SDK setup, export, numerical validation, and GVSoC execution.
  * `results/`: reproducible numerical checks and simulation measurements.

## Prerequisites & Dependencies

* blueYosys (Required for hardware builds)
  * Sway uses the shared build flow provided by [blueYosys](https://github.com/SeMinLim/blueyosys).
  * Keep blueYosys at the same level as Sway, or pass `ROOTDIR=/absolute/path/to/blueyosys` to Make.
* Environment Setup
  * Linux with GNU Make, GCC/G++, and Python 3.
  * Bluespec Compiler **2025.07** with Bluesim.
  * OSS CAD Suite **2026-07-11**: Yosys **0.67+24**, nextpnr-ecp5, and ecppack.
  * Icarus Verilog for primitive-model tests.
* Python Model Evaluation
  * Python 3.10 or newer, NumPy, and PyTorch.
  * Install the Python dependencies with `python -m pip install -r sw/python/requirements.txt`.
* C Inference and GAP8 Simulation
  * Host correctness checks require GCC with OpenMP and Python with NumPy.
  * GAP8 execution uses the pinned compiler, PULP OS, and GVSoC described in [SDK setup](sw/c/SDK_SETUP.md).

## How to build and run

Run commands from the Sway repository root unless a command changes the directory.

### Hardware

The default board is ULX3S-85F.

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

### Software correctness checks

```bash
make -C sw/c test
PYTHONPATH=sw/python python -m unittest discover -s sw/python -p 'test_*.py'
```

The host tests compare every output and recurrent state against `hw/reference/integer_model.py`, including real fixtures, all constant INT8 inputs, impulses, random inputs, extreme values, repeated-frame resets, and persistent worker teams. Host execution time is **not** used to estimate GAP8 performance.

### GAP8 GVSoC execution

Build the pinned SDK and run the virtual GAP8:

```bash
cd sw/c
bash tools/setup_gap_sdk.sh "$HOME/sway-gap8"
source "$HOME/sway-gap8/env.sh"
python tools/run_gvsoc.py
python tools/run_gvsoc.py --scalar
python tools/run_gvsoc.py --opt-level O3 --output results/gvsoc_simd_O3.json
```

[SDK setup and clock definitions](sw/c/SDK_SETUP.md) specify the compiler, simulator revision, host compatibility changes, and prerequisites. The default benchmark requests FC 250 MHz and cluster 170 MHz, corresponding to the manual's 1.2 V operating point. It records the programmed FLL rates separately. This is a **GVSoC timing simulation**, not a physical-board measurement or an energy measurement.

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

### Python model

* Model dimensions and reconstruction choices are defined in [model.json](sw/python/config/model.json).
* Convolution output enters the SSM path directly. SiLU remains on the gate branch.
* Delta path: `Linear -> ReLU -> Linear`, without an activation after the second Linear.
* `hw/` contains the frozen checkpoint and its generated INT8 parameters, nonlinear tables, and integer-reference outputs.

### GAP8 C inference

The C implementation runs the **same frozen integer model as the FPGA reference**, from an INT8 `8 × 8 × 5` input to 57 INT8 coordinates. It retains the checkpoint, scales, exact rational range normalization, ties-to-even rounding, INT24 current state, INT17 retained state, and per-frame reset. Input feature extraction and float-to-INT8 conversion are outside this comparison, as they are for the FPGA kernel.

* **SIMD:** four signed INT8 products per GAP8 dot-product instruction for dense projections.
* **Cooperative execution:** workers divide independent tokens and channels, with channel-local recurrence across the 16 tokens. Dependent stages synchronize explicitly.
* **Frame-parallel execution:** each worker runs a separate frame with a private workspace. This uses the model's existing independent frame resets and does not change the recurrence.
* **Exact arithmetic optimization:** composed exponential/gate tables, corrected reciprocal division for normalization, native saturation, and bounded ties-to-even shifts remove repeated work without approximating the reference. The exporter exhaustively checks the composed tables over their INT8 operand domains.
* **Memory:** the model and derived tables occupy **20,840 bytes**; each frame workspace occupies **3,800 bytes**. Eight workspaces plus the model use **51,240 bytes** of the 64 KiB cluster L1, leaving room for the successfully allocated worker stacks and runtime. Inputs and result buffers reside in L2. Timed execution includes their memory accesses and synchronization.

## Results

### Software Evaluation

* Test RMSE: **8.1461 cm** for exact FP32, **8.1567 cm** for PWL FP32, and **9.3022 cm** for INT8 PTQ.
* RMSE is the mean of 57 coordinatewise RMSE values over 7,984 official MARS test frames.
* [Checkpoint](sw/python/results/mars/final/checkpoint.pt), [metrics](sw/python/results/mars/final/metrics.json), and [verification](sw/python/results/mars/verification.json).
* All 35 software tests pass. [Cleanup verification](sw/python/results/mars/cleanup_verification.json) confirms unchanged FP32, PWL, and INT8 inference on 264 frames, including the eight real hardware fixtures.
* [Training protocol](sw/python/results/mars/protocol.json) & [architecture checks](sw/python/results/mars/architecture_verification.json).
* INT8 retains the existing `Abar` scale of `2^-7`, with a maximum of `127/128`. Signed delta permits larger values. On the first 2,048 training frames, **39.89%** of PTQ `Abar` values require saturation; full counts and PWL range limits are recorded in the verification report.

### C Inference Verification

All 1,118 validation inputs match the Python integer reference, including every checked intermediate and recurrent state, across serial, cooperative, and independent-frame execution. Persistent worker teams also pass at 1/2/4/8 cores. **1,465,939 arithmetic boundary cases**, AddressSanitizer, and UndefinedBehaviorSanitizer pass. All 14 frozen fixtures pass on the virtual GAP8 before each timed configuration.

[Host numerical validation](sw/c/results/host_validation.json).

### GAP8 GVSoC Throughput

Each configuration validates all 14 frozen fixtures before timing, then runs the eight real MARS fixtures repeatedly for 64 frames per trial, with three trials. The cluster counter measures elapsed cycles for the complete worker team; the FC counter additionally includes task dispatch. A separate 32,768 Hz reference timer checks virtual elapsed time. Initialization, correctness checks, and console output are outside the timed interval. Results include the full commands, raw logs, source hashes, compiler/SDK revisions, and median/minimum/maximum cycle counts.

The following throughputs use the **median FC elapsed-cycle count**, including cluster dispatch. Programmed clocks are **249,987,072 Hz FC** and **169,967,616 Hz cluster**. `Scalar affine` disables the four-way dot-product instruction while retaining the other exact optimizations.

| Cluster cores | Cooperative, SIMD (frames/s) | Independent frames, SIMD (frames/s) | Independent frames, scalar affine (frames/s) |
| --- | ---: | ---: | ---: |
| 1 | 239.0 | 239.0 | 160.7 |
| 2 | 477.2 | 477.9 | 321.3 |
| 4 | 949.4 | 953.6 | 640.5 |
| 8 | 1,863.9 | **1,894.9** | 1,260.7 |

* The fastest evaluated mapping is **eight independent frames with SIMD**, delivering **7.93×** the one-core SIMD throughput and **1.50×** the eight-core scalar-affine throughput. This is the best evaluated configuration, not a proof of a global optimum.
* At that setting, the median cluster interval is **89,696.7 cycles/frame**. The separate reference-clock timer reports approximately **1,896 frames/s**, consistent with its 30.52 µs batch-time quantization.
* GCC **`-O2`** is the default because it outperformed `-O3` in the evaluated target builds.
* The existing FPGA baseline's **18,592 cycles/frame at 100 MHz** corresponds to **5,378.7 frames/s**, or **2.84×** the best evaluated GAP8 throughput. The FPGA number is calculated from RTL steady-state output intervals and the validated clock target; the GAP8 number is a processor timing simulation. Both cover the frozen integer inference kernel. These results do not show GAP8 outperforming the FPGA baseline.

[SIMD measurements](sw/c/results/gvsoc_simd.json) · [Scalar-affine measurements](sw/c/results/gvsoc_scalar.json) · [Compiler comparison](sw/c/results/gvsoc_simd_O3.json) · [Frozen export provenance](sw/c/generated/provenance.json). Raw simulator logs accompany each measurement JSON in `sw/c/results/`.

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

## Notes

* Maintained by Se-Min Lim.
* The checkpoint is independently trained from the published eMamba settings. Model details and reconstruction choices are recorded in [model.json](sw/python/config/model.json); dataset membership is recorded in [data_manifest.json](sw/python/results/data_manifest.json).
* Hardware uses exact rational range normalization. Its comparison with software QDQ normalization is recorded in [numerical verification](hw/generated/software_contract_verification.json).
* Packed resource use and routed timing come from nextpnr. Synthesis counts and simulation cycle measurements alone do not establish placement/routing success or physical-board operation.
