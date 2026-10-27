# Sway

MARS pose-regression accelerator for Lattice ECP5, with a Bluespec hardware baseline and parallel GAP8 C inference.

## File structure

| Directory | Contents |
| --- | --- |
| [`hw/`](hw/) | BSV hardware, frozen model, simulation, and FPGA build |
| [`sw/python/`](sw/python/) | Model, training, INT8 quantization, and evaluation |
| [`sw/c/`](sw/c/) | GAP8 inference, correctness tests, and GVSoC results |

## Architecture

* **Model:** patch embedding → two Mamba blocks → regression head; INT8 `8 × 8 × 5` input → 57 coordinates. Independently trained from eMamba settings; [model configuration](sw/python/config/model.json).
* **FPGA:** 17 dedicated affine engines, LUT-ROM weights, and native ECP5 DSPs. Default: ULX3S-85F, 100 MHz, `ParallelismDivisor=4`. [Parallelism settings](hw/bsv/SwayTypes.bsv); clean and rebuild after changing them.
* **GAP8:** the same frozen integer kernel, with INT8 SIMD and up to eight cores. Supports cooperative token/channel execution and independent frames; preserves rounding, saturation, and per-frame state reset.

## Dependencies

* **Common:** Linux, GNU Make, GCC/G++ with OpenMP, Python 3.10+, NumPy, and PyTorch.
* **FPGA:** [blueYosys](https://github.com/SeMinLim/blueyosys), BSC **2025.07**, OSS CAD Suite **2026-07-11**, and Icarus Verilog. Keep blueYosys beside Sway or pass `ROOTDIR=/path/to/blueyosys` to Make.
* **GAP8:** pinned compiler, PULP OS, and GVSoC; see [SDK setup](sw/c/SDK_SETUP.md) for prerequisites and clock definitions.

## Build and run

From the repository root:

```bash
# FPGA simulation, Verilog, and bitstream
make -C hw runsim
make -C hw verilog
make -C hw synth

# Software dependencies and correctness checks
python -m pip install -r sw/python/requirements.txt
make -C sw/c test
PYTHONPATH=sw/python python -m unittest discover -s sw/python -p 'test_*.py'
```

GAP8 timing simulation:

```bash
cd sw/c
bash tools/setup_gap_sdk.sh "$HOME/sway-gap8"
source "$HOME/sway-gap8/env.sh"
python tools/run_gvsoc.py
```

## Results

### Accuracy

Mean of 57 coordinatewise RMSEs over **7,984 MARS test frames**:

| Implementation | RMSE (cm) |
| --- | ---: |
| FP32 | 8.1461 |
| PWL FP32 | 8.1567 |
| INT8 PTQ | 9.3022 |
| Frozen integer reference | 9.3015 |

[Software metrics](sw/python/results/mars/final/metrics.json) · [Integer reference](hw/generated/reference_report.json) · [Numerical comparison](hw/generated/software_contract_verification.json)

### Throughput

Both cover the frozen INT8 inference kernel, excluding feature extraction and input quantization.

| Implementation | Evaluation | Frames/s |
| --- | --- | ---: |
| ECP5 baseline | 18,592 RTL cycles/frame at validated 100 MHz | **5,378.7** |
| GAP8, 8-core SIMD, independent frames | GVSoC timing simulation | **1,894.9** |

GAP8 uses the best evaluated configuration: FC/cluster clocks requested at **250/170 MHz**, with throughput calculated from programmed rates. The result is the median of **three 64-frame trials**, repeating eight real MARS inputs, including dispatch, memory accesses, and synchronization. These are calculated/simulated kernel rates, not board measurements.

[GAP8 measurements and logs](sw/c/results/) · [Clock definitions](sw/c/SDK_SETUP.md)

### Verification

* **Software:** 35 Python tests pass; C outputs and checked states match the integer reference on **1,118 inputs**. Arithmetic boundary and sanitizer checks pass. [Validation report](sw/c/results/host_validation.json).
* **Hardware:** functional regression passes at divisors 1/2/4 on 14 fixtures. Placement, routing, and bitstream generation pass at divisor 4; routed Fmax is **100.321 MHz** for the core and **123.031 MHz** for UART. [Build reports](https://github.com/SeMinLim/sway/actions/runs/36624329587).
