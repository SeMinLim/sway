#!/usr/bin/env python3
"""Plot actual rule firings from the validated Block0 sample CSV."""

import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=Path("results/block_profile/bluesim/sample.csv"))
    parser.add_argument("--output", type=Path, default=Path("results/block_profile/bluesim/timeline.svg"))
    args = parser.parse_args()
    if args.sample.resolve() == args.output.resolve():
        parser.error("output must not overwrite the input CSV")
    with args.sample.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in ("ordinal", "token", "cycle", "group", "item"):
            row[key] = int(row[key])
    puts = [row for row in rows if row["event"] == "put"]
    if len(puts) != 1:
        parser.error("sample must contain exactly one token acceptance")
    origin = puts[0]["cycle"]
    reads = [[row["cycle"] - origin for row in rows
              if row["event"] == "read" and row["group"] == group] for group in (0, 1)]
    if not all(len(group) == 20 for group in reads):
        parser.error("sample must contain two complete 20-issue output groups")
    issue_intervals = {b - a for group in reads for a, b in zip(group, group[1:])}
    gap = reads[1][0] - reads[0][-1] - 1
    group_interval = reads[1][0] - reads[0][0]
    layout = [("group", "Group start"), ("chunk", "Input chunk read"),
              ("read", "Operand issue"), ("dispatch", "ROM request"),
              ("response", "ROM response"), ("multiply", "Multiply"),
              ("combine", "Product combine"), ("accumulate", "Accumulate"),
              ("restart", "Group restart")]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.hashsalt": "sway-block0-profile"})
    fig, ax = plt.subplots(figsize=(11.6, 4.8))
    colors = ("#195680", "#16836F")
    for y, (event, _) in enumerate(reversed(layout)):
        ax.axhline(y, color="#DCE3E8", linewidth=0.65, zorder=0)
        for group in (0, 1):
            points = [row["cycle"] - origin for row in rows if row["group"] == group
                      and (row["event"] == event or event == "accumulate" and row["event"] == "last")]
            ax.scatter(points, [y] * len(points), marker="|", s=105,
                       linewidths=1.7, color=colors[group], zorder=3)
    issue_y = len(layout) - 1 - next(i for i, (name, _) in enumerate(layout) if name == "read")
    ax.add_patch(Rectangle((reads[0][-1] + 0.5, issue_y - 0.28), gap, 0.56,
                           facecolor="#F6D6AB", edgecolor="none", zorder=1))
    ax.text((reads[0][-1] + reads[1][0]) / 2, issue_y + 0.43,
            f"{gap} cycles without a new issue", ha="center", fontsize=8, color="#875410")
    ax.set_yticks(range(len(layout)), [label for _, label in reversed(layout)])
    ax.set_xlim(0, max(row["cycle"] - origin for row in rows
                       if row["event"] == "restart" and row["group"] == 1) + 2)
    ax.set_ylim(-0.6, len(layout) - 0.1)
    ax.set_xticks(range(0, int(ax.get_xlim()[1]) + 1, 10))
    ax.grid(axis="x", color="#E8EDF0", linewidth=0.6)
    ax.set_xlabel("Simulation cycle since input-projection token acceptance")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=9)
    interval_label = ", ".join(str(v) for v in sorted(issue_intervals))
    fig.suptitle("Block0 input projection: actual firings in the first two output groups",
                 x=0.02, ha="left", fontsize=13, fontweight="bold")
    fig.text(0.02, 0.895,
             f"Frame {puts[0]['ordinal'] // 16}, token {puts[0]['token']}  |  "
             f"Within-group issue interval: {interval_label} cycles  |  Group-start interval: {group_interval} cycles",
             color="#485965", fontsize=10)
    fig.text(0.02, 0.015,
             "Blue: output group 0. Green: output group 1. Each tick marks a rule firing; overlapping pipeline work is retained.",
             color="#485965", fontsize=9)
    fig.subplots_adjust(left=0.17, right=0.985, top=0.82, bottom=0.14)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, metadata={"Date": None} if args.output.suffix == ".svg" else None, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
