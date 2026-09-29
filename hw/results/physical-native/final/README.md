# Baseline physical closure

The default dedicated-engine baseline passes synthesis, placement, routing and bitstream generation for ULX3S-85F at a 100 MHz core and 25 MHz UART clock. The final physical checker is frozen at `7689b27d6816de9d8927de12ddee13672b23491b`. Functional and native-multiplier evidence comes from `80fbfe4cdb635606c394f435b28c1102c9de5829`; all RTL, model data, placement hooks, Makefile settings and clock constraints are identical between these commits. The only production build-source change corrects how the checker locates the reset FF in nextpnr JSON; the continuation script is retained with the evidence. The final main integration adds evidence and documentation and restores the complete validation workflow with a manual trigger.

## Result

| Check | Result |
| --- | --- |
| Core clock | 101.502228 MHz achieved / 100 MHz required |
| UART clock | 118.595825 MHz achieved / 25 MHz required |
| Placement / routing | PASS / PASS; both nextpnr commands returned zero |
| Configuration / bitstream | Nonempty configuration and ecppack bitstream; hashes independently verified |
| Functional RTL | Divisors 1, 2 and 4, stress and continuous kernel modes: 14 frames / 798 coordinates each |
| Native DSP wrapper | Three backends, 256 signed boundary/random products each, stalls and two resets; cycle-exact traces and four-cycle first-result latency |
| In-flight warm reset | Reset at cycle 40,000, held for five cycles; complete 14-frame / 798-coordinate restart passed |

Device: ECP5-85F, CABGA381, speed grade 6. Seed: 1. Router: router1. Physical scope is default divisor 4; divisors 1 and 2 have functional verification. No physical-board programming or operation was performed.

| Resource | Used | Available |
| --- | ---: | ---: |
| TRELLIS_IO | 11 | 365 |
| DCCA | 2 | 56 |
| DP16KD | 22 | 208 |
| MULT18X18D | 45 | 156 |
| ALU54B | 0 | 78 |
| EHXPLLL | 1 | 4 |
| EXTREFB | 0 | 2 |
| DCUA | 0 | 2 |
| PCSCLKDIV | 0 | 2 |
| IOLOGIC | 0 | 224 |
| SIOLOGIC | 0 | 141 |
| GSR | 0 | 1 |
| JTAGG | 0 | 1 |
| OSCG | 0 | 1 |
| SEDGA | 0 | 1 |
| DTR | 0 | 1 |
| USRMCLK | 0 | 1 |
| CLKDIVF | 0 | 4 |
| ECLKSYNCB | 0 | 10 |
| DLLDELD | 0 | 8 |
| DDRDLL | 0 | 4 |
| DQSBUFM | 0 | 14 |
| TRELLIS_ECLKBUF | 0 | 8 |
| ECLKBRIDGECS | 0 | 2 |
| DCSC | 0 | 2 |
| TRELLIS_FF | 57,790 | 83,640 |
| TRELLIS_COMB | 39,794 | 83,640 |
| TRELLIS_RAMW | 668 | 10,455 |

| Parallelism divisor | Stress finish cycle | Kernel finish cycle |
| --- | ---: | ---: |
| 1 | 130,800 | 115,503 |
| 2 | 231,599 | 223,357 |
| 4 | 305,868 | 297,627 |

Reported finish cycles include each test's 2,048-cycle trailing-output check; stress mode also includes deliberately injected source bubbles and sink stalls. They are simulation measurements, not board or UART performance measurements.

## Failure causes and corrections

1. **PFU reset grouping prevented placement.** ECP5 shares a reset signal across the eight FFs of a PFU. Separate identical reset-inverter nets fragmented otherwise compatible control groups. The build now proves eligible inversions, folds them into each FF's reset-polarity setting, and merges identical reset-only LUTs while preserving clocks, enables, reset mode/value and protected nets. A focused truth-table suite checks the transformation.
2. **DSP cascade pins could not route from fabric ground.** Unused cascade and secondary control ports are left unconnected; the active clock, enable, reset and arithmetic pipeline settings remain explicit.
3. **Long selection, ROM and arithmetic paths missed timing.** Dedicated native DSP pipelines, two-cycle banked weight ROMs, bounded selection stages and FIFO boundaries shorten these paths. Reserved result/operand slots and ordered metadata preserve values through stalls. The model, arithmetic formats, 17 dedicated affine engines and lane settings are unchanged.
4. **Backpressure crossed pipeline boundaries.** Registered readiness on the affine sum and product FIFOs cuts row-completion logic from downstream readiness and DSP/metadata control. The final product FIFO has two entries and supports simultaneous enqueue/dequeue without a combinational dequeue-to-enqueue-ready path.
5. **Bias ROM inference and an unnecessary reset added critical paths.** Equivalent bias truth planes preserve all table values while avoiding word-ROM address-register absorption. The output byte index is initialized before becoming active, so its redundant reset was removed; counter equivalence and full in-flight reset regression cover that change.
6. **DSP operand defaults and affine bank selection exposed further timing paths.** A no-input DSP cycle now has unspecified operand data and a zero valid bit, eliminating the control-dependent default-zero mux without changing valid results or the three-register DSP latency. A separate two-entry FIFO registers the chosen INT8 bank value and final-column flag before ROM metadata writes. ROM addresses and the four reserved operand slots still advance only when a ROM request is accepted.

7. **Core reset distribution remained too long.** A checked pre-placement hook constrains the existing reset-output FF to `X63/Y47/SLICEA.FF0`, near the center of the usable FF fabric. It changes only placement: reset logic, reset-release latency, clocks and all RTL remain unchanged from 439. The physical checker hashes the hook and verifies the selected FF and BEL in the actual placed checkpoint; the independent artifact audit repeats those checks.

The b21 candidate placed successfully but reached 99.068756 MHz; its 10.094 ns path crossed the lookahead product FIFO's readiness logic. The following 7b0 candidate removed that path but reached 95.923256 MHz, with scan put-readiness driving a default-zero DSP-input mux and a secondary 10.094 ns bank-selection-to-metadata-RAM path. The 439 candidate removed both data paths but reached 97.181732 MHz because the core reset-output FF was placed near one corner, with a 9.341 ns route to a reset sink near the opposite corner (10.290 ns including source and setup delay). Retained failure reports remain failures and are not acceptance evidence.

## Reproducible evidence

The functional/native run's original physical job remains a failure: synthesis and placement returned zero, but the checker expected a `mkTop` module while nextpnr wrote the placed module as `top`. That exception stopped the run before routing. The corrected checker finds exactly one reset FF across all modules and still requires its actual placed BEL to match the hook. The continuation verifies the original artifact digest, preserves its failed report and successful synthesis/placement commands, checks every mapped/placed netlist, generated RTL and placement-evidence hash, permits only the checker source change, and then routes the unchanged checkpoint with strict 100/25 MHz constraints. The independent audit downloads both artifacts and repeats these provenance checks; it does not reinterpret the failed run as a pass.

- [Reset placement constraint](../../../reference/place_core_reset.py) is included in the frozen physical source manifest; the placed checkpoint and independent audit record the selected FF and BEL.
- [Strict physical report](physical-report.json), [routed timing transcription](timing.json) and [full physical CI log](physical-ci-job.log).
- [Independent artifact audit](artifact-audit.json) and [the exact audit script](audit-artifacts.py): ZIP digests, frozen source hashes, generated RTL, netlists, constraints, routed configuration, bitstream and functional logs.
- [Functional and warm-reset evidence](../ci-80fbfe4/summary.json), including raw CI logs, reports and source-hash checks.
- [Fresh native multiplier evidence](../ci-80fbfe4/native-report.json) and an independent inspection of the generated DSP operand assignments.
- [Source/build integration audit](source-audit.json), [final source identity](integration-source-identity.json), and [final acceptance summary](summary.json).
- [Physical continuation run](https://github.com/SeMinLim/sway/actions/runs/36508244812), [functional/native run](https://github.com/SeMinLim/sway/actions/runs/36506484774), and [independent audit run](https://github.com/SeMinLim/sway/actions/runs/36508646970).
- [Complete physical build artifact](https://github.com/SeMinLim/sway/actions/runs/36508244812/artifacts/11007898824), including generated RTL, mapped/placed netlists, constraints, detailed timing, configuration and bitstream. CI artifact retention applies; the reports and hashes above are committed permanently.

The committed timing transcription preserves the logged routed clock results and critical paths with the original report hashes. Its JSON formatting is reconstructed; the byte-exact `nextpnr.json` and `timing_summary.json` are in the independently audited artifact.

Bitstream SHA-256: `6478081d9205a2f6b7f75b42e4e9a48c150d0ef5e55d8542cf75165412f0bc53` (1,978,721 bytes).

Run `.github/workflows/baseline-pnr-validation.yml` manually to repeat the full matrix and strict physical verification. Local commands and pinned dependencies are documented in [the hardware README](../../../README.md). Arithmetic/formal proofs and model evaluation reports retain their original provenance; the cited functional/native jobs freshly verify the changed RTL against frozen golden fixtures and do not retrain or recalibrate the model.

