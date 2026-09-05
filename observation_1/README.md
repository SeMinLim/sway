# Sway Observation 1 baseline

One Bluespec accelerator implements the **Mamba block**, not the complete KWS application. The model is pinned to MambaLite-Micro commit `44d51fce0f17ddadb6111c5e5554d1f7f6c67aff`, `examples/mambakws-any-10`: batch 1, 100 tokens, input/output dimension 64, inner dimension 128, state dimension 16, causal convolution width 4, delta rank 4.

This is an **eMamba-inspired reimplementation**, adopting its token-overlapped staged execution (Section 4.4), not a reproduction of the authors' RTL, quantization, approximation-aware training, or resource allocation. No new Sway optimization and no architectural-bottleneck conclusion are claimed by this baseline.

## Build in blueYosys

Install `bsc`, Bluesim, Yosys, `nextpnr-ecp5`, `ecppack`, GNU Make/G++, Python 3 and NumPy. The checked reference blueYosys revision is `15836908675784f36a7614085d418da6f77be65f`.

```sh
cd blueyosys
make -C projects/sway_observation_1 test DATA_MODE=fixture
make -C projects/sway_observation_1 test
make -C projects/sway_observation_1 pnr
make -C projects/sway_observation_1 synth
```

The identical project in the Sway repository works with:

```sh
make -C observation_1 test ROOTDIR=/absolute/path/to/blueyosys
make -C observation_1 synth ROOTDIR=/absolute/path/to/blueyosys
```

All HDL build stages include blueYosys `build.mk`; this project does not replace its synthesis or place-and-route flow. Generated files stay in `build/`, `bsim/`, and `data/`. By default, `reference/prepare.py` downloads and verifies the pinned public KWS weights and example input. Network failures are errors, not a silent switch to synthetic data. For offline use, copy the two verified headers to `data/upstream/` first. `DATA_MODE=fixture` explicitly selects deterministic synthetic implementation-test data.

## Datapath

```text
64 -> input projection (4 lanes) -> x/z split
   -> causal depthwise convolution + two SiLU lookup units
   -> parameter projection (4 lanes, delta-rank/B/C)
   -> delta projection (4 lanes) -> softplus lookup
   -> diagonal selective scan (pipelined, one mode per cycle)
   -> C reduction + D skip + z gate
   -> output projection (4 lanes) -> 64
```

Each engine has explicit FIFO interfaces, its own controller and SRAM, and can process a different token concurrently. Metadata FIFOs preserve the original x/z and B/C ordering. Backpressure propagates through the chain. `first=1` on a token starts a sequence: convolution history and recurrent state are treated as zero for that token and then overwritten. A new sequence does not require a device reset.

## Numerical contract and boundary

Weights, activations and state are signed 16-bit values with 10 fractional bits. Linear accumulators are signed 48-bit. Products are widened before multiplication; fixed-point reductions use arithmetic right shift (floor), then signed saturation. SiLU/softplus/exponential use 4096-entry SRAM lookup tables, step 1/64. Static `A=-exp(A_log)` is exported offline. Exponential input is non-positive by construction. Linear weights and bias columns are packed into four-row SRAM words.

This fixed-point implementation **is not FP32-equivalent and is not yet accuracy-qualified**. `data/manifest.json` reports its numerical difference from a floating-point reference, saturation counts and data hashes. HDL tests compare against the **same fixed-point reference**, not against a task label. A single example cannot establish KWS accuracy. The MFCC frontend, application-level 40-to-64 projection, temporal pooling, and classifier are outside the kernel. The pinned Mamba block itself contains no normalization or residual wrapper; none is silently added.

## Verification and profiling

`test` checks all 12,800 output elements for two consecutive 100-token sequences, including sequence reset, tag order, source bubbles, prolonged output backpressure, and concurrent active stages. It fails on any mismatch, timeout, missing PASS, or absent stage overlap. `SWAY_TOKEN` logs expose output cycles. `SWAY_STAGE` logs report stage cycles, busy cycles, completed multiplication count, idle-with-empty-input cycles and full output-queue occupancy cycles. Occupancy is **not** asserted to be a critical-path stall; stage cycle counts overlap and must not be summed as total latency. These stress-test cycles are not a headline no-stall throughput measurement.

The UART board wrapper reuses blueYosys' ULX3S PLL, UART and constraints. It is only a transport adapter; it is excluded from kernel-cycle timing. Each input/output packet is 131 bytes: flags (bit 0 = first), 16-bit little-endian tag, 64 signed 16-bit little-endian values. Continuous high-rate input must respect FIFO backpressure; this debug UART has no framing recovery or CRC and is not a production protocol.

Successful simulation, routed timing, bitstream creation, physical-board execution, and task accuracy are separate results. Only completed build logs establish a build PASS. No board-power or bottleneck result is embedded in this project.

## References

- MambaLite-Micro: https://arxiv.org/abs/2509.05488
- Model source: https://github.com/Whiten-Rock/MambaLite-Micro/tree/44d51fce0f17ddadb6111c5e5554d1f7f6c67aff
- eMamba, token-level pipelining: https://arxiv.org/abs/2508.10370
- blueYosys: https://github.com/SeMinLim/blueyosys

Upstream model headers are downloaded into ignored build-data storage; their original MIT license and copyright remain applicable. The implementation here independently expresses the block's equations.
