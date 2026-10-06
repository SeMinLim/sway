# Joint observation of arithmetic resources

The unchanged divisor-4 baseline was observed over 56 continuous frames. All
three resource allocations below coexist in that same execution at **18,592
cycles/frame**, with every operation, capture, and result-delivery cycle fixed.

| Resource | Synthesized baseline | Stage-confined sharing | Cross-stage sharing | Reclaimed |
| --- | ---: | ---: | ---: | ---: |
| Signed 18×18 multiplier | 45 | 45 | 25 | 20 (44.44%) |
| INT24 affine accumulation adder | 17 | 17 | 13 | 4 (23.53%) |
| Selected requantization functions | 23 | 23 | 21 | 2 (8.70%) |

All reductions require sharing across stages under the checked reservation
policy. Thus the observed opportunity extends to several kinds of arithmetic
resource. These are feasible trace-derived schedules, not implemented shared
hardware, minimum-resource proofs, or measured net FPGA-area savings. Added
interconnect/control/storage cost and shared-design routed frequency remain
unmeasured.

## Synthesis-backed inventory

The physical `mkTop` was compiled from baseline revision
`a079d0d76e6ed850037c460c8d2a6359bbadae8c` with BSC **2025.07**, then fully mapped
by OSS CAD Suite **2026-07-11**, Yosys **0.67+24 / 0e82bbefe**, using the normal
`synth_ecp5 -top mkTop -noabc9` flow. Inventory is taken before the existing
physical-netlist transformations; placement and routing were not repeated.
Common baseline source hashes, compiler version, divisor, and observed function
catalog are checked against the simulation.

The inventory follows mapped combinational logic from original register/FIFO
outputs to arithmetic results. It verifies nonconstant input bits, distinct
outputs and exclusive logic, and reports any overlapping cells. It uses no
isolated synthesis or added preservation attributes. All **17 adders and 23
requantizers** survive; none are constant, merged, or overlapping with another
selected cone. The same netlist contains the **45 native MULT18X18D** instances.

All 40 additional functions pass symbolic SAT equivalence over **1,307 input
bits** against signed addition or ties-to-even rounding with INT8 saturation.
Flipping one mapped carry-LUT bit causes the proof to fail. The 1,066 mapped
cells establish that the functions exist; their total is not an area-saving
estimate. Affine row-valid gating remains local to each stage when sharing the
arithmetic data function.

The additional scope is deliberately specific:

* Adders: the 17 affine engines' `sumR + product` accumulation. The ordinary and
  final-product rules use the same mapped adder in each engine. Bias adders and
  other additions are outside this count.
* Requantization: 17 affine `process4_2` functions (INT24), two convolution
  `process4_7` functions (INT18), and four scan `process2_3` functions (INT16).
  Exact input width, input/output scale exponents, rounding and saturation define
  12 compatibility classes. INT24 classes **−13→−6: 5→4** and **−12→−5: 7→6**
  account for the two reclaimed functions. Other classes retain their resources.

## Same-execution checks

The existing 14 fixtures repeat four times with no inserted source bubbles or
sink stalls. Control and externally observed generated-Verilog simulations
(Verilator **5.020**) match all **3,192 golden outputs** and all **3,305 kernel
records**, including cycle timestamps. Baseline hardware, parameters, and
parallelism are unchanged.

* **10,959,200 multiplies**, **6,515,040 additions**, and **1,523,928 requantizations**
  match independent integer arithmetic, with zero mismatches and exact per-unit
  operation counts.
* Every additional result is checked at its original destination: next-edge
  register readback or ordered scan-FIFO dequeue. All observed delivery checks
  occur one cycle after capture. Affine feedback, final-row reset, row boundaries,
  and requantization row/channel/part order also pass.
* Statistics use the same 28 interior frame intervals, cycles **[295,672, 816,248)**.
  Allocation checks include the entire trace, including startup and drain.

`sumR`, output registers, metadata, and FIFOs stay with their original stages.
Adder/requant operations capture their result locally on the issue edge;
consecutive operations of one owner form a burst. A **complete idle cycle**
separates bursts. Later local storage retention does not occupy these stateless
circuits. Multiplier reservations retain the original fully-drained policy.

The serialized certificates independently verify **1,758,518 additional-resource
reservations** and **398,552 multiplier reservations**. Compatible pools use
separate resources while retaining all original request, capture, and receipt
cycles. Dependencies and SRAM-port demand therefore remain unchanged, with no
retiming or added memory ports. The same cost model applied within stages retains
all 45/17/23 resources. All 32 checker tests pass.

## Evidence and reproduction

[Joint analysis](evidence/multi_resource/resource_analysis.json),
[per-unit measurements](evidence/multi_resource/resource_units.json),
[mapped inventory](evidence/multi_resource/resource_inventory.json),
[run validation](evidence/multi_resource/validation.json), and
[synthesis provenance](evidence/multi_resource/synthesis_provenance.json) contain
the counts, checks and SHA256 pins. The [archive manifest](evidence/multi_resource/archive.json)
identifies the prepared raw-evidence archive containing the complete traces,
certificates, mapped netlist and SAT proof. Archive attachment storage failed;
its availability status is recorded in that manifest. The earlier [multiplier and adder observation](README.md)
remains available.

From the repository root, use the pinned tools on `PATH` and the normal blueYosys
location (or supply `ROOTDIR` to Make). Choose a fresh absolute output directory:

```bash
SWAY_OBS=/tmp/sway-observation-resources
python3 hw/reference/observation/run_resources.py --output "$SWAY_OBS" --jobs 4 --extra-resources --no-pch
make -C hw runtime-rtl BUILD_DIR="$SWAY_OBS/synthesis"
(cd "$SWAY_OBS/synthesis" && yosys -p 'synth_ecp5 -top mkTop -noabc9 -json mkTop.json; stat' *.v)
python3 hw/reference/observation/inventory.py \
  --netlist "$SWAY_OBS/synthesis/mkTop.json" --rtl "$SWAY_OBS/synthesis/mkTop.v" \
  --units "$SWAY_OBS/extra_units.json" \
  --cells-sim "$(yosys-config --datdir)/ecp5/cells_sim.v" \
  --yosys "$(command -v yosys)" --bsc "$(command -v bsc)" \
  --output "$SWAY_OBS/inventory"
python3 hw/reference/observation/analyze_resources.py \
  --transactions "$SWAY_OBS/transactions.csv" --units "$SWAY_OBS/units.json" \
  --stages "$SWAY_OBS/stages.json" --waits "$SWAY_OBS/stage_waits.csv" \
  --control "$SWAY_OBS/control.log" --observed "$SWAY_OBS/observed.log" \
  --inputhex "$SWAY_OBS/snapshot/generated/test_input.hex" \
  --expectedhex "$SWAY_OBS/snapshot/generated/test_expected.hex" \
  --operations "$SWAY_OBS/extra_operations.csv" --extra-units "$SWAY_OBS/extra_units.json" \
  --inventory "$SWAY_OBS/inventory/resource_inventory.json" \
  --validation "$SWAY_OBS/validation.json" --outputdir "$SWAY_OBS/analysis"
python3 -m unittest discover -s hw/reference/observation -p 'test_*.py'
```

`--no-pch` only disables C++ precompiled headers for the simulator build; it does
not affect generated RTL. If `yosys-config` is unavailable, pass the selected OSS
CAD Suite's `share/yosys/ecp5/cells_sim.v` directly.

The joint runner is named `run_resources.py` to retain the existing `run.py --adders`
command. Its contents are byte-identical to the runner used for this measurement;
the archive also retains the original measured source paths and hashes.
[Integration checks](evidence/multi_resource/integration.json) verify this identity
and unchanged default observer outputs for the existing runner.
