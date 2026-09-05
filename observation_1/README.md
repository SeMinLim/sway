# Sway Observation 1 baseline

One Bluespec accelerator implements the MambaLite-Micro **Mamba block**, not the complete KWS application. Source: commit `44d51fce0f17ddadb6111c5e5554d1f7f6c67aff`, `examples/mambakws-any-10`. Batch 1, 100 tokens, dimension 64, inner dimension 128, state dimension 16, causal convolution width 4, delta rank 4.

This is an **eMamba-inspired reimplementation** of token-overlapped staged execution (Section 4.4), not the authors' RTL, quantization, or approximation-aware training. No architectural bottleneck or task-accuracy conclusion is claimed here.

## Run with blueYosys

Prerequisites: Bluespec/Bluesim, Yosys, nextpnr-ecp5, ecppack, GNU Make/G++, Python 3 and NumPy. The pinned build reference is blueYosys `15836908675784f36a7614085d418da6f77be65f`.

```sh
cd blueyosys
make test PROJECT=sway_observation_1 DATA_MODE=fixture
make test PROJECT=sway_observation_1
make pnr PROJECT=sway_observation_1
make synth PROJECT=sway_observation_1
```

The identical project in `sway/observation_1` runs with:

```sh
make -C observation_1 test ROOTDIR=/absolute/path/to/blueyosys
make -C observation_1 synth ROOTDIR=/absolute/path/to/blueyosys
```

The Makefile includes blueYosys `build.mk` for every HDL build stage. Generated files stay in ignored `build/`, `bsim/`, and `data/`. Default data preparation downloads and verifies the pinned public weights and example input. Network failure is an error, never a silent synthetic fallback. For offline use, place the verified `mamba_weights.h` and `sample_input.h` in `data/upstream/`. `DATA_MODE=fixture` is explicitly synthetic implementation-test data. Bluesim uses host C++ `-O0` to bound compiler memory; this has no effect on RTL cycles or hardware synthesis.

## Datapath

```text
64 -> input projection (4 lanes)
   -> causal depthwise convolution + x/z SiLU
   -> parameter projection (4 lanes, delta-rank/B/C)
   -> delta projection (4 lanes) -> softplus
   -> diagonal selective scan (pipelined, one mode per cycle)
   -> C reduction + D skip + z gate
   -> output projection (4 lanes) -> 64
```

Engines have separate controllers and SRAM and overlap different tokens. FIFO contents retain the active input token until completion; no redundant copy of that input is required. Metadata FIFOs align x/z, B/C and delta. Backpressure is propagated. `first=1` clears the sequence history logically in convolution and scan, without resetting the device. Fixed synthesis boundaries keep compiler memory bounded without changing the datapaths.

## Numerical contract

Storage values and weights are signed16; linear accumulators are signed48. The number of fractional bits is **per tensor**, listed in `config/model.json`, not one uniform Q format. In particular, the interface uses 8 fractional bits on input and 7 on output; recurrent state uses 12; linear weights use 13. Every reduction shifts toward negative infinity and saturates signed16. SiLU, gate SiLU, softplus, and decay use independent 4096-entry SRAM tables. Decay has nearest 1/256 resolution on [-16,0]; the other lookup grids are specified in the config. Static `A=-exp(A_log)` is exported offline. Softplus output saturates below 1 in this initial profile.

This is **not FP32-equivalent or accuracy-qualified**. `data/manifest.json` reports numerical error against a floating-point reference and state saturation counts. HDL tests use the identical fixed-point reference. Format selection and one public example do not establish KWS test-set accuracy. The MFCC frontend, application-level 40-to-64 projection, pooling and classifier are outside the kernel. No normalization/residual wrapper exists inside the pinned `Mamba_Forward` boundary, so none is silently added.

## Tests and instrumentation

`test` compares all 12,800 values in two consecutive 100-token sequences. It checks reset, tags, source bubbles, prolonged sink backpressure, concurrent active stages, and a watchdog. Missing PASS is failure. Testbench cycle counting is separated from traffic and watchdog rules to avoid scheduling dependencies.

`SWAY_TOKEN` records output cycles. `SWAY_STAGE` reports cycles, busy cycles, completed multiplication count, idle-with-empty-input cycles and output-queue-full occupancy. Occupancy is not asserted to be a producer stall. Overlapping stage cycles must not be summed as total latency. Stress-test cycles are not a no-stall throughput headline. Unused profiling methods may be removed by synthesis in the board wrapper.

The UART wrapper reuses blueYosys ULX3S clocks/UART/constraints. Packets are 131 bytes: flags (bit0=first), 16-bit little-endian tag, and 64 signed16 little-endian values. It is only debug transport, not a CRC-protected or flow-controlled production protocol. It does not define kernel throughput.

Simulation, synthesis, routed timing, physical-board operation, and task accuracy are separate checks. Successful commands and saved logs, not this README, establish each build result.

## Sources

- MambaLite-Micro: https://arxiv.org/abs/2509.05488
- Model source: https://github.com/Whiten-Rock/MambaLite-Micro/tree/44d51fce0f17ddadb6111c5e5554d1f7f6c67aff
- eMamba: https://arxiv.org/abs/2508.10370
- blueYosys: https://github.com/SeMinLim/blueyosys

Downloaded upstream model headers retain their original MIT license and copyright. This hardware independently implements the model equations.
