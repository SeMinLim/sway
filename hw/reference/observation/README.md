# Observation: baseline trace and sharing analysis

Step 4 measures the existing dedicated-engine baseline and tests whether compatible
compute resources can be shared at its observed throughput. The baseline BSV,
RTL, checkpoint, numerical formats, lane counts, and pipeline are unchanged.

## Result

| Configuration | Signed 18×18 multipliers | Output interval (cycles/frame) |
| --- | ---: | ---: |
| Measured baseline | 45 | 18,592 |
| Stage-confined scheduling analysis | 45 | 18,592 |
| Cross-stage scheduling analysis | 25 | 18,592 |

The verified schedule reclaims **20 multipliers (44.44%)**. This is a feasible
schedule for the measured trace, not implemented shared RTL or an optimal-resource
claim. Mux/control/storage area and shared-design routed frequency are unmeasured;
the result does not establish net FPGA-area savings.

## Measurement

* Baseline revision: `2c41cc08571e77a4c180e1b16b854bc1c9b434d2`, divisor 4.
* BSC **2025.07** generates Verilog; Verilator **5.020** runs the same RTL with
  and without an external, read-only hierarchical observer.
* The existing 14 fixtures repeat four times: **56 frames, 3,192 scalar outputs**.
  The source inserts no bubbles and the sink inserts no stalls. Natural FIFO
  backpressure remains. Only a temporary test snapshot changes the frame count.
* All **3,305 kernel records** match between control and observed runs, including
  values and cycle timestamps. Golden outputs match; the run ends at cycle
  **1,060,049**, following a 2,048-cycle drain. A second complete run reproduces
  the transaction trace byte for byte.
* Statistics use cycles **[295,672, 816,248)**: 520,576 cycles across 28 interior
  output intervals. All 55 consecutive frame-start intervals are 18,592 cycles.
  Startup and drain do not contribute to the reported utilization or waits.
* **10,959,200 products** match signed 18×18 arithmetic, with independently checked
  per-unit operation counts and ordered retirement. Every observed get follows
  its put by four cycles. A bounded Icarus **12.0** check independently matches
  the first 62 kernel records, including all 57 outputs of the first frame.

The observer records accepted multiply cycles and two boundary predicates for
each of 25 stages. Input wait means the stage is not processing an input and its
required input FIFO data is unavailable. Output wait means an output is available
but is not consumed; for gating, it means the final result is blocked by the
downstream FIFO. Exact predicates are in [stages.json](stages.json).
These predicates are independent and are not additive categories of stage idle
time. For example, embedding output backpressure occupies **59.55%** of the
window, while head-hidden input starvation occupies **64.60%**. Complete stage
and multiplier measurements are in [stages.csv](evidence/stages.csv) and
[units.csv](evidence/units.csv).

## Scheduling constraints

Only signed **18×18 → 36** multipliers share resources. Accumulators and other
stage state remain local. Every original operand, request cycle, result cycle,
and ordering is retained, so dependencies and baseline SRAM-port demand remain
unchanged. No retiming, extra memory ports, or memory sharing is assumed.

A reservation spans the first accepted operation through the last consumed
result of a continuously outstanding burst. The eight-entry capacity is checked
using the baseline's pre-edge guard. An owner can change only after all products
are consumed, followed by **one complete idle cycle** for operand transition.
That gap is charged even if the next reservation has the same owner.

All **398,552 reservations**, including startup, drain, and measurement-boundary
crossings, fit 25 shared units. A separate checker verifies every serialized
assignment, original interval, and transition gap. Applying the identical policy
separately within each stage still requires 45 units: all 20 reclaimed units in
this analysis come from permitting cross-stage sharing. No timing improvement or
general behavior beyond this complete trace is claimed.

## Evidence and reproduction

* [Analysis](evidence/analysis.json): arithmetic, allocation, certificate checks,
  measurement window, and input/output SHA256 hashes.
* [Run validation](evidence/validation.json): commands, tool versions, baseline
  source hashes, measurement source hashes, and compiled-input hashes.
* [Kernel log](evidence/kernel.log), [replay equivalence](evidence/replay-equivalence.json),
  and [bounded Icarus comparison](evidence/icarus-prefix.json).
* [Archive manifest](evidence/archive.json): identity and checksum of the complete
  raw trace, assignment certificate, generated RTL, and reproduction sources.
  The archive accompanies the run report; large traces are kept outside Git.

From the repository root, with BSC, Verilator, a C++ compiler, and Python 3 on
`PATH`, choose an output directory that does not already exist:

```bash
python3 hw/reference/observation/run.py --output /tmp/sway-observation --jobs 4
python3 hw/reference/observation/analyze.py \
  --transactions /tmp/sway-observation/transactions.csv \
  --units /tmp/sway-observation/units.json \
  --stages /tmp/sway-observation/stages.json \
  --waits /tmp/sway-observation/stage_waits.csv \
  --control /tmp/sway-observation/control.log \
  --observed /tmp/sway-observation/observed.log \
  --inputhex /tmp/sway-observation/snapshot/generated/test_input.hex \
  --expectedhex /tmp/sway-observation/snapshot/generated/test_expected.hex \
  --outputdir /tmp/sway-observation/analysis
python3 -m unittest discover -s hw/reference/observation -p 'test_*.py'
```

`run.py` also accepts `--bsc /path/to/bsc` and `--verilator /path/to/verilator`.
Simulation outputs and build products stay in the selected output directory.
