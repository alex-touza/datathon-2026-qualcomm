#!/usr/bin/env python3
"""Make a spreadsheet you can review by hand: one row per problem, mistakes marked.

Person C runs this after every eval. It joins the results CSV with the problems
file and writes results/review-<stamp>.csv, which opens in Excel or Google Sheets.
The last two columns are empty on purpose: you fill them in.

    python3 tools/error_table.py results/eval-20260714-120000-calibrated.csv
    python3 tools/error_table.py results/eval-...csv --problems data/train.jsonl

No coding needed. It also prints the summary numbers you paste into the shared table.
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict


def load_problems(path):
    problems = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                problems[row["id"]] = row
    return problems


def percent(hits, total):
    return f"{100 * hits / total:5.1f}%" if total else "  n/a"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("eval_csv", help="the file eval.py wrote, e.g. results/eval-...-name.csv")
    ap.add_argument("--problems", default="data/val.jsonl", help="the problems file it was scored on")
    ap.add_argument("--out", default="results", help="where to write the review spreadsheet")
    args = ap.parse_args()

    problems = load_problems(args.problems)
    with open(args.eval_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("the eval file has no rows")

    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(args.out, f"review-{stamp}.csv")

    # utf-8-sig is what makes Excel show accents (Català, número) correctly on Windows.
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "language", "level", "has_options", "correct_answer",
                         "model_answer", "correct?", "confidence", "seconds",
                         "question text", "A", "B", "C", "D", "E",
                         "question readable? (yes/no)", "options readable? (yes/no)",
                         "mistake type", "your notes"])
        for row in rows:
            problem = problems.get(row["id"], {})
            options = problem.get("options") or {}
            writer.writerow([
                row["id"], row["language"], row["level"], row["has_options"],
                row["label"], row["answer"], "yes" if row["correct"] == "1" else "NO",
                row.get("confidence", ""), row["seconds"],
                (problem.get("text") or "(no text)").replace("\n", " ")[:300],
                options.get("A", ""), options.get("B", ""), options.get("C", ""),
                options.get("D", ""), options.get("E", ""),
                "", "", "", "",
            ])

    # Summary, so you can paste numbers into the shared table without counting by hand.
    total = len(rows)
    hits = sum(1 for r in rows if r["correct"] == "1")
    print(f"spreadsheet: {out_path}")
    print(f"accuracy: {hits}/{total} ({percent(hits, total)})\n")

    for field, title in (("level", "by level"), ("language", "by language"),
                         ("has_options", "with options / without")):
        groups = defaultdict(lambda: [0, 0])
        for r in rows:
            groups[r[field]][0] += r["correct"] == "1"
            groups[r[field]][1] += 1
        print(title)
        for key in sorted(groups, key=str):
            got, size = groups[key]
            print(f"  {str(key):8} {got:4}/{size:<4} {percent(got, size)}")
        print()

    missing_options = sum(1 for r in rows if r["has_options"] in ("False", "0", ""))
    missing_text = sum(1 for r in rows if len((problems.get(r["id"], {}).get("text") or "").strip()) < 20)
    print(f"problems with no readable options: {missing_options}/{total} ({percent(missing_options, total)})")
    print(f"problems with no question text:    {missing_text}/{total} ({percent(missing_text, total)})")
    print("\nNext: open the spreadsheet, filter 'correct?' = NO, and fill in the last two columns.")


if __name__ == "__main__":
    main()
