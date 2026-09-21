#!/usr/bin/env python3
"""Validate Block0 rule-firing evidence without changing the baseline datapath."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re

from check_perf import INPUT_WORDS, STREAM_FRAMES, TRIM_FRAMES, hardware_sources, read_hex, sha256, source_manifest
from check_profile import CSV_FIELDS, TOKENS, check_profile, distribution

STAGES = ("norm_in", "norm_ready", "inproj_in", "inproj_out", "conv_start", "conv_out",
          "stateproj_in", "deltaproj_in", "scan_in", "gate_in", "outproj_in", "residual_in", "block_ready")
TOKEN_EVENTS = ("put", "capture", "start", "emit", "get")
DETAIL_EVENTS = ("group", "chunk", "read", "dispatch", "response", "multiply", "combine",
                 "accumulate", "last", "restart", "bias", "add", "round", "collect")
COUNT_NAMES = ("read", "dispatch", "response", "multiply", "combine", "accumulate", "last", "group", "chunk")
INPUTS = 20
GROUPS = 20
CHUNKS = 2
RESIDUAL_CAPACITY = 4  # SwayBlock.residualQ, allocated at norm_in and released at residual_in.
SAMPLE = 128
TOTAL = STREAM_FRAMES * TOKENS
SAMPLE_FIELDS = ("event", "ordinal", "token", "cycle", "group", "item")
ADDED_RULES = {"test_dut_block0_blockProfileTick", "test_dut_block0_normalization_blockProfileTick",
               "test_dut_block0_inputProjection_profileLinearPutCycle", "test_dut_block0_inputProjection_engine_profileLinearCycle"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_schedules(baseline: str, actual: str) -> dict:
    def parse(text: str) -> tuple[dict, list[str]]:
        require(text.count("Logical execution order:") == 1, "Missing or duplicate schedule order")
        rules_text, order_text = text.split("Logical execution order:")
        matches = list(re.finditer(r"^Rule: (\S+)\nPredicate: (.*?)\nBlocking rules: ([^\n]*)", rules_text, re.M | re.S))
        rules = {m[1]: tuple(" ".join(m[i].split()) for i in (2, 3)) for m in matches}
        require(len(rules) == len(matches) == len(re.findall(r"^Rule:", rules_text, re.M)) and bool(rules), "Malformed or duplicate schedule rules")
        order = [part.strip() for part in order_text.strip().split("\n\n", 1)[0].split(",")]
        require(len(order) == len(rules) and set(order) == set(rules), "Invalid schedule execution order")
        return rules, order

    original, original_order = parse(baseline)
    profiled, profiled_order = parse(actual)
    require(not (set(original) & ADDED_RULES) and set(profiled) == set(original) | ADDED_RULES,
            "Block profile schedule has unexpected added or removed rules")
    for name, detail in original.items():
        require(profiled[name] == detail, f"Instrumentation changed predicate or blockers: {name}")
    for name in ADDED_RULES:
        require(profiled[name] == ("True", "(none)"), f"Observer clock must be unconditional and unblocked: {name}")
    require([name for name in profiled_order if name not in ADDED_RULES] == original_order,
            "Instrumentation changed original logical execution order")
    return {"status": "pass", "original_rules_checked": len(original), "added_rules": sorted(ADDED_RULES),
            "original_predicates_and_blockers_equal": True, "original_logical_execution_order_equal": True}


def parse_records(text: str) -> tuple[str, dict, dict, dict, dict]:
    stages = {name: [] for name in STAGES}
    token_events = {name: [] for name in TOKEN_EVENTS}
    detail = {name: [] for name in DETAIL_EVENTS}
    counts = {name: [] for name in COUNT_NAMES}
    filtered = []
    begun = False
    finished = False
    previous_cycle = -1
    for line in text.splitlines():
        if "SWAY_BLOCK" not in line and "SWAY_LINEAR" not in line:
            filtered.append(line)
            if line.startswith("SWAY_PERF_BEGIN,"):
                begun = True
            if line.startswith("SWAY_PERF_PASS,"):
                finished = True
            continue
        require(begun and not finished, "Block record outside BEGIN/PASS")
        fields = line.split(",")
        tag = fields[0]
        if tag == "SWAY_BLOCK":
            require(len(fields) == 5 and fields[1] in stages and all(re.fullmatch(r"\d+", x) for x in fields[2:]),
                    f"Unknown or malformed block record: {line}")
            name = fields[1]
            frame, token, cycle = map(int, fields[2:])
            ordinal = len(stages[name])
            require(ordinal < TOTAL and (frame, token) == divmod(ordinal, TOKENS), f"Missing, duplicate, or reordered {name}")
            require(not stages[name] or cycle > stages[name][-1], f"Non-monotonic {name}")
            stages[name].append(cycle)
        elif tag == "SWAY_LINEAR_EVENT":
            require(len(fields) == 7 and fields[1] in TOKEN_EVENTS + DETAIL_EVENTS and
                    all(re.fullmatch(r"-?\d+", x) for x in fields[2:]), f"Unknown or malformed linear event: {line}")
            name = fields[1]
            ordinal, index, cycle, group, item = map(int, fields[2:])
            require(0 <= ordinal < TOTAL and index == ordinal % TOKENS and cycle >= 0, "Invalid linear ordinal, token, or cycle")
            if name in token_events:
                require(ordinal == len(token_events[name]) and group == item == -1, f"Missing, duplicate, or invalid {name} event")
                require(not token_events[name] or cycle > token_events[name][-1], f"Non-monotonic {name} event")
                token_events[name].append(cycle)
            else:
                require(ordinal == SAMPLE, "Unexpected detailed linear token")
                rows = detail[name]
                require(not rows or cycle > rows[-1]["cycle"], f"Non-monotonic {name} detail")
                rows.append(dict(zip(SAMPLE_FIELDS, (name, ordinal, index, cycle, group, item))))
        elif tag == "SWAY_LINEAR_COUNT":
            require(len(fields) == 5 and fields[1] in counts and all(re.fullmatch(r"\d+", x) for x in fields[2:]), f"Malformed linear counters: {line}")
            name = fields[1]
            ordinal, cycle, count = map(int, fields[2:])
            require(ordinal == len(counts[name]) and ordinal < TOTAL, "Missing, duplicate, or reordered linear counts")
            require(not counts[name] or cycle > counts[name][-1]["cycle"], "Non-monotonic linear count records")
            counts[name].append({"ordinal": ordinal, "cycle": cycle, "count": count})
        else:
            raise AssertionError(f"Unknown block instrumentation record: {line}")
        require(cycle >= previous_cycle, "Non-monotonic block instrumentation transcript")
        previous_cycle = cycle
    for name, rows in {**stages, **token_events}.items():
        require(len(rows) == TOTAL, f"Expected {TOTAL} {name} events, got {len(rows)}")
    require(all(len(rows) == TOTAL for rows in counts.values()), "Missing linear count records")
    return "\n".join(filtered), stages, token_events, detail, counts


def validate_detail(detail: dict, token_events: dict, group_overlap: bool = False) -> dict:
    wanted = {}
    for name in DETAIL_EVENTS:
        if name == "restart" and group_overlap:
            pairs = []
        elif name in ("read", "dispatch", "response", "multiply", "combine"):
            pairs = [(group, item) for group in range(GROUPS) for item in range(INPUTS)]
        elif name == "accumulate":
            pairs = [(group, item) for group in range(GROUPS) for item in range(INPUTS - 1)]
        elif name == "chunk":
            pairs = [(group, item) for group in range(GROUPS) for item in range(CHUNKS)]
        elif name == "last":
            pairs = [(group, INPUTS - 1) for group in range(GROUPS)]
        else:
            pairs = [(group, -1) for group in range(GROUPS)]
        require([(row["group"], row["item"]) for row in detail[name]] == pairs,
                f"Missing, duplicate, or incorrectly labeled {name} firing")
        wanted[name] = {(row["group"], row["item"]): row["cycle"] for row in detail[name]}

    pipeline = ("read", "dispatch", "response", "multiply", "combine")
    latencies = {f"{a}_to_{b}": [] for a, b in zip(pipeline, pipeline[1:])}
    latencies["combine_to_accumulate"] = []
    for group in range(GROUPS):
        for item in range(INPUTS):
            key = (group, item)
            cycles = [wanted[name][key] for name in pipeline]
            final = wanted["last" if item == INPUTS - 1 else "accumulate"][key]
            for a, b, start, end in zip(pipeline, pipeline[1:], cycles, cycles[1:]):
                require(end > start, f"Noncausal {a} to {b} firing")
                latencies[f"{a}_to_{b}"].append(end - start)
            require(final > cycles[-1], "Accumulation preceded product availability")
            latencies["combine_to_accumulate"].append(final - cycles[-1])
        group_cycle = wanted["group"][(group, -1)]
        first_read = wanted["read"][(group, 0)]
        chunk0 = wanted["chunk"][(group, 0)]
        chunk1 = wanted["chunk"][(group, 1)]
        require(group_cycle < chunk0 < first_read, "Invalid group startup ordering")
        require(wanted["read"][(group, 15)] < chunk1 < wanted["read"][(group, 16)], "Invalid second-chunk ordering")
        last = wanted["last"][(group, INPUTS - 1)]
        accumulations = [wanted["accumulate"][(group, item)] for item in range(INPUTS - 1)] + [last]
        require(all(b > a for a, b in zip(accumulations, accumulations[1:])), "Accumulation order changed within group")
        if group_overlap:
            if group + 1 < GROUPS:
                require(wanted["group"][(group + 1, -1)] > wanted["read"][(group, INPUTS - 1)],
                        "Next group started before prior operand issue completed")
        else:
            restart = wanted["restart"][(group, -1)]
            require(restart > last, "Restart preceded last accumulation")
            if group + 1 < GROUPS:
                require(wanted["group"][(group + 1, -1)] > restart, "Group started before restart")
        if group + 1 < GROUPS:
            require(wanted["accumulate"][(group + 1, 0)] > last, "Next group accumulation preceded prior group completion")
        post = [wanted[name][(group, -1)] for name in ("bias", "add", "round", "collect")]
        require(all(b > a for a, b in zip([last] + post, post)), "Invalid bias-to-collect ordering")
    require(wanted["group"][(0, -1)] > token_events["start"][SAMPLE], "First group preceded start")
    require(token_events["emit"][SAMPLE] > wanted["collect"][(GROUPS - 1, -1)], "Emit preceded final collect")
    put = token_events["put"][SAMPLE]
    next_put = token_events["put"][SAMPLE + 1]
    reads = [row["cycle"] for row in detail["read"]]
    require(put < reads[0] <= reads[-1] < next_put, "Read firings outside token service interval")
    within_gaps = [wanted["read"][(g, i + 1)] - wanted["read"][(g, i)]
                   for g in range(GROUPS) for i in range(INPUTS - 1)]
    across_gaps = [wanted["read"][(g + 1, 0)] - wanted["read"][(g, INPUTS - 1)] for g in range(GROUPS - 1)]
    partition = {"before_first_read": reads[0] - put, "read_issue_cycles": len(reads),
                 "within_group_nonissue_cycles": sum(gap - 1 for gap in within_gaps),
                 "between_group_nonissue_cycles": sum(gap - 1 for gap in across_gaps),
                 "after_last_read": next_put - reads[-1] - 1}
    require(sum(partition.values()) == next_put - put, "Service-cycle partition does not cover interval")
    return {"frame": SAMPLE // TOKENS, "token": SAMPLE % TOKENS,
            "put_cycle": put, "next_put_cycle": next_put, "service_interval_cycles": next_put - put,
            "service_interval_interpretation": "Observed put-to-next-put interval; includes any input or downstream wait after this token completes",
            "accepted_to_emit_cycles": token_events["emit"][SAMPLE] - put,
            "next_put_after_emit_cycles": next_put - token_events["emit"][SAMPLE],
            "cycle_partition": partition,
            "cycle_partition_convention": "Disjoint cycle slots [put, next put); issue slots contain process2Read firings, all other slots are classified by their position",
            "read_issue_fraction": len(reads) / (next_put - put),
            "read_issue_mean_interval_cycles": (reads[-1] - reads[0]) / (len(reads) - 1),
            "within_group_read_intervals_cycles": distribution(within_gaps),
            "group_first_read_intervals_cycles": distribution([wanted["read"][(g + 1, 0)] - wanted["read"][(g, 0)] for g in range(GROUPS - 1)]),
            "last_accumulate_to_next_group_read_cycles": distribution([wanted["read"][(g + 1, 0)] - wanted["last"][(g, INPUTS - 1)] for g in range(GROUPS - 1)]),
            "last_read_to_last_accumulate_cycles": distribution([wanted["last"][(g, INPUTS - 1)] - wanted["read"][(g, INPUTS - 1)] for g in range(GROUPS)]),
            "pipeline_step_cycles": {name: distribution(values) for name, values in latencies.items()},
            "last_collect_to_emit_cycles": token_events["emit"][SAMPLE] - wanted["collect"][(GROUPS - 1, -1)]}


def check_block_profile(text: str, stderr: str, baseline_text: str, baseline_stderr: str,
                        perf_text: str, perf_stderr: str, expected: list[int], fixture_frames: int,
                        group_overlap: bool = False) -> tuple[dict, list, list]:
    require("SWAY_BLOCK" not in stderr and "SWAY_LINEAR" not in stderr, "Block instrumentation or failure on stderr")
    filtered, stages, linear, detail, counts = parse_records(text)
    old_records = lambda value: [line for line in value.splitlines() if line.startswith("SWAY_")]
    require(old_records(filtered) == old_records(baseline_text), "Block instrumentation changed baseline SWAY_ transactions")
    previous, _ = check_profile(filtered, stderr, perf_text, perf_stderr, expected, fixture_frames)
    baseline, _ = check_profile(baseline_text, baseline_stderr, perf_text, perf_stderr, expected, fixture_frames)
    require(previous == baseline, "Block instrumentation changed profile results")
    outer = {name: [] for name in ("block0_in", "block1_in")}
    for line in filtered.splitlines():
        fields = line.split(",")
        if fields[0] == "SWAY_PROFILE" and fields[1] in outer:
            outer[fields[1]].append(int(fields[-1]))
    require(stages["inproj_in"] == linear["put"], "Linear put disagrees with Block0 inproj input")
    require(stages["inproj_out"] == linear["get"], "Linear get disagrees with Block0 inproj output")
    factor = (400, 400, 400, 400, 400, 380, 20, 20, 40)
    for name, expected_count in zip(COUNT_NAMES, factor):
        for ordinal, row in enumerate(counts[name]):
            require(row["count"] == (ordinal + 1) * expected_count, "Linear rule count disagrees with model dimensions")
            require(linear["start"][ordinal] < row["cycle"] < linear["emit"][ordinal], "Count record outside token execution")
        require(bool(detail[name]) and counts[name][SAMPLE]["cycle"] == detail[name][-1]["cycle"],
                f"Sample {name} counter cycle disagrees with its final firing")
    for ordinal in range(TOTAL):
        pipeline = [linear[name][ordinal] for name in TOKEN_EVENTS]
        require(all(b > a for a, b in zip(pipeline, pipeline[1:])), "Noncausal linear token events")
        if ordinal:
            require(linear["put"][ordinal] > linear["emit"][ordinal - 1], "Retained-input slot reused before prior emit")
    sample = validate_detail(detail, linear, group_overlap)
    spans = []
    pairs = (("block_input_queue", "block0_in", "norm_in"), ("normalization_to_ready", "norm_in", "norm_ready"),
             ("normalization_output_wait", "norm_ready", "inproj_in"), ("input_projection", "inproj_in", "inproj_out"),
             ("conv_input_queue", "inproj_out", "conv_start"), ("convolution", "conv_start", "conv_out"),
             ("conv_output_queue", "conv_out", "stateproj_in"), ("state_projection", "stateproj_in", "deltaproj_in"),
             ("delta_projection", "deltaproj_in", "scan_in"), ("selective_scan", "scan_in", "gate_in"),
             ("gating", "gate_in", "outproj_in"), ("output_projection", "outproj_in", "residual_in"),
             ("residual", "residual_in", "block_ready"), ("block_output_wait", "block_ready", "block1_in"))
    all_stages = {**stages, **outer}
    for name, before, after in pairs:
        for ordinal in range(TOTAL):
            begin, end = all_stages[before][ordinal], all_stages[after][ordinal]
            require(end >= begin, f"Noncausal {name} stage span at token {ordinal}")
            frame, token = divmod(ordinal, TOKENS)
            spans.append(dict(zip(CSV_FIELDS, (name, frame, token, begin, end, end - begin))))
    first, last = TRIM_FRAMES, STREAM_FRAMES - TRIM_FRAMES - 1
    stage_summary = {}
    for name, _, _ in pairs:
        rows = [row for row in spans if row["stage"] == name]
        stage_summary[name] = {
            "all_frames_cycles": distribution([row["elapsed_cycles"] for row in rows]),
            "steady_frames_cycles": distribution([row["elapsed_cycles"] for row in rows if first <= row["frame"] <= last])}
    boundary_summary = {}
    for name, cycles in stages.items():
        selected = cycles[first * TOKENS:(last + 1) * TOKENS]
        boundary_summary[name] = {"transactions": len(cycles), "steady_token_intervals_cycles": distribution([b - a for a, b in zip(selected, selected[1:])])}
    lifecycle_summary = {}
    for name, before, after in (("accepted_to_emit", "put", "emit"), ("capture_to_emit", "capture", "emit"),
                                ("start_to_emit", "start", "emit"), ("emit_to_get", "emit", "get")):
        values = [end - begin for begin, end in zip(linear[before], linear[after])]
        lifecycle_summary[name] = {"all_frames_cycles": distribution(values),
                                   "steady_frames_cycles": distribution(values[first * TOKENS:(last + 1) * TOKENS])}
    refill = [linear["put"][i] - linear["emit"][i - 1] for i in range(1, TOTAL)]
    norm_lead = [linear["emit"][i - 1] - stages["norm_ready"][i] for i in range(1, TOTAL)]
    get_delay = [get - emit for emit, get in zip(linear["emit"], linear["get"])]
    outer_delay = [end - start for start, end in zip(stages["block_ready"], outer["block1_in"])]
    residual_refill = [stages["norm_in"][i + RESIDUAL_CAPACITY] - stages["residual_in"][i]
                       for i in range(TOTAL - RESIDUAL_CAPACITY)]
    residual_residence = [end - begin for begin, end in zip(stages["norm_in"], stages["residual_in"])]
    evidence = {"next_put_after_previous_emit_cycles": distribution(refill),
                "next_norm_ready_before_previous_emit_cycles": distribution(norm_lead),
                "emit_to_get_cycles": distribution(get_delay),
                "block_ready_to_downstream_transfer_cycles": distribution(outer_delay),
                "retained_input_refilled_at_first_possible_cycle_all_tokens": all(x == 1 for x in refill),
                "next_normalized_token_ready_before_previous_emit_all_tokens": all(x >= 0 for x in norm_lead),
                "linear_output_consumed_next_cycle_all_tokens": all(x == 1 for x in get_delay),
                "previous_output_consumed_before_next_token_start_all_tokens": all(linear["get"][i - 1] < linear["start"][i] for i in range(1, TOTAL)),
                "block_output_consumed_next_cycle_all_tokens": all(x == 1 for x in outer_delay),
                "sample_emit_one_cycle_after_final_collect": sample["last_collect_to_emit_cycles"] == 1,
                "residual_slots": {"capacity": RESIDUAL_CAPACITY, "release_to_reuse_pairs": len(residual_refill),
                                   "release_to_reuse_cycles": distribution(residual_refill),
                                   "reused_cycle_after_release_all_pairs": all(value == 1 for value in residual_refill),
                                   "steady_residence_cycles": distribution(residual_residence[first * TOKENS:(last + 1) * TOKENS]),
                                   "interpretation": "norm_in[i+4] minus residual_in[i] observes residualQ slot reuse; norm_in-to-residual_in is slot residence, not a standalone stage latency"}}
    result = {"status": "pass", "evidence": "Bluesim simulation with Block0 internal rule-firing instrumentation",
              "scope": "Selected ECP5 input-projection FIFO/scheduling configuration and repeated checked-in fixtures; no board measurement",
              "group_scheduling": "issue-overlapped" if group_overlap else "completion-driven",
              "dut": "mkSwayBaseline.block0", "frames_checked": STREAM_FRAMES,
              "scalar_outputs_checked": previous["scalar_outputs_checked"],
              "baseline_sway_records_exactly_equal": True,
              "block_transactions_checked": sum(map(len, stages.values())),
              "linear_token_events_checked": sum(map(len, linear.values())),
              "linear_detail_events_checked": sum(map(len, detail.values())),
              "linear_count_records_checked": sum(map(len, counts.values())),
              "steady_window": {"first_frame": first, "last_frame": last},
              "cycle_convention": "Shared root-reset origin; elapsed cycles are endpoint subtraction, without +1",
              "stage_span_interpretation": "Accepted-to-transferred spans include queues and downstream backpressure; overlapping spans must not be summed",
              "stage_spans": stage_summary, "boundaries": boundary_summary,
              "input_projection_lifecycle": lifecycle_summary,
              "lifecycle_interpretation": "Accepted-to-emit is token completion latency, including output enqueue backpressure; it is distinct from successive accepted-token intervals",
              "input_projection_rule_firings_per_token": dict(zip(COUNT_NAMES, factor)),
              "accumulate_count_interpretation": "380 process3 normal accumulations plus 20 process3Last final accumulations = 400 accumulated four-lane product vectors per token",
              "sample_input_projection": sample, "flow_evidence": evidence,
              "kernel_output": previous["kernel_output"]}
    sample_rows = sorted([row for rows in detail.values() for row in rows] +
                         [dict(zip(SAMPLE_FIELDS, (name, SAMPLE, SAMPLE % TOKENS, cycles[SAMPLE], -1, -1))) for name, cycles in linear.items()],
                         key=lambda row: (row["cycle"], row["event"]))
    return result, spans, sample_rows


def main() -> None:
    hw_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("log", "results/block_profile/bluesim/stream.log"), ("stderr", "results/block_profile/bluesim/stream.stderr.log"),
                          ("baseline-log", "results/profile/bluesim/stream.log"), ("baseline-stderr", "results/profile/bluesim/stream.stderr.log"),
                          ("perf-log", "results/perf/bluesim/stream.log"), ("perf-stderr", "results/perf/bluesim/stream.stderr.log"),
                          ("input", "generated/test_input.hex"), ("expected", "generated/test_expected.hex"),
                          ("output", "results/block_profile/bluesim/summary.json"), ("stages", "results/block_profile/bluesim/stages.csv"),
                          ("sample", "results/block_profile/bluesim/sample.csv")):
        parser.add_argument("--" + name, type=Path, default=Path(default))
    parser.add_argument("--baseline-schedule", type=Path)
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--group-overlap", action="store_true", help="Validate issue-overlapped groups with no process3Restart firings")
    parser.add_argument("--backend", choices=("bluesim", "iverilog"), default="bluesim")
    parser.add_argument("--linear-source", type=Path, default=hw_dir / "bsv/SwayLinear.bsv",
                        help="SwayLinear package selected by the build, for source provenance")
    args = parser.parse_args()
    if (args.baseline_schedule is None) != (args.schedule is None):
        parser.error("baseline-schedule and schedule must be supplied together")
    sources = hardware_sources(hw_dir, args.linear_source)
    sources.extend(hw_dir / name for name in ("sim/TbSwayPerf.bsv", "Makefile", "perf.mk", "reference/check_perf.py", "reference/check_profile.py", "reference/check_block_profile.py"))
    plot_source = hw_dir / "reference/plot_block_profile.py"
    if plot_source.exists():
        sources.append(plot_source)
    evidence = [args.log, args.stderr, args.baseline_log, args.baseline_stderr, args.perf_log, args.perf_stderr, args.input, args.expected]
    if args.schedule is not None:
        evidence.extend((args.baseline_schedule, args.schedule))
    outputs = [args.output, args.stages, args.sample]
    protected = {path.resolve() for path in sources + evidence}
    if len({path.resolve() for path in outputs}) != 3 or any(path.resolve() in protected for path in outputs):
        parser.error("outputs must be distinct and must not overwrite source or evidence files")
    for path in outputs:
        path.unlink(missing_ok=True)
    try:
        inputs = read_hex(args.input)
        require(len(inputs) % INPUT_WORDS == 0, "Incomplete input fixture")
        result, spans, sample = check_block_profile(args.log.read_text(), args.stderr.read_text(),
            args.baseline_log.read_text(), args.baseline_stderr.read_text(), args.perf_log.read_text(),
            args.perf_stderr.read_text(), read_hex(args.expected), len(inputs) // INPUT_WORDS, args.group_overlap)
        result["schedule_audit"] = check_schedules(args.baseline_schedule.read_text(), args.schedule.read_text()) if args.schedule else {"status": "not_requested"}
        if args.backend == "iverilog":
            result["evidence"] = "Generated-Verilog/Icarus simulation with Block0 internal rule-firing instrumentation"
        result["raw_event_log"] = str(args.log)
        result["evidence_sha256"] = {str(path): sha256(path) for path in evidence}
        result["source_sha256"] = source_manifest(hw_dir, sources)
        for path, rows, fields in ((args.stages, spans, CSV_FIELDS), (args.sample, sample, SAMPLE_FIELDS)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        result["derived_csv_sha256"] = {str(path): sha256(path) for path in (args.stages, args.sample)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
        for path in outputs:
            path.unlink(missing_ok=True)
        parser.exit(1, f"SWAY_BLOCK_PROFILE_CHECK_FAIL: {error}\n")
    print(f"SWAY_BLOCK_PROFILE_CHECK_PASS frames={result['frames_checked']} outputs={result['scalar_outputs_checked']} block_events={result['block_transactions_checked']}")
    print("Baseline scalar and boundary transactions are exactly unchanged.")
    print(json.dumps(result["sample_input_projection"]["cycle_partition"], sort_keys=True))


if __name__ == "__main__":
    main()
