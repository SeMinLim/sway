# Observation: baseline trace and sharing analysis

The [joint resource observation](resources.md) extends these multiplier and adder
measurements to selected requantization circuits in the same execution.

Step 4 measures the dedicated-engine baseline and tests whether compatible
compute resources can share hardware at its observed throughput. Baseline BSV,
RTL logic, checkpoint, numerical formats, lane counts, and pipeline are unchanged.

## Result

| Resource | Baseline | Within-stage sharing | Cross-stage sharing | Reclaimable |
| --- | ---: | ---: | ---: | ---: |
| Signed 18×18 multipliers | 45 | 45 | 25 | 20 (44.44%) |
| Affine INT24 accumulation adders | 17 | 17 | 13 | 4 (23.53%) |

Both schedules preserve **18,592 cycles/frame** in the same 56-frame execution.
The reductions require sharing across stages; within-stage sharing reclaims
neither resource. These are trace-based schedules. Shared mux/control area and
routed timing are unmeasured, so the counts do not establish net FPGA-area savings.

The multiplier result is a feasible allocation, not an optimality claim. For
adders, **13 operations occur together at cycle 27,498**. Thus 13 is the minimum
for these fixed operation cycles with one addition per adder per cycle, and the
verified allocation attains it while allowing one idle transition cycle.

## Measurement

* Divisor 4. The adder run uses baseline revision
  `a079d0d76e6ed850037c460c8d2a6359bbadae8c`; hardware source hashes match the
  original multiplier observation. Generated RTL differs only in its timestamp comment.
* BSC **2025.07** generates Verilog; Verilator **5.020** executes the same RTL
  with and without passive hierarchical observers.
* The existing 14 fixtures repeat four times: **56 frames, 3,192 scalar outputs**.
  No source bubbles or sink stalls are inserted; natural FIFO backpressure remains.
  All **3,305 kernel records** match in value and cycle between the control,
  multiplier-only, and combined observer runs.
* Statistics use cycles **[295,672, 816,248)**: 520,576 cycles across 28 interior
  output intervals. All 55 frame-start intervals are **18,592 cycles**. The run
  ends at cycle **1,060,049**, including a 2,048-cycle post-completion drain.
* **10,959,200 products** match signed 18×18 arithmetic and ordered retirement.
  The combined run's multiplier trace is byte-identical to the certified
  multiplier-only trace. Its earlier bounded Icarus comparison remains separate
  evidence for the multiplier-only run.
* **6,515,040 additions** and their post-edge captures match, including **305,368
  final row sums**. All **18,020,833** per-cycle `sumR` state checks pass. Each
  addition consumes the corresponding earlier multiplier result through the
  original registered `productQ` in order.

Stage boundary wait predicates remain in [stages.json](stages.json), with measured
[stage waits](evidence/stages.csv) and [multiplier utilization](evidence/units.csv).
Input and output waits are independent predicates, not additive idle categories.

## Adder inventory

The target is `sumR + signExtend(productQ)` in the 17 affine engines: embedding,
seven projections per block, head-hidden, and head-output. Each engine has one
live INT24 adder shared by `process3` and `process3Last`; these two source
expressions are not counted twice. Bias, convolution, normalization, scan, and
address/count adders are outside this measurement.

The [full mapped inventory](evidence/adders/inventory.json) identifies 17 disjoint,
live 24-bit carry chains in the original ABC9 netlist. Its input RTL matches the
current baseline apart from the compiler timestamp. A separate
[focused noabc9 mapping](evidence/adders/focused-mapping.json) preserves the same
17 exact arithmetic cones without `keep` attributes. The focused mapping is
supplementary evidence, not a whole-baseline final noabc9 netlist measurement.

## Scheduling constraints

Only identical arithmetic resources share a pool: signed **18×18 → 36**
multipliers or affine **INT24 + sign-extended INT16 → INT24** adders. Every
original operand, operation cycle, result-capture cycle, and ordering remains
fixed. All 17 stage-local `sumR` states, original FIFOs, and memory access times
are retained; no extra memory ports or state sharing are assumed.

A multiplier reservation spans the first accepted operation through the last
consumed result of a continuously outstanding burst. Its eight-entry capacity
is checked using the baseline's pre-edge guard. An adder reservation covers
consecutive operations of one original engine. The combinational addition and
capture use the same recorded clock edge: normal products update `sumR`; the
final product enters `sumQ` while `sumR` resets to zero.

Each reservation leaves **one complete idle cycle** before the physical unit is
reused, including reuse by the same owner. Independent checks validate all
**398,552 multiplier reservations** and **306,258 adder reservations**, including
startup, drain, and measurement-boundary crossings. The adder checker verifies
every original operation against its serialized assignment, retained state,
arithmetic result, and transition gap. Full-trace and interior-window adder
allocations both require 13 units.

The two resource pools use their original event times in the same execution.
Their jointly feasible schedules therefore preserve dependencies and the
measured output interval without rescheduling either operator class.

## Evidence and reproduction

* Multiplier [analysis](evidence/analysis.json), [run validation](evidence/validation.json),
  [kernel log](evidence/kernel.log), [replay equivalence](evidence/replay-equivalence.json),
  and [bounded Icarus comparison](evidence/icarus-prefix.json).
* Adder [analysis](evidence/adders/analysis.json), [independent certificate check](evidence/adders/verification.json),
  [run validation](evidence/adders/validation.json), [capture checks](evidence/adders/checks.json),
  [observed units](evidence/adders/units.json), and [audit](evidence/adders/audit.json).
* [Repository reproduction](evidence/adders/integration.json) confirms identical raw
  traces and allocation certificates using the integrated commands.
* [Multiplier archive manifest](evidence/archive.json) and
  [adder archive manifest](evidence/adders/archive.json) identify the complete
  raw traces, assignments, generated RTL, and reproduction sources. Large
  traces and build products remain outside Git.

From the repository root, with BSC, Verilator, a C++ compiler, and Python 3 on
`PATH`, choose an output directory that does not already exist:

```bash
python3 hw/reference/observation/run.py --adders --output /tmp/sway-observation --jobs 1
python3 hw/reference/observation/analyze.py \
  --transactions /tmp/sway-observation/transactions.csv \
  --units /tmp/sway-observation/units.json \
  --stages /tmp/sway-observation/stages.json \
  --waits /tmp/sway-observation/stage_waits.csv \
  --control /tmp/sway-observation/control.log \
  --observed /tmp/sway-observation/observed.log \
  --inputhex /tmp/sway-observation/snapshot/generated/test_input.hex \
  --expectedhex /tmp/sway-observation/snapshot/generated/test_expected.hex \
  --outputdir /tmp/sway-observation/multipliers
python3 hw/reference/observation/analyze_adders.py \
  --transactions /tmp/sway-observation/adder_transactions.csv \
  --units /tmp/sway-observation/adder_units.json \
  --multiplier-transactions /tmp/sway-observation/transactions.csv \
  --multiplier-units /tmp/sway-observation/units.json \
  --control /tmp/sway-observation/control.log \
  --observed /tmp/sway-observation/observed.log \
  --inputhex /tmp/sway-observation/snapshot/generated/test_input.hex \
  --expectedhex /tmp/sway-observation/snapshot/generated/test_expected.hex \
  --prior-analysis /tmp/sway-observation/multipliers/analysis.json \
  --outputdir /tmp/sway-observation/adders
python3 hw/reference/observation/verify_adders.py \
  --analysis /tmp/sway-observation/adders/analysis.json \
  --transactions /tmp/sway-observation/adder_transactions.csv \
  --units /tmp/sway-observation/adder_units.json \
  --output /tmp/sway-observation/adders/verification.json
```

`run.py` also accepts `--bsc /path/to/bsc` and `--verilator /path/to/verilator`;
omitting `--adders` reproduces the multiplier-only observation. Simulation
outputs and build products stay in the selected output directory.

To recheck the saved whole-netlist inventory, supply its mapped input RTL and
netlist plus the current baseline RTL. Yosys on `PATH` is needed only for the
supplementary arithmetic-only mapping:

```bash
python3 hw/reference/observation/inventory_adders.py \
  --rtl /path/to/current/mkTop.v \
  --mapped-rtl /path/to/mapped-input/mkTop.v \
  --netlist /path/to/mapped/mkTop.json \
  --output /tmp/adder-inventory.json
python3 hw/reference/observation/focused_map_adders.py \
  --rtl /path/to/current/mkTop.v \
  --inventory /tmp/adder-inventory.json \
  --outputdir /tmp/adder-focused-map
```
