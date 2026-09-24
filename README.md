# Sway

* A selective state-space model accelerator project for Lattice ECP5 FPGAs using Bluespec SystemVerilog (BSV).
* Includes a MARS pose-regression model, FP32 training, INT8 post-training quantization (PTQ), and a dedicated-engine baseline for ULX3S-85F.

## File structure

* hw/
  * Baseline hardware, UART interface, simulation, and FPGA build configuration.
  * `bsv/`: compute modules and parallelism settings.
  * `model/` & `generated/`: frozen checkpoint, parameter tables, and reference outputs.
  * `sim/` & `reference/`: testbenches, integer reference, and verification scripts.
  * `results/engine_refactor/`: validation results for the current baseline.
  * `results/lut_rom/`: weight ROM verification and synthesis comparison.
  * `results/linear_output_registers/`: output register verification and synthesis comparison.
* sw/
  * Model implementation, training, quantization, and evaluation scripts.
  * `config/`: model settings and reconstruction choices.
  * `results/mars_ptq_20260923/`: selected checkpoint, INT8 exports, metrics, and reproduction commands.

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
* The kernel has 17 affine engines, with no sharing across projections or blocks.
* Change `ParallelismDivisor` in [SwayTypes.bsv](hw/bsv/SwayTypes.bsv) to select parallelism:

| Divisor | Affine lanes per engine | Norm / Conv / Gate / Scan lanes |
| --- | ---: | ---: |
| 1 | 4 | 2 |
| 2 | 2 | 1 |
| 4 (Default) | 1 | 1 |

* Run `make -C hw clean` and rebuild after changing the divisor. Parameter tables do not need regeneration.
* See the [hardware README](hw/README.md) for interfaces, numerical formats, and verification commands.

## How to run the checkpoint

Run from the repository root after installing the Python dependencies:

```sh
python sw/prepare_data.py --data-dir ../data/mars --download
python sw/evaluate_ptq.py \
  --data ../data/mars \
  --checkpoint sw/results/mars_ptq_20260923/final/checkpoint.pt \
  --output sw/results/mars_ptq_recheck \
  --batch-size 256 --threads 2
```

* Use a fresh output directory. This evaluates the frozen checkpoint without fitting or checkpoint selection.
* Evaluation covers exact FP32, piecewise-linear FP32, and INT8 PTQ, and exports the INT8 tensors.
* [Training and PTQ commands](sw/results/mars_ptq_20260923/README.md#reproduce-training-and-ptq-selection) cover checkpoint reproduction.

## Results

* Software Evaluation
  * Test RMSE: **8.0681 cm** for exact FP32 and **8.3564 cm** for INT8 PTQ.
  * RMSE is the mean of 57 coordinatewise RMSE values over 7,984 official MARS test frames.
  * [Metrics](sw/results/mars_ptq_20260923/final/metrics.json) & [verification](sw/results/mars_ptq_20260923/verification.json).
* Hardware Verification
  * All three parallelism settings pass both the stall regression and continuous-input kernel test, with 798 matching outputs per run.
  * Default-configuration Verilog generation also passes.
  * [Simulation results](hw/results/linear_output_registers/validation.json) & [output/cycle comparison](hw/results/linear_output_registers/timing_comparison.json).
  * The updated weight LUT-ROM passes all 974,848 bank/address checks. Default-configuration stress and kernel tests pass with identical output values and cycles.
  * [ROM verification](hw/results/lut_rom/weight_rom_verification.json) & [regression results](hw/results/lut_rom/validation.json).
* Hardware Synthesis
  * blueYosys synthesis of `mkTop` for ULX3S-85F at divisor 4 reduces LUT4 use from **64,204 to 60,517** with per-element output registers and write enables.
  * Logic use before packing is **88,739 / 83,640 (106.10%)**, down from **92,426 (110.50%)**. The design still exceeds logic capacity.
  * FF: **47,751**, down from **47,887**; DSP: **78**; BRAM: **2**, unchanged. [Synthesis comparison](hw/results/linear_output_registers/synthesis_comparison.json).
  * Output arrays retain their element counts. Direct forwarding of the final group lets synthesis remove 136 unused register bits; all six regression runs preserve output values, cycles, and BSC schedules.

## Notes

* Maintained by Se-Min Lim.
* The checkpoint is independently trained from the published eMamba settings. Model details and reconstruction choices are recorded in [model.json](sw/config/model.json); dataset membership is recorded in [data_manifest.json](sw/results/data_manifest.json).
* Hardware uses exact rational range normalization. Its comparison with software QDQ normalization is recorded in [numerical verification](hw/generated/software_contract_verification.json).
* Logic use is calculated as `LUT4 + 2 * CCU2C + 6 * TRELLIS_DPR16X4`; it is not a packed `TRELLIS_COMB` measurement. Placement/routing, timing closure, and physical-board operation remain unverified for the current baseline. Earlier hardware reports describe the fused implementation.
