# Observation 1: Selective-SSM Baseline

A Bluespec baseline for characterizing compact selective-SSM execution on ULX3S-85F. It implements the MambaLite-Micro keyword-spotting (KWS) Mamba block with eMamba-inspired token-overlapped stages. It is neither the original eMamba RTL nor a complete KWS application.

## Configuration

| Item | Setting |
| --- | --- |
| FPGA | ECP5-85F; 100 MHz target |
| Input | Batch 1; 100 tokens; 64 values per token |
| Model / inner dimension | 64 / 128 |
| State dimension / delta rank / convolution width | 16 / 4 / 4 |
| Arithmetic | Signed16 weights, activations, and state; signed48 linear accumulators |

Fractional bits vary by tensor; see `config/model.json`. Conversion uses arithmetic shifts and signed16 saturation. The initial softplus format saturates below 1 and is not FP32-equivalent.

## Datapath

```text
Input projection -> Causal convolution + SiLU
    -> Parameter projection -> Delta projection + Softplus
    -> Selective scan + C reduction + D skip + Gating
    -> Output projection
```

Projection engines use four lanes. Independent stages use SRAM and FIFOs to overlap different tokens while propagating backpressure. Set `first=1` on the first token of each sequence to reset convolution history and recurrent state. Audio preprocessing, pooling, and classification are outside the kernel.

## Build and run

Requirements: Bluespec/Bluesim, Yosys, nextpnr-ecp5, ecppack, GNU Make/G++, and Python 3 with NumPy. Use an updated [blueYosys](https://github.com/SeMinLim/blueyosys#how-to-build) checkout containing the ULX3S PLL correction.

From the blueYosys repository root:

```sh
make runsim PROJECT=sway_observation_1 BOARD=ulx3s-85f
make pnr PROJECT=sway_observation_1 BOARD=ulx3s-85f
make synth PROJECT=sway_observation_1 BOARD=ulx3s-85f
```

These are independent commands: `runsim` compiles and runs Bluesim, `pnr` builds through placement and routing, and `synth` builds through bitstream generation.

From the Sway repository, use `make -C observation_1 <command> ROOTDIR=/absolute/path/to/blueyosys BOARD=ulx3s-85f` with the same commands.

Data preparation downloads and verifies pinned model weights and example input. For offline use, place the verified `mamba_weights.h` and `sample_input.h` in `data/upstream/`. Add `DATA_MODE=fixture` to `runsim` for synthetic test data; download failures never silently select fixture mode.

## Files and results

| Location | Contents |
| --- | --- |
| `bsv/` | Accelerator stages and UART debug wrapper |
| `reference/prepare.py` | Weight, input, lookup-table, and reference-output generation |
| `sim/TbSway.bsv` | Self-checking Bluesim testbench |
| `system.log`, `output.log` | Simulation output and errors |
| `data/manifest.json` | Data provenance and numerical-error summary |
| `build/mkTop.utilization.rpt` | Resource and timing summary |
| `build/mkTop.nextpnr.log` | Full placement-and-routing log |

The testbench compares 12,800 values across two 100-token sequences and checks sequence reset, tags, and backpressure. Success requires `SWAY_PASS` and no `SWAY_FAIL`.

`SWAY_TOKEN` records output cycles; `SWAY_STAGE` reports stage counters. The testbench deliberately stalls traffic, so these cycles are not no-stall throughput measurements. Stage activity overlaps and must not be summed as total latency.

**Status:** Fixed-point agreement does not establish KWS accuracy. The 100 MHz timing target and physical-board operation remain unverified. UART is debug transport, not a kernel-throughput benchmark.

## Sources

- [MambaLite-Micro](https://arxiv.org/abs/2509.05488) and its pinned [model source](https://github.com/Whiten-Rock/MambaLite-Micro/tree/44d51fce0f17ddadb6111c5e5554d1f7f6c67aff), `examples/mambakws-any-10`.
- [eMamba](https://arxiv.org/abs/2508.10370), Section 4.4, for the token-overlapped execution principle.

Downloaded model headers retain their original MIT license and copyright.
