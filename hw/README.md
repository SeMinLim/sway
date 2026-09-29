# MARS baseline for blueYosys

Dedicated-engine MARS inference for ULX3S-85F, using the frozen INT8 PTQ checkpoint in `model/` from `sw/results/mars/final/`.

## Architecture

Patch embedding feeds two independent Mamba blocks and the regression head through FIFOs. Each block contains separate main/gate input projections, normalization, convolution, gate SiLU, delta-input/B/C projections, delta expansion, scan, and output projection. The complete kernel has 17 affine engines; no engine is shared across projections or blocks.

Main projection feeds convolution; its quantized output enters the SSM path without SiLU. Gate projection feeds an independent SiLU stage, then waits in an ordered FIFO for the scan result. Delta-input, B, and C projections run in independent engines and rejoin before scan. Delta follows `Linear -> ReLU -> Linear`: ReLU applies before delta-input branch requantization, and delta expansion remains signed. Each projection uses its corresponding matrix rows and affine-then-branch requantization sequence.

The four nonlinear INT8 tables contain gate SiLU and exponential values for each block. Current recurrent state is INT24 and retained state is INT17. Affine accumulation uses INT24, convolution alignment INT18, recurrence alignment INT26, scan output accumulation INT35, and residual alignment INT10. All 61 static bounds pass for the frozen scales. The `Abar` scale is `2^-7`, saturating at `127/128`, including when signed delta produces a larger exponential.

`requantN` implements INT8 saturation with sign-extension bit checks and ties-to-even rounding with guard, sticky, and retained-LSB checks. A small increment handles rounding, with explicit saturation at +127 and handling for large shifts. The function is combinational and preserves the exact numerical result without changing pipeline cycles.

Each affine engine splits input selection into 16-column banks followed by a registered bank selection, limiting the largest second-stage selection to 20 inputs. The first selection FIFO has one registered entry and permits simultaneous replacement without empty bypass. A two-entry FIFO then registers the selected INT8 operand and final-column flag before the ROM request and metadata FIFO write; this separates the largest 20-way bank selection from the metadata RAM input. Each lane owns a two-cycle weight ROM initialized from `generated/linear_rom/`. It uses synchronous BRAM banks of at most 2,048 INT8 values, with a fabric output register for each bank before bank selection. A 13-bit address register advances only when a read is accepted and resets for each token. Each request reserves one of four operand FIFO entries; a metadata FIFO and two-cycle valid pipeline keep the input and final-column flag aligned with the weight. Captures continue during downstream stalls without overwriting an unconsumed result. Consecutive requests remain possible. The operand FIFO and two-entry product FIFO separate ROM capture, multiplication, and accumulation. The product FIFO uses registered readiness so accumulator stalls cannot propagate through DSP-result collection into metadata controls; it still permits one enqueue and one dequeue per cycle. A registered row-restart request separates final-product readiness from the next row's input counter and issue enable. Affine alignment and bias addition enter another FIFO before the single INT8 requantization.

Affine bias lookup uses eight constant 512-bit truth planes per biased layer, indexed by the existing nine-bit row. Every layer and row retains its original signed INT8 value, including zero padding. This represents the lookup as bit-selection logic and avoids inferring a word ROM that can absorb the sum FIFO's address registers.

The 119 generated banks cover all 1/2/4-lane configurations; the default uses 17 banks. All 43,260 stored INT8 values across the three configurations preserve the frozen matrix slices, including lane padding.

Affine, normalization, convolution, gating, and scan products use `SwayMultiply`, a signed 18-by-18-bit native ECP5 DSP wrapper with input, pipeline, and output registers enabled. Each multiplier reserves one of eight result slots before accepting operands, advances its three-stage DSP pipeline every cycle, and retains completed products until consumed. Ordered metadata queues keep products aligned through stalls. Bluesim and Verilog simulation use models of the same register sequence. Multipliers remain dedicated to their engines and lanes. On cycles without an accepted input, the operand wires are unspecified; only the three-stage valid pipeline permits result capture. This removes optional-write presence from the DSP operand path while preserving accepted values, latency, reset behavior and eight-slot capacity.

Normalization separates operand selection, multiplication, numerator alignment, and divider initialization. Convolution separates input selection, products, reduction, and requantization; gate lookup and multiplication also have explicit stages. Scan separates coefficient lookup, recurrence arithmetic, state/output products, and output reduction. Data registers without reset are initialized before their first valid read; FIFO validity and control state still reset.

Convolution and gate operand serializers read fixed lane positions and shift the remaining values after each accepted group. Convolution history rotates by the same group size, preserving channel order across tokens. Registered issue flags stop each bounded operation at its final group. Convolution, gate, and affine group counters wrap on the last accepted group, separating their updates from token-load control. The affine sum FIFO has one entry with registered readiness, cutting downstream backpressure from the row-completion controls; its group metadata retains the completed row's index before the counter wraps. These changes shorten operand selection and control paths without changing the UART interface or arithmetic sequence.

Each affine engine retains its output vector in per-element INT8 registers with explicit group write enables. The final group feeds the output FIFO directly in the same cycle; earlier groups come from their registers.

Patch formation first selects one of four patch columns for each patch row into a registered FIFO, retaining the token index with that snapshot. A second stage selects one of four patch rows before the embedding engine. This splits the original 16-way selection and preserves patch order when the next frame loads or the embedding engine stalls.

The kernel selects each UART result byte into a registered one-entry FIFO before enqueueing the depth-32 output FIFO. This separates the 57-way byte selector from the output FIFO's RAM and output-register input paths, while retaining one-byte-per-cycle transfers when the sink is ready.

The same storage pattern applies to the 320-element frame `inputR`, each block's 40-element `xR`, `gateR`, and `gatedR`, each normalization engine's 20-element `outputR`, and each scan engine's 40-element `outputR`. Only the selected element or lane group is written. The final input/result bypasses its register into the completed vector in the same cycle. FIFO readiness keeps the writes, counters, and transfer atomic under backpressure; the arithmetic and engine parallelism are unchanged.

## Parallelism

Change one typedef in `bsv/SwayTypes.bsv`:

```bsv
typedef 4 ParallelismDivisor;
```

| Divisor | Affine lanes per engine | Norm / Conv / Gate / Scan lanes |
| --- | ---: | ---: |
| 1 | 4 | 2 |
| 2 | 2 | 1 |
| 4 (default) | 1 | 1 |

Counters, bank selection, state addresses, and reductions follow the selected divisor. The checked-in parameter tables support all three settings without regeneration. Clean and rebuild after changing it.

## Interfaces

* Input: 320 signed INT8 values per frame in HWC order.
* Output: 57 signed INT8 coordinates in X19/Y19/Z19 order.
* Scales: `model/export/quantization.json`.
* UART: send one complete frame, then receive its 57-byte reply, at 115200 baud.
* Configured core clock: 100 MHz.

## How to build

Install BSC/Bluesim and [blueYosys](https://github.com/SeMinLim/blueyosys). From the Sway repository root:

| Task | Command |
| --- | --- |
| Run the regression | `make -C hw runsim ROOTDIR=/path/to/blueyosys` |
| Generate Verilog | `make -C hw verilog ROOTDIR=/path/to/blueyosys` |
| Synthesize the netlist | `make -C hw netlist ROOTDIR=/path/to/blueyosys` |
| Prepare the placement netlist | `make -C hw physical-netlist ROOTDIR=/path/to/blueyosys` |
| Place and route | `make -C hw pnr ROOTDIR=/path/to/blueyosys` |
| Generate the bitstream | `make -C hw synth ROOTDIR=/path/to/blueyosys` |

The FPGA flow requires Yosys, nextpnr-ecp5, and ecppack. To build inside blueYosys, copy this directory into `projects/sway_observation/` and select `PROJECT=sway_observation`. Keep `generated/linear_rom/` and `rtl/` with the project. The Makefile copies the ROM files and local block-RAM/DSP wrappers into the synthesis directory; simulations run from the project or a complete isolated snapshot.

The passing validation run used Ubuntu 24.04 and these pinned dependencies:

| Dependency | Validated version |
| --- | --- |
| Bluespec Compiler | BSC 2026.01, Ubuntu 24.04 release |
| Yosys | 0.33, Ubuntu package `0.33-5build2` |
| nextpnr-ecp5 | Commit `1aea87ab`, from OSS CAD Suite `2026-09-26` |
| ecppack | From the same OSS CAD Suite `2026-09-26` bundle |
| blueYosys | Commit `3663e87b88146c248919923ea952e025944ca3e0` |

The [validation workflow](../.github/workflows/baseline-pnr-validation.yml) records the release URLs and SHA-256 checks for BSC and OSS CAD Suite, checks the Yosys/nextpnr versions, and checks out the exact blueYosys commit. Run it manually for the full functional/native/physical matrix. For a local reproduction, check out that blueYosys commit and select the corresponding executables with the physical runner's `--bsc`, `--yosys`, and `--nextpnr` options.

## File structure

* `HwMain.bsv`, `Top.bsv`: UART adapter, clock crossing, and PLL wrapper.
* `bsv/`: compute modules and clock wrapper.
* `model/`: frozen checkpoint, INT8 export, and provenance.
* `generated/`: parameter tables, affine ROM initialization files, and fixed golden fixtures.
* `rtl/BRAM1Load.v`: BSC primitive with block-RAM inference and its original license.
* `bsv/SwayMultiply.bsv`, `rtl/sway_mult18x18d.v`: reserved-result multiplier interface, registered ECP5 DSP, and simulation models.
* `sim/TbSway.bsv`: regression with source bubbles, output stalls, repeated frames, and trailing-output checks.
* `sim/TbSwayKernel.bsv`: continuous-source, immediate-sink regression using the same golden outputs.
* `reference/`: integer reference, parameter generator, and test runners.
* `results/baseline/`: historical validation and synthesis records for the frozen checkpoint and activation order.
* `results/physical-native/final/`: physical closure report, routed timing, current regression cycle counts, bitstream digest, and independent artifact audit.

## Validation

Run all three lane configurations and both testbenches with:

```sh
python3 hw/reference/check_refactor.py --backend iverilog --output hw/results/baseline/recheck
python3 hw/reference/check_warm_reset.py --output hw/results/baseline/reset-recheck
```

The first runner builds isolated copies, checks every output against the fixed 14-frame / 798-coordinate fixtures, and tests rounding and saturation boundaries. It covers source bubbles, 8,192-cycle sink stalls, repeated frames, and a 2,048-cycle drain. The second resets an active kernel after 40,000 cycles, holds reset for five cycles, and checks all 14 frames again after restart. Both accept `--bsc`, `--iverilog`, `--vvp`, and `--ivl-dir` for tools installed outside the standard paths. Use a new output directory for each run.

The DSP wrapper and reset-net transformation have focused checks:

```sh
python3 hw/reference/native_multiply/check_native_multiply.py --hw-root hw --output /tmp/sway-multiply
python3 hw/reference/test_merge_reset_controls.py --report /tmp/sway-reset-fold.json
```

The multiplier check compares 256 signed boundary/random products, full result queues, and two in-flight resets across Bluesim and both Verilog build modes. It checks the fixed DSP configuration's simulation models and handshake; physical timing is checked separately below. The reset check verifies effective reset polarity and preservation of other FF controls, including mixed fanout and constrained nets.

Kernel cycle counts exclude intentional source/sink stalls and do not establish physical timing. The added pipeline boundaries change latency and frame intervals; measurements from the prior combinational weight-ROM implementation do not apply to this implementation.

The unchanged `requantN` has recorded coverage of **329,988** rounding/saturation tests. A BSC/Yosys [formal equivalence proof](results/baseline/equivalence/report.json) checks every signed input in **402 width/shift combinations** against the numerical reference function: widths 9, 10, 16, 18, 24, 26, 32, 35, 48, and 64; left shifts 0 through 8 and 1000; right shifts 1 through `n+1` and 1000 for width `n`. This covers negative ties-to-even, rounding at the saturation boundary, and large shifts for the listed combinations.

The retained combinational weight-reference functions have [974,848 checked addresses](results/baseline/weight_rom_verification.json) across 119 banks, including padding and out-of-range zeros. The new BRAM files contain the same values at every valid address. Hardware reads only the configured bank extent; it does not rely on an out-of-range BRAM value. The prior [standalone regeneration report](results/baseline/standalone_generation.json) records the earlier LUT-ROM artifact hashes.

Convolution weights and biases use direct bit-plane tables. The artifact update in `generated/reference_report.json` records all 640 channel lookups checked against the previous tables, including zero padding. Its ROM and source hash updates retain the historical frozen-model evaluation; they do not claim a new model evaluation run.

Normal builds use the checked-in tables and fixtures. Regeneration requires NumPy/PyTorch and `python3 reference/generate.py` from this directory; it performs no training or calibration. With the official dataset, run `python3 hw/reference/check_contract.py --data /path/to/mars` from the repository root to reproduce the software comparison.

The frozen checkpoint SHA-256 is `4fad39cc272077901ef86e24fd9fd3a51c2572417f0c4593c5edacf5c4e27428`. Its parameters and scales are unchanged from the software bundle. The [integer-reference report](generated/reference_report.json) records generation and width checks; [software contract verification](generated/software_contract_verification.json) covers all 7,984 official test frames and all 1,024 nonlinear table entries.

All 455,088 integer outputs match the software graph when only range normalization is replaced with the baseline's exact-rational definition. Against original float32 QDQ normalization, 435,831 outputs match exactly and the maximum difference is 3 LSB. Integer-reference coordinate-mean RMSE is **9.3015 cm**, versus **9.3022 cm** for software PTQ. This is reference evaluation; RTL regression uses the 14 fixed frames.

## Placement and routing

The default divisor-4 baseline at `7689b27d6816de9d8927de12ddee13672b23491b` passed full `mkTop` synthesis, placement, routing, and bitstream generation on ULX3S-85F. The routed core achieved **101.502228 MHz** against 100 MHz; UART achieved **118.595825 MHz** against 25 MHz. The unchanged RTL passed functional verification at divisors 1, 2, and 4, the native multiplier matrix, and in-flight warm reset in the preceding `80fbfe4cdb635606c394f435b28c1102c9de5829` run. Only the checker was then corrected to locate a unique reset FF across nextpnr module names, and routing resumed from the hash-verified, unchanged placed checkpoint. The original physical job remains recorded as a checker failure; final evidence records both runs and their source identity. Physical closure is established only for divisor 4; no physical-board programming or operation was performed. See [final evidence](results/physical-native/final/README.md) for source identity, resource use, fresh simulation cycle counts, bitstream SHA-256, and the independent audit.

ECP5's eight FFs in a PFU share a reset signal. Mapping generated many identical reset-inverter LUTs on distinct nets, which unnecessarily split the FF control groups and prevented legal placement. After synthesis, `reference/merge_reset_controls.py` proves eligible LUTs implement a single-input inversion, connects each affected FF reset port to the inverter's source, and toggles that FF's `LSRMUX` between `LSR` and `INV`. The effective reset signal is identical; reset mode, reset value, data, clock, and clock enable remain unchanged. An inverter is removed only when it has no remaining users or protected aliases. Other identical unconstrained LUTs are merged only when they exclusively drive FF reset ports. Data LUTs, DSPs, and dedicated wide-LUT structures are preserved. Both `make pnr` and the verification runner apply this transformation and record its changes in `reset_merge.json`.

The placement hook `reference/place_core_reset.py` constrains the existing core-reset output FF to the available compatible site nearest the FF fabric's center (`X63/Y47/SLICEA.FF0` in the validated run). This bounds its distribution distance after a corner-to-corner reset route exceeded the core period. The hook changes only the FF placement constraint; reset logic, reset-release latency, clocks, RTL and numerical behavior are unchanged. Both `make pnr` and the strict runner apply the same hook. The runner hashes the exact copied hook and verifies its selected cell/BEL against `NEXTPNR_BEL` in the placed checkpoint; the independent artifact audit repeats that check.

The native DSP wrapper leaves unused cascade inputs and unused clock/reset/enable ports unconnected. In particular, tying `SRIA`/`SRIB` to fabric ground creates unroutable connections to dedicated cascade pins. The active `CLK0`, `CE0`, and `RST0` connections and all multiplier pipeline parameters remain explicit.

Run full `mkTop` verification, including UART and PLL, from the repository root:

```sh
python3 hw/reference/check_physical.py \
  --rootdir /path/to/blueyosys \
  --output /tmp/sway-physical
```

The output directory must be new or empty. Optional `--bsc`, `--yosys`, and `--nextpnr` select tool executables. The runner uses ULX3S-85F, CABGA381, speed grade 6, seed 1, and router1. Placement writes a checkpoint; routing resumes that checkpoint with both the 100 MHz core and 25 MHz UART constraints explicitly restored. A pass requires successful routing, a nonempty configuration, both clocks meeting their targets, resources within capacity, and unchanged sources, netlists, and constraints. It writes `report.json`, `placement.json`, `nextpnr.json`, the copied reset-placement hook, synthesis/placement/routing logs, and the routed configuration. The CI workflow runs `ecppack` only after this strict physical pass, then records the bitstream SHA-256. Timing failures remain failures; no timing-allow-fail or force option is used.
