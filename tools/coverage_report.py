#!/usr/bin/env python3
"""How much of the bank is actually answerable, and what that caps your score at.

The pipeline can only reason about a problem if it has (a) the question text and
(b) readable options. Everything else is a coin flip, so this report measures the
funnel that turns 1,747 pictures into letters.

    python3 tools/coverage_report.py                      # data/train.jsonl, data/val.jsonl
    python3 tools/coverage_report.py --input data/val.jsonl

Stdlib only, so it runs on the board and on your laptop.
"""
import argparse
import json
import statistics
from collections import Counter, defaultdict

FALLBACK_PRIOR = 0.25  # always-D floor; the score you get when you can't read a problem
CHARS_PER_TOKEN = 4     # rough, for English/Catalan problem text
PREFILL_TOK_S = 11.0    # measured on the board
MIN_TEXT = 20           # shorter than this and there is no question to read


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def addressable(p):
    text = (p.get("text") or "").strip()
    return len(text) >= MIN_TEXT and bool(p.get("options"))


def money(n, d):
    return f"{n:5}/{d:<5} {100 * n / d:5.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", nargs="+", default=["data/train.jsonl", "data/val.jsonl"])
    args = ap.parse_args()

    problems = [p for path in args.input for p in load(path)]
    if not problems:
        raise SystemExit("no problems found")

    n = len(problems)
    has_text = sum(1 for p in problems if len((p.get("text") or "").strip()) >= MIN_TEXT)
    has_opts = sum(1 for p in problems if p.get("options"))
    both = sum(1 for p in problems if addressable(p))
    text_only = sum(1 for p in problems if p.get("text_only"))

    print(f"problems: {n}")
    print(f"  question text present   {money(has_text, n)}")
    print(f"  options readable (OCR)  {money(has_opts, n)}")
    print(f"  ANSWERABLE (text+opts)  {money(both, n)}   <- your real working set")
    print(f"  flagged text_only       {money(text_only, n)}   (the rest is picture-only)")

    # Where the working set leaks, by level and language.
    for field in ("level", "language"):
        groups = defaultdict(lambda: [0, 0, 0])
        for p in problems:
            g = groups[p.get(field, "?")]
            g[0] += 1
            g[1] += bool(p.get("options"))
            g[2] += addressable(p)
        print(f"\nby {field}: size / options / answerable")
        for key in sorted(groups, key=str):
            size, opts, addr = groups[key]
            print(f"  {str(key):6} {size:5}  {100 * opts / size:5.1f}%  {100 * addr / size:5.1f}%")

    # Prompt cost of the problems you can read: prefill dominates the 60 s budget.
    tokens = [(len(p["text"]) + sum(len(v) for v in p["options"].values())) // CHARS_PER_TOKEN
              for p in problems if addressable(p)]
    if tokens:
        tokens.sort()
        print("\nprompt size of answerable problems (text + options):")
        for label, value in (("p50", statistics.median(tokens)),
                             ("p90", tokens[int(0.9 * (len(tokens) - 1))]),
                             ("max", tokens[-1])):
            print(f"  {label:3} {value:4} tok  ~{value / PREFILL_TOK_S:5.1f} s prefill")

    labels = Counter(p["label"] for p in problems)
    top, count = labels.most_common(1)[0]
    print(f"\nlabels: " + "  ".join(f"{l}:{labels[l] / n * 100:4.1f}%" for l in "ABCDE"))
    print(f"best blind guess: {top} at {count / n * 100:.1f}%")

    # The number that matters: overall score = covered * accuracy + uncovered * blind floor.
    print("\nprojected overall accuracy = coverage x accuracy_on_covered + (1-coverage) x blind")
    blind = count / n
    print(f"  {'coverage':>8} " + "".join(f"{a:>10.0%}" for a in (0.25, 0.35, 0.45, 0.55)))
    for coverage in (0.40, 0.55, 0.62, 0.75, 0.90):
        row = f"  {coverage:>8.0%} "
        for acc in (0.25, 0.35, 0.45, 0.55):
            row += f"{coverage * acc + (1 - coverage) * blind:>10.1%}"
        print(row)
    print("  (uncovered problems are answered with the fallback letter, roughly the blind floor)")


if __name__ == "__main__":
    main()
