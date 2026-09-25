#!/usr/bin/env python3
"""Check serial convolution arithmetic/history while its result FIFO is blocked.

Instrumentation is inserted only into a temporary source snapshot. The production
FIFO, datapath and arithmetic remain unchanged; its drain rule is permitted to
run for eight cycles in every 64 cycles to force a full result FIFO.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import gzip
import json
import os
from pathlib import Path
import shutil
import tempfile

from check_refactor import ROOT, build, file_hash
from check_sim import check_log, read_hex
from integer_model import IntegerModel


def replace_once(source, original, replacement):
    if source.count(original) != 1:
        raise AssertionError("Convolution instrumentation anchor changed: " + original)
    return source.replace(original, replacement, 1)


def instrument(source):
    source = replace_once(source,
        "\tReg#(Bool) convolutionOn <- mkReg(False);",
        """\tReg#(Bool) convolutionOn <- mkReg(False);

\t// Simulation-only observation; no production source is modified.
\tReg#(Bit#(64)) probeCycleCnt <- mkReg(0);
\trule probeTick;
\t\tprobeCycleCnt <= probeCycleCnt + 1;
\tendrule

\trule probeState ( convolutionOn && convGroupCnt < fromInteger(valueOf(ConvGroups)) );
\t\t$display("CONV_STATE,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
\t\t\tblockId, probeCycleCnt, mainR.index, convGroupCnt, convTapCnt, convSumR[0],
\t\t\thistoryR[0][0][convGroupCnt], historyR[1][0][convGroupCnt], historyR[2][0][convGroupCnt]);
\tendrule""")
    source = replace_once(source,
        "\t\t\tsum[lane] = sum[lane] + signExtend(product);",
        """\t\t\tsum[lane] = sum[lane] + signExtend(product);
\t\t\t$display("CONV_TAP,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
\t\t\t\tblockId, probeCycleCnt, mainR.index, channel, convTapCnt,
\t\t\t\tsamples[convTapCnt], weights[convTapCnt], sum[lane]);""")
    source = replace_once(source,
        "\t\tconvolvedQ.enq(ConvPartial {group: convGroupCnt, sum: convSumR});",
        """\t\t$display("CONV_COMMIT,%0d,%0d,%0d,%0d,%0d",
\t\t\tblockId, probeCycleCnt, mainR.index, convGroupCnt, convSumR[0]);
\t\tconvolvedQ.enq(ConvPartial {group: convGroupCnt, sum: convSumR});""")
    source = replace_once(source,
        "\trule process4_4 ( convolutionOn );",
        "\trule process4_4 ( convolutionOn && probeCycleCnt[5:0] < 8 );")
    source = replace_once(source,
        "\t\tlet value = convolvedQ.first;",
        """\t\tlet value = convolvedQ.first;
\t\t$display("CONV_DRAIN,%0d,%0d,%0d,%0d,%0d",
\t\t\tblockId, probeCycleCnt, mainR.index, value.group, value.sum[0]);""")
    return source


def check_trace(path, frame_count):
    model = IntegerModel()
    events = [defaultdict(list), defaultdict(list)]
    for line in path.read_text().splitlines():
        if line.startswith("CONV_"):
            name, *fields = line.split(",")
            block, cycle, *values = map(int, fields)
            assert block in (0, 1)
            events[block][cycle].append((name, values))
    result = []
    for block, cycles in enumerate(events):
        history = [[0, 0, 0] for _ in range(40)]
        taps = []
        queue = deque()
        commits = drains = tap_count = blocked_cycles = 0
        wide_partial_sums = 0
        commit_waits = []
        last_tap_cycle = None
        previous_state = None
        previous_blocked = False
        for cycle, records in sorted(cycles.items()):
            state = [values for name, values in records if name == "CONV_STATE"]
            tap = [values for name, values in records if name == "CONV_TAP"]
            commit = [values for name, values in records if name == "CONV_COMMIT"]
            drain = [values for name, values in records if name == "CONV_DRAIN"]
            assert len(state) <= 1 and len(tap) <= 1 and len(commit) <= 1 and len(drain) <= 1
            blocked = False
            if state:
                token, channel, position, total, *actual_history = state[0]
                assert (token, channel) == ((commits // 40) % 16, commits % 40)
                assert actual_history == history[channel], (block, cycle, "history changed before commit")
                assert position == len(taps) and total == sum(sample * weight for sample, weight in taps)
                blocked = position == 4 and len(queue) == 2
                if blocked:
                    blocked_cycles += 1
                    assert not tap and not commit, (block, cycle, "full FIFO accepted a duplicate update")
                if previous_blocked:
                    assert state[0] == previous_state, (block, cycle, "state changed while waiting for FIFO space")
                previous_state = state[0]
            previous_blocked = blocked
            old_queue_size = len(queue)
            if drain:
                assert cycle % 64 < 8
                assert queue and tuple(drain[0]) == queue.popleft()
                drains += 1
            if tap:
                token, channel, position, sample, weight, total = tap[0]
                assert state and (token, channel, position) == tuple(state[0][:3])
                assert position == len(taps) < 4
                assert weight == int(model.parameters[f"blocks.{block}.convWeight"][channel, 0, position])
                assert -128 <= sample <= 127
                if position < 3:
                    assert sample == (0 if token == 0 else history[channel][position])
                taps.append((sample, weight))
                assert total == sum(x * w for x, w in taps)
                assert -(1 << 17) <= total < (1 << 17)
                wide_partial_sums += int(total < -128 or total > 127)
                last_tap_cycle = cycle
                tap_count += 1
            if commit:
                token, channel, total = commit[0]
                assert state and (token, channel) == tuple(state[0][:2])
                assert not tap and len(taps) == 4 and old_queue_size < 2
                assert total == sum(sample * weight for sample, weight in taps)
                queue.append((token, channel, total))
                history[channel] = ([0, 0] if token == 0 else history[channel][1:]) + [taps[3][0]]
                commit_waits.append(cycle - last_tap_cycle)
                taps = []
                commits += 1
        assert not taps and not queue
        assert commits == drains == frame_count * 16 * 40
        assert tap_count == commits * 4
        assert blocked_cycles > 0 and max(commit_waits) > 1
        result.append({"block": block, "tap_products_checked": tap_count,
                       "channel_sums_checked": commits, "history_commits_checked": commits,
                       "partial_sums_outside_int8_checked": wide_partial_sums,
                       "fifo_full_wait_cycles_checked": blocked_cycles,
                       "last_tap_to_commit_cycles": {"minimum": min(commit_waits), "maximum": max(commit_waits)}})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--backend", choices=("bluesim", "iverilog"), default="bluesim")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--ivl-dir")
    parser.add_argument("--output", type=Path, default=ROOT / "results/baseline/convolution")
    parser.add_argument("--trace-output", type=Path,
                        help="Optional gzip path for the complete simulation trace")
    args = parser.parse_args()
    bsc = shutil.which(args.bsc)
    if bsc is None:
        raise FileNotFoundError(args.bsc)
    bsc = str(Path(bsc).resolve())
    env = dict(os.environ)
    env["PATH"] = str(Path(bsc).parent) + os.pathsep + env["PATH"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_paths = [*ROOT.joinpath("bsv").glob("*.bsv"), *ROOT.joinpath("generated").glob("*.bsv"),
                    *ROOT.joinpath("sim").glob("*.bsv"), ROOT / "generated/test_input.hex",
                    ROOT / "generated/test_expected.hex", Path(__file__).resolve(),
                    ROOT / "reference/check_refactor.py", ROOT / "reference/check_sim.py"]
    result = {"status": "running", "compiler": bsc,
              "checkpoint_sha256": file_hash(ROOT / "model/checkpoint.pt"),
              "source_sha256": {str(path.relative_to(ROOT)): file_hash(path) for path in sorted(source_paths)}}
    summary = output / "result.json"
    summary.write_text(json.dumps(result, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="sway-convolution-") as temporary:
        snapshot = Path(temporary)
        for folder in ("bsv", "generated", "sim"):
            shutil.copytree(ROOT / folder, snapshot / folder)
        block = snapshot / "bsv/SwayBlock.bsv"
        block.write_text(instrument(block.read_text()))
        result["instrumented_block_sha256"] = file_hash(block)
        result["compiler_warnings"] = build(bsc, snapshot, "mkTbSway", output, env,
                                            args.backend, args.iverilog, args.vvp, args.ivl_dir)
        simulation = output / "simulation.log"
        result["network"] = check_log(simulation, snapshot / "generated/test_input.hex",
                                      snapshot / "generated/test_expected.hex", args.backend)
        frames = len(read_hex(snapshot / "generated/test_input.hex")) // 320
        result["blocks"] = check_trace(simulation, frames)
        result["scope"] = "Temporary-source observation with convolution FIFO drain allowed for 8 of every 64 cycles; production FIFO and datapath unchanged"
        result["arithmetic_oracle"] = "Python arbitrary precision products/sums; coefficients checked against immutable export; causal history tracked independently per channel"
        if args.trace_output is not None:
            args.trace_output.parent.mkdir(parents=True, exist_ok=True)
            with simulation.open("rb") as source, gzip.open(args.trace_output, "wb") as destination:
                shutil.copyfileobj(source, destination)
        result["trace_log_sha256"] = file_hash(simulation)
        result["trace_log_storage"] = "Full trace is optional; --trace-output retains it outside the result summary"
        simulation.unlink()
    assert all(file_hash(ROOT / path) == digest for path, digest in result["source_sha256"].items()), "Sources changed during validation"
    result["status"] = "pass"
    summary.write_text(json.dumps(result, indent=2) + "\n")
    print("SWAY_CONVOLUTION_PASS blocks=2 frames=" + str(frames), flush=True)


if __name__ == "__main__":
    main()
