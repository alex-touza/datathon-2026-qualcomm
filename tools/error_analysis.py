#!/usr/bin/env python3
"""Break an eval run down by level, language, options and topic.

Reads one `results/eval-*.csv` (or a permutation run) and the validation
problems, so the README's error analysis comes from the same numbers as
`results/ablations.csv`. No model, no network, no board.

    python3 tools/error_analysis.py results/eval-20261115-101748-hint-ladder-v1.csv
    python3 tools/error_analysis.py results/perm-20261114-124819-perm2-full.csv --column correct_single
"""
import argparse
import collections
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot import topics  # noqa: E402


def accuracy(rows):
    return 100 * sum(row["correct"] for row in rows) / len(rows) if rows else 0.0


def table(title, rows, key):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    print(f"\n### {title}\n")
    print("| " + title + " | Correct | Problems | Accuracy |")
    print("| --- | --- | --- | --- |")
    for value in sorted(groups, key=lambda name: (str(type(name)), name)):
        group = groups[value]
        print(f"| {value} | {sum(r['correct'] for r in group)} | {len(group)} | "
              f"{accuracy(group):.1f}% |")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv")
    p.add_argument("--input", default="data/val.jsonl")
    p.add_argument("--column", default="correct",
                   help="correct column; permutation runs use correct_single")
    args = p.parse_args()

    text = {}
    for line in open(args.input):
        if line.strip():
            problem = json.loads(line)
            text[f"{problem['id']}|{problem['language']}"] = problem

    rows = []
    for raw in csv.DictReader(open(args.csv)):
        key = f"{raw['id']}|{raw['language']}"
        problem = text.get(key)
        rows.append({
            "correct": int(float(raw[args.column])),
            "level": int(raw["level"]),
            "language": raw["language"],
            "has_options": raw.get("has_options") in ("True", "true", "1", 1, True),
            "topic": topics.classify((problem or {}).get("text", ""))["topic"],
        })

    print(f"# Error analysis: {os.path.basename(args.csv)}")
    print(f"\nOverall: **{sum(r['correct'] for r in rows)}/{len(rows)} = "
          f"{accuracy(rows):.1f}%**")
    table("Level", rows, "level")
    table("Language", rows, "language")
    table("OCR options", rows, "has_options")
    table("Topic", rows, "topic")


if __name__ == "__main__":
    main()
