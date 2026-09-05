# Sway

ECP5 selective-SSM accelerator research. The current deliverable is the [Observation 1 baseline](observation_1/README.md), written in Bluespec SystemVerilog.

The same project is mirrored at [blueyosys/projects/sway_observation_1](https://github.com/SeMinLim/blueyosys/tree/main/projects/sway_observation_1). All project source files must remain identical between these directories. Publishing rules are in [AGENTS.md](AGENTS.md).

## Build

With blueYosys checked out separately:

```sh
make -C observation_1 test ROOTDIR=/absolute/path/to/blueyosys
make -C observation_1 synth ROOTDIR=/absolute/path/to/blueyosys
```

Inside blueYosys:

```sh
make test PROJECT=sway_observation_1
make synth PROJECT=sway_observation_1
```

The baseline implements the pinned MambaLite-Micro KWS Mamba block with token-overlapped stages inspired by eMamba. It is not the original eMamba RTL or a full KWS application. The fixed-point implementation has a matched reference and self-checking Bluesim testbench. Task accuracy, board operation and Observation 1's architectural conclusions are separate validations, not implied by source publication.

CI compiles/tests and uploads logs only. It never commits or pushes generated files.
