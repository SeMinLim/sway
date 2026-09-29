# Registered affine ROM pipeline evidence

The recorded functional and structural source snapshot is candidate `2cedd1d87bb21aa5f8ef3087aac85ccf7e746bda` for the two changed files, whose SHA-256 values are recorded in the reports. The tests here do not establish full-kernel correctness or routed timing.

- `focused-report.json`: 320-input, 20-output headHidden engine; four fixed tokens and 80 exact INT8 outputs; 25,600 requests; an uninterrupted 320-cycle issue run; 76 row restarts; 15,000-cycle initial sink stall and 1,024-cycle final drain. BSC reported no warnings or blocking rules.
- `wrapper-report.json`: comparison against the previous `BRAM1Load` behavior with 17 instances for 5,000 clock cycles. Cases include 6,400/3,200/1,600 words, partial final banks, widths 1/8/9/16, both pipeline settings, random enable stalls and writes, bank-boundary addresses, and binary initialization. The raw log's comparison count is a nominal constant, not a measured total.
- `synthesis-report.json`: the 6,400 × 8 ROM maps to four DP16KD cells. All 32 used EBR outputs connect directly to output FF data inputs; the bank mux follows those FFs. `synth.log.gz` and `rom.json.gz` preserve the raw Yosys log and synthesized netlist.
- `credit-report.json`: the testbench pauses the existing MAC-accept enable, observing four reserved ROM credits and 165 full-credit stall cycles. A five-cycle top reset occurs with four outstanding credits. After reset all 25,600 requests are captured and consumed exactly once, and the four fixed tokens still produce 80 exact outputs.

`commands.json` preserves the original focused compile/link/run commands. `main_warm.v` and `monitor_credit.v` replace the standard simulation main and passive monitor for the credit/reset replay; the production RTL is unchanged. The focused BSV fixtures use the unchanged generated parameters and coefficient files from the candidate repository.

`check_wrapper.py` generates deterministic initialization files and compares the wrapper with `BRAM1Load.before.v`. After the recorded successful run, its tool/path arguments were made portable and syntax-checked; that helper revision was not rerun because the local tool installation expired. Example invocation:

```sh
python3 check_wrapper.py --rtl /path/to/hw/rtl/BRAM1Load.v --output /tmp/sway-rom-wrapper
```

Optional `--iverilog`, `--vvp`, and `--ivl-dir` select tool paths. `run_focused.py` is the exact recorded local runner, not a portable project entry point. Source, fixture, and evidence hashes distinguish the recorded runs from later replay helper edits.

The subsequent production parser rejected the inline `for (genvar bank = ...)` declaration because it uses Verilog-2005. `syntax-compatibility.json` records the exact subsequent change: `genvar bank;` moves to module scope, with no other source changes. The old source hashes remain attached to the recorded tests; the subsequent production CI validates the corrected source.
