# Affine LUT-ROM verification

The affine datapath uses private combinational LUT-ROMs in all 17 engines. `SwayLinear.bsv` calls `linearWeight()` directly and enqueues the input, weight, and final-column flag together. The synchronous BRAM request/response path has been removed. The coefficient lookup adds zero read-latency cycles; the existing downstream arithmetic stages remain registered.

The original weight truth planes, biases, scales, nonlinear tables, integer arithmetic, and engine ownership are unchanged. Source comparison against `ca7f3d370063af269caf8447d3a797783e79802d` is recorded in [source-audit.json](source-audit.json). The existing two scan-state block RAMs remain; affine weights use no block RAM.

## Physical result

The physical target is ULX3S-85F, ECP5 CABGA381, speed grade 6, seed 1, router1, and `ParallelismDivisor=4`. Core and UART constraints remain 100 MHz and 25 MHz, respectively. Only the routed result establishes timing closure.

| Clock | Required | Routed Fmax | Result |
| --- | ---: | ---: | --- |
| CORE | 100 MHz | 100.331093 MHz | PASS |
| UART | 25 MHz | 114.639465 MHz | PASS |

| Resource | Used |
| --- | ---: |
| TRELLIS_FF | 57,540 |
| TRELLIS_COMB | 46,736 |
| MULT18X18D | 45 |
| DP16KD: affine weights | 0 |
| DP16KD: scan state | 2 |

See [physical-summary.json](physical-summary.json) and the [raw-evidence manifest](physical/manifest.json).

The build uses blueYosys `3663e87b88146c248919923ea952e025944ca3e0`, BSC 2026.01, Yosys 0.33, and nextpnr `1aea87ab`. The physical summary records binary hashes, the 52 frozen Sway/blueYosys source hashes, commands, constraints, and hashes of original and stored evidence.

The first placement process completed normally, but its output JSON was truncated after the complete cell inventory. Its failed driver report is retained. A successful follow-up process freshly packed the identical mapped netlist, replayed all 104,869 observed BELs, and independently proved the complete live graph and placement before routing. The check covers 123,513 bijectively matched wires, 485,302 connected pins, 205,925 disconnected pins, and all 11 external I/O pads. It restores the 100/25 MHz clock constraints, then routes in the same process, without exporting or reloading a large intermediate checkpoint. The process exits zero, reports both clocks passing, and produces a routed FPGA configuration. The [independent review](physical/independent-recovery-review.json) and original failure reports remain separate. No truncated checkpoint is accepted as a valid routed design.

These results are synthesis, placement, routing, and static timing verification. They do not describe a board execution test.

## Mapping and numerical checks

- The final mapped coefficient cones contain combinational LUT logic, no intermediate memory or register, and no input from another engine or unrelated control signal.
- All 8,192 addresses of each of 17 engines were evaluated against the frozen INT8 export: 139,264 addresses, including 14,340 valid coefficients and 124,924 zero-padding addresses, with zero mismatches. See [mapped-weight-values.json](mapped-weight-values.json).
- The coefficient evaluator's ECP5 primitive equations were checked against the pinned Yosys cell models over 61 cases and 19,312 input combinations. See [primitive-model-report.json](primitive-model-report.json).
- Divisors 1, 2, and 4 each pass continuous-input and source/sink-stall simulations: 14 frames and 798 outputs per configuration, followed by 2,048 drain cycles. The active-work reset test also passes a complete 14-frame restart. Recorded arithmetic coverage is 329,988 cases.
- Functional simulation evidence was reused after verifying exact identities of its 18 common source inputs, 19 warm-reset source inputs, and 44 recorded artifacts. It was not rerun during this physical build. See [functional-summary.json](functional-summary.json) and [functional-identity-audit.json](functional-identity-audit.json).

The physical build reduces head-hidden ROM address fan-out by copying 91 address flip-flops on the same clock edge and 513 combinational decoder cells. These copies add no coefficient pipeline stage. The decoder copies comprise 342 LUT4s and 171 PFUMXs, including 171 zero-valued LUT leaves required for packing. Independent reversal checks recover the original netlist exactly; decoder evaluation checks all 32 page values. [Four intentional invalid mutations](decoder-negative-audit.json) are rejected by the independent checker.

## Reproduction

With the pinned tools available and blueYosys checked out alongside Sway:

```sh
python3 hw/reference/check_physical.py --rootdir ../blueyosys --output /tmp/sway-physical
python3 hw/reference/check_refactor.py --backend iverilog --skip-arithmetic --output /tmp/sway-rtl
python3 hw/reference/check_warm_reset.py --output /tmp/sway-reset
```

The normal `make -C hw synth` build applies the same checked address/decoder transformations. No `.github/workflows` or `.gitignore` file is included in this revision.
