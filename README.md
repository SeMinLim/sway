# Sway

Selective state-space model (SSM) accelerator research for ECP5-class FPGAs, implemented in Bluespec SystemVerilog with [blueYosys](https://github.com/SeMinLim/blueyosys).

## Current implementation

[Observation 1](observation_1/README.md) contains one baseline accelerator, a fixed-point software reference, and a self-checking testbench. It implements the MambaLite-Micro keyword-spotting Mamba block using eMamba-inspired token-overlapped stages, not the complete keyword-spotting application.

The project is mirrored in [blueyosys/projects/sway_observation_1](https://github.com/SeMinLim/blueyosys/tree/main/projects/sway_observation_1).

## Build and run

Install the blueYosys tools and Python NumPy. From this repository, point `ROOTDIR` to your blueYosys checkout:

```sh
make -C observation_1 runsim ROOTDIR=/absolute/path/to/blueyosys BOARD=ulx3s-85f
make -C observation_1 pnr ROOTDIR=/absolute/path/to/blueyosys BOARD=ulx3s-85f
make -C observation_1 synth ROOTDIR=/absolute/path/to/blueyosys BOARD=ulx3s-85f
```

`runsim` checks the fixed-point implementation; `pnr` checks placement, routing, and timing; `synth` builds through bitstream generation. See the [project README](observation_1/README.md) for configuration, data preparation, and output files.

**Status:** 100 MHz timing closure, board operation, and keyword-spotting accuracy remain unverified. Development rules are in [AGENTS.md](AGENTS.md).
