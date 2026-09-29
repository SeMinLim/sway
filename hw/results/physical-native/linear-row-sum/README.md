# Focused affine row counter and sum FIFO check

This fixture checks the final-row `groupCnt` wrap and one-entry `sumQ`, retaining the prior `rowStartQ` and `selectQ` fixes in the 320-input, 20-output headHidden engine. Four fixed INT8 tokens produce 80 outputs checked against the frozen integer reference. The sink initially stalls for 15,000 cycles; a 1,024-cycle drain checks for trailing outputs.

The recorded run passed with zero compiler warnings and no blocked rules. The passive Verilog monitor observed 25,600 input issues, a 320-cycle uninterrupted issue run, and exactly 76 restart requests and consumes. This is a functional and scheduling check of one default-lane affine engine; it does not establish full-kernel correctness or routed timing.

`report.json` records the exact source and evidence hashes. The input and expected hex values were generated with `IntegerModel.linear(inputs, 9)` from the unchanged frozen export; this did not rerun the model's dataset evaluation. `GeneratedLinearVectors.bsv` embeds those fixed values to avoid a multiport testbench memory.

From the repository root, replay against sources matching the report:

```sh
python3 hw/results/physical-native/linear-row-sum/replay.py \
  --hw hw --output /tmp/sway-linear-row-sum
```

Optional `--bsc`, `--iverilog`, `--vvp`, and `--ivl-dir` select tool locations. The replay verifies source hashes before building an isolated snapshot and limits each child process to 6 GiB of address space. `test-commands.json` retains the original compile, link, and simulation invocation details; the new replay helper was syntax-checked but was not used for the recorded run.
