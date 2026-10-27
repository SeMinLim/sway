# GAP8 GVSoC setup

Run from `sw/c` on Linux x86-64:

```bash
bash tools/setup_gap_sdk.sh "$HOME/sway-gap8"
source "$HOME/sway-gap8/env.sh"
python tools/run_gvsoc.py
```

The installer requires `git`, `make`, `gcc`, `g++`, `rsync`, and zlib development files (`zlib1g-dev` on Ubuntu). It uses Python 3.10 or 3.11; set `PYTHON_BIN` if needed. If Python 3.10 is absent and `uv` is already installed, it downloads Python 3.10.22 into the destination. It creates an isolated Python environment and does not install system packages. Allow roughly 2 GB for sources, the prebuilt compiler, and build products. The installer finishes by compiling and running the official eight-core HelloWorld example.

## Pinned tools

| Component | Revision/version |
|---|---|
| [GAP SDK](https://github.com/GreenWaves-Technologies/gap_sdk) | `a23026507efe57410b98c5945544cff3150eb996` |
| [GAP RISC-V toolchain](https://github.com/GreenWaves-Technologies/gap_riscv_toolchain_ubuntu) | `e6b5fbbfbf57a0ad1092efb280d0417cf3ee7e51`, GCC 7.1.1 |
| Board configuration | `configs/gapuino_v3.sh`, GAP8 revision 3 |
| Runtime and simulator | PULP OS, `platform=gvsoc` |
| Tested host | Ubuntu 24.04, GCC 13, Python 3.10.22 |

Python 3.12 removes `imp`, which this SDK still imports. The installer adds `<stdint.h>` to two upstream DPI headers, `gap8/gvsoc/dpi-models/ext/nosv/svdpi.h` and `gap8/gvsoc/dpi-models/include/dpi/models.hpp`, so they compile with the tested host compiler. These are host header compatibility changes; no processor timing, memory latency, capacity, or instruction behavior is changed. The SDK is intentionally a working tree with these two modifications.

The focused build command is `make -f gap8/gap8.mk gvsoc -j8`. It avoids the top-level target that also builds unrelated tools such as OpenOCD. Optional camera/audio development packages are unnecessary for this inference benchmark. Use `JOBS=4` when installing on a host with less memory.

## Clock interpretation

The [GAP8 Hardware Reference Manual, version 1.5.5](https://gwt-website-files.s3.amazonaws.com/gap8_datasheet.pdf), page 327, Table 70, gives FC **250 MHz** and cluster **170 MHz** at **1.2 V**. These are the benchmark's requested performance-point clocks. GVSoC results are simulated timing, not physical-board or power measurements.

This PULP OS version reports the requested frequency through `pi_freq_get`. Its FLL programs multiples of the 32,768 Hz reference clock, so requests of 250 MHz and 170 MHz program 249,987,072 Hz and 169,967,616 Hz, respectively. Preserve the distinction between requested and programmed clocks when interpreting counters. Hardware execution additionally requires setting the appropriate supply voltage before raising clocks.
