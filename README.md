# Sway

Compact selective state-space model accelerator research for ECP5-class FPGAs.

## MARS software

`sw/` implements, trains, evaluates, quantizes, and exports a MARS human-pose regression model reconstructed from the published [eMamba configuration](https://arxiv.org/html/2508.10370v1). It uses D=20, E=2, P=2, M=2, N=8, 16 spatial tokens, range normalization, and ReLU for the selective step size. Exact SiLU and exponential functions are used in training; piecewise approximations are evaluated separately.

This is our reimplementation, not an author checkpoint. The paper omits the regression head, several block details, training recipe, and approximation coefficients. Our choices are explicit in [model.json](sw/config/model.json). The implemented model has 15,717 trainable parameters, so it is not a parameter-exact recovery of the reported model.

The [official MARS arrays](https://github.com/SizheAn/MARS/tree/dc902822f864ca0d5df90d559d53d3cd919d3c7c/feature) contain 24,066 training, 8,033 validation, and 7,984 test frames. We preserve these memberships. Their approximately 60/20/20 split differs from eMamba's stated 64/16/20 proportions. Inputs are 8×8×5; outputs are 19 X coordinates, 19 Y coordinates, and 19 Z coordinates, in metres. Reported centimetre RMSE averages 57 coordinatewise RMSE values, following the original MARS evaluation code; pooled RMSE is also recorded.

## Run

Python 3.10 or newer and CPU or CUDA PyTorch are required. From the repository root:

```sh
python -m pip install -r sw/requirements.txt
python sw/prepare_data.py --data-dir ../data/mars --download
python sw/check.py
python sw/train.py --data ../data/mars --output sw/results/my_run --epochs 150 --batch-size 256 --threads 2
python sw/evaluate.py --data ../data/mars --checkpoint sw/results/my_run/best.pt --output sw/results/my_run/evaluation --quantize
```

Training uses MSE, AdamW (learning rate 0.001, weight decay 0.0001), gradient clipping at 1, validation-based learning-rate reduction, and early stopping after 25 epochs without improvement. These are our training settings. The default seed is 20260917. The test split is excluded from fitting, calibration, learning-rate decisions, and checkpoint selection. Resume within the same output directory using `--resume sw/results/my_run/last.pt` and a larger `--epochs` limit.

Quantization uses symmetric INT8 values and power-of-two scales calibrated on 2,048 training frames. Two clipping policies (99.9th percentile and maximum) are compared on validation data before final test evaluation. The affine/convolution reference uses integer accumulators; the SSM uses INT24 current state and INT17 retained state. Normalization, nonlinearities, and remaining elementwise operations use quantize/dequantize simulation. These results validate the software numerical model, not complete integer RTL equivalence.

## Outputs

- `best.pt`, `last.pt`: validation-selected and resumable FP32 checkpoints.
- `history.csv`, `run.json`: epoch measurements, configuration, dataset hashes, software versions, and runtime.
- `evaluation/metrics.json`: FP32, piecewise FP32, and post-training quantized test metrics.
- `evaluation/calibration.json`: train-only quantization scales.
- `evaluation/export/`: parameter arrays, hexadecimal and binary files, and layout/scale metadata for a later RTL implementation.
- [data_manifest.json](sw/results/data_manifest.json): pinned source revision, file hashes, dimensions, and split differences.

No FPGA latency, utilization, power, or board result is claimed by this software experiment.

## Measured run

[Run 20260917](sw/results/mars_seed20260917/run.json) trained for 150 epochs on CPU with two PyTorch threads (942.26 seconds). Validation selected epoch 148, with coordinatewise mean RMSE 8.9315 cm. The epoch limit was reached; convergence is not claimed. Training history and checkpoints are included.

The following results use all 7,984 official test frames and the same selected checkpoint. Quantization scales use 2,048 training frames; validation selected maximum-based calibration over the 99.9th-percentile alternative.

| Software numerical model | MAE (cm) | Mean coordinatewise RMSE (cm) |
|---|---:|---:|
| FP32, exact nonlinear functions | 6.6485 | 8.9582 |
| FP32, our piecewise nonlinear functions | 7.3938 | 9.6793 |
| INT8 PTQ, our piecewise nonlinear functions | 7.9799 | 10.4598 |

[Detailed metrics](sw/results/mars_seed20260917/evaluation/metrics.json) include axis errors, pooled RMSE, validation-only calibration selection, and state saturation counts. [Exports](sw/results/mars_seed20260917/evaluation/export/manifest.json) contain 15,717 INT8 parameter bytes, excluding scale metadata and state storage. [Numerical checks](sw/results/verification.json) and [artifact checks](sw/results/mars_seed20260917/evaluation/artifact_verification.json) passed.

The eMamba paper reports FP32/INT8 RMSE of 7.85/8.83 cm. Those are published reference values, not reproduced here. Our model details, split membership and PWL coefficients differ as described above, so this run does not establish a matched reproduction of that accuracy or a hardware comparison.

## MARS hardware baseline

`hw/` implements the complete fixed-weight MARS inference graph in Bluespec: patch embedding, two selective-SSM blocks, and the regression head. It retains separate layer engines and token pipelining through FIFOs. Affine engines have four output lanes; normalization, depthwise convolution, and gating use two lanes; the scan processes two states of one channel per cycle. Weights are fixed ROM logic; each block's recurrent state uses two 160×17 RAM banks with one asynchronous read and one synchronous write per bank per cycle. This is our ECP5-folded baseline, not the eMamba authors' RTL.

The kernel uses the existing trained INT8 weights and calibration scales. Normalization computes an exact rational result with ties-to-even rounding; SiLU and exponential ROMs enumerate the existing quantized piecewise functions. Each frame starts with zero convolution history and zero SSM state. Current state is signed INT24; its output is computed before arithmetic right-shifting by seven bits into signed INT17 storage.

Build with [blueyosys](https://github.com/SeMinLim/blueyosys/tree/3ea0afea56c7c73b8ed59ec0edd256c409466788), BSC/Bluesim and the ECP5 OSS CAD tools on `PATH`. The Makefile directly includes blueyosys `build.mk`; the default checkout location is a sibling of `sway`. Checked-in ROMs and test vectors allow simulation without downloading the dataset or running training.

```sh
make -C hw runsim BLUEYOSYS=/absolute/path/to/blueyosys
make -C hw runsim SIM_BACKEND=iverilog BLUEYOSYS=/absolute/path/to/blueyosys
make -C hw verilog BLUEYOSYS=/absolute/path/to/blueyosys
make -C hw netlist BLUEYOSYS=/absolute/path/to/blueyosys
make -C hw pnr BLUEYOSYS=/absolute/path/to/blueyosys
make -C hw synth BLUEYOSYS=/absolute/path/to/blueyosys
```

`mkSwayBaseline` accepts 320 signed INT8 values per frame in HWC order, scaled by 2^-2, and returns 57 signed INT8 coordinates, ordered X19/Y19/Z19 and scaled by 2^-5 metres. The ULX3S wrapper uses 115200-baud UART with raw input/output bytes; send one frame and read its complete reply before sending another. It synchronizes reset release in both clock domains and corrects the pinned blueyosys PLL reset polarity locally. UART transfer time is outside kernel measurements.

[The integer reference report](hw/generated/reference_report.json) records an RMSE of 10.4589 cm on all 7,984 test frames. Replacing only the software model's float32 normalization with this rational definition makes all 455,088 outputs match the integer reference. The original software QDQ graph is not bit-identical: its RMSE is 10.4598 cm, with at most three output LSBs difference. Hardware simulation results are separate from this full-dataset software evaluation.

[Bluesim](hw/results/bluesim.json) and [generated-Verilog/Icarus](hw/results/iverilog.json) each pass 14 frames and all 798 output comparisons against the integer reference. Fixtures include real data, zero and extreme inputs, and repeated frames; the test applies input bubbles and an 8,192-cycle output stall per frame. Its cycle totals include those deliberate stalls and are not kernel throughput measurements. The build uses BSC 2026.01 and Yosys 0.69+75; source hashes and FPGA build results are recorded in [validation.json](hw/results/validation.json).

ULX3S-85F synthesis uses 49,454 LUT4s, 50,535 FFs, 113 DSP multipliers, and four BRAMs. Legal placement completed, but the post-placement timing estimate was 18.40 MHz against the 100 MHz core constraint. Routing was stopped before completion; this is not routed Fmax or measured board performance. The baseline is not timing-closed for a 100 MHz bitstream. The [partial nextpnr log](hw/results/nextpnr_partial.log) records this limit; no board test was performed.

To regenerate checkpoint ROMs, test fixtures, and the numerical report using the software Python dependencies:

```sh
python hw/reference/generate.py --data ../data/mars
```
