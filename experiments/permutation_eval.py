#!/usr/bin/env python3
"""Does averaging over option orders cancel the model's letter bias?

The baseline asks for one letter and shows the options in the order they came
in. This experiment asks for the five letter probabilities, then repeats the
question with the options in a different order and averages the probabilities
after mapping them back to the original options. If the bias is about the
letter/position rather than the content, averaging should help; if it is about
the content, it should not.

    python3 experiments/permutation_eval.py --limit 50 --name perm

It writes results/perm-<time>-<name>.csv and prints an ablation summary. Keep
this out of predict.py until it has earned its place.
"""
import argparse
import csv
import json
import math
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot.config import CONFIG  # noqa: E402
from copilot.llm import letter_logprobs  # noqa: E402
from copilot.pipeline import TIME_LIMIT  # noqa: E402

LETTERS = "ABCDE"


def sample(input_path, limit, languages, only_with_options=False):
    with open(input_path) as f:
        problems = [json.loads(line) for line in f if line.strip()]
    if languages:
        problems = [p for p in problems if p["language"] in languages]
    if only_with_options:
        problems = [p for p in problems if p.get("options")]
    random.Random(42).shuffle(problems)
    return problems[:limit] if limit else problems


def reorder(problem, order):
    """Return a copy whose option at position i holds the original option order[i]."""
    options = problem.get("options") or {}
    if len(options) != 5:
        return problem, LETTERS
    return {**problem, "options": {new: options[old] for new, old in zip(LETTERS, order)}}, order


def distribution(logprobs):
    """Turn the five log-probabilities into a distribution over the five letters."""
    weights = {letter: math.exp(lp) for letter, lp in logprobs.items()}
    total = sum(weights.values())
    if not total:
        return {letter: 1 / 5 for letter in LETTERS}
    return {letter: w / total for letter, w in weights.items()}


def distributions(problem, orders, timeout):
    """Return one distribution per order, mapped back to the original letters."""
    out, seconds = [], []
    for order in orders:
        reordered, mapping = reorder(problem, order)
        start = time.perf_counter()
        logprobs, _timings = letter_logprobs(reordered, timeout=timeout)
        seconds.append(time.perf_counter() - start)
        dist = distribution(logprobs)
        back = {letter: dist[new] for new, letter in zip(LETTERS, mapping)}
        out.append(back)
    return out, seconds


def mean_distribution(dists):
    return {letter: sum(d[letter] for d in dists) / len(dists) for letter in LETTERS}


def best(dist):
    return max(LETTERS, key=lambda letter: dist[letter])


def table(title, groups):
    print(f"\n{title}")
    for key in sorted(groups, key=str):
        hits = groups[key]
        print(f"  {str(key):12} {sum(hits):4}/{len(hits):<4} {100 * sum(hits) / len(hits):5.1f}%")


def mcnemar_exact(single, better, rows):
    """Two-sided exact McNemar: did 'better' change answers in a lopsided way?

    b = single wrong, better right; c = single right, better wrong.
    """
    b = sum(1 for r in rows if not r[single] and r[better])
    c = sum(1 for r in rows if r[single] and not r[better])
    n = b + c
    if not n:
        return b, c, 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1))
    return b, c, min(1.0, 2 * tail / 2 ** n)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default="data/val.jsonl")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--languages", default="", help="e.g. eng,cat")
    p.add_argument("--name", default="perm")
    p.add_argument("--orders", type=int, default=4, choices=(2, 4), help="2 = original+reverse, 4 = plus 2 shuffles")
    p.add_argument("--only-with-options", action="store_true",
                   help="evaluate only problems whose options OCR could read")
    args = p.parse_args()

    languages = set(filter(None, args.languages.split(",")))
    problems = sample(args.input, args.limit, languages, args.only_with_options)
    os.makedirs("results", exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = f"results/perm-{stamp}-{args.name}.csv"

    rows = []
    with open(path, "w", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["id", "language", "level", "has_options", "label", "single", "mean2",
                         "mean4", "correct_single", "correct_mean2", "correct_mean4",
                         "seconds_single", "seconds_mean2", "seconds_mean4"])
        for n, problem in enumerate(problems, 1):
            base = list(LETTERS)
            orders = [base, base[::-1]]
            if args.orders == 4:
                for seed in (1, 2):
                    shuffled = base[:]
                    random.Random(seed).shuffle(shuffled)
                    if shuffled not in orders:
                        orders.append(shuffled)
            if len(problem.get("options") or {}) != 5:
                orders = [base]  # permuting identical text would only waste time
            dists, seconds = distributions(problem, orders, timeout=TIME_LIMIT - 3)

            single = best(dists[0])
            mean2 = best(mean_distribution(dists[:2]))
            mean4 = best(mean_distribution(dists))
            label = problem["label"]
            row = {
                "id": problem["id"], "language": problem["language"], "level": problem["level"],
                "has_options": bool(problem.get("options")), "label": label,
                "single": single, "mean2": mean2, "mean4": mean4,
                "correct_single": single == label, "correct_mean2": mean2 == label,
                "correct_mean4": mean4 == label,
                "seconds_single": round(seconds[0], 2),
                "seconds_mean2": round(sum(seconds[:2]), 2),
                "seconds_mean4": round(sum(seconds), 2),
            }
            rows.append(row)
            writer.writerow([row["id"], row["language"], row["level"], row["has_options"],
                             row["label"], row["single"], row["mean2"], row["mean4"],
                             int(row["correct_single"]), int(row["correct_mean2"]),
                             int(row["correct_mean4"]), row["seconds_single"],
                             row["seconds_mean2"], row["seconds_mean4"]])
            out.flush()
            if len(orders) == 1:
                verdict = f"single {single}"
            else:
                verdict = f"single {single} / mean{len(orders)} {best(mean_distribution(dists))}"
            print(f"[{n}/{len(problems)}] {problem['id']} {problem['language']}: {verdict} "
                  f"(truth {label}) {row['seconds_mean4']:.1f} s", flush=True)

    def accuracy(key, subset=None):
        subset = rows if subset is None else subset
        return 100 * sum(r[key] for r in subset) / len(subset)

    print("\n" + "=" * 60)
    print(f"{len(rows)} problems, options in {sum(r['has_options'] for r in rows)}")
    print(f"single          {accuracy('correct_single'):5.1f}%   "
          f"mean {statistics.mean(r['seconds_single'] for r in rows):5.1f} s")
    print(f"mean of 2       {accuracy('correct_mean2'):5.1f}%   "
          f"mean {statistics.mean(r['seconds_mean2'] for r in rows):5.1f} s")
    if args.orders == 4:
        print(f"mean of 4       {accuracy('correct_mean4'):5.1f}%   "
              f"mean {statistics.mean(r['seconds_mean4'] for r in rows):5.1f} s")
    floor = sum(r["label"] == CONFIG["FALLBACK_ANSWER"] for r in rows) / len(rows)
    print(f"always {CONFIG['FALLBACK_ANSWER']}      {100 * floor:5.1f}%   random 20.0%")

    with_options = [r for r in rows if r["has_options"]]
    if with_options:
        print(f"\nOn the {len(with_options)} problems with OCR options")
        print(f"  single        {accuracy('correct_single', with_options):5.1f}%   "
              f"mean {statistics.mean(r['seconds_single'] for r in with_options):5.1f} s")
        print(f"  mean of 2     {accuracy('correct_mean2', with_options):5.1f}%   "
              f"mean {statistics.mean(r['seconds_mean2'] for r in with_options):5.1f} s")
        over = sum(r["seconds_mean2"] > TIME_LIMIT for r in with_options)
        print(f"  mean of 2 over the {TIME_LIMIT:.0f} s limit: {over}/{len(with_options)}")

    b, c, pvalue = mcnemar_exact("correct_single", "correct_mean2", rows)
    print(f"\nMcNemar (all): mean2 fixed {b}, broke {c}, exact two-sided p={pvalue:.3f}")
    if with_options:
        b, c, pvalue = mcnemar_exact("correct_single", "correct_mean2", with_options)
        print(f"McNemar (with options): fixed {b}, broke {c}, exact two-sided p={pvalue:.3f}")

    for key in ("single", "mean2", "mean4"):
        print(f"Letters {key:6} " + "  ".join(f"{L}:{Counter(r[key] for r in rows)[L]:>3}" for L in LETTERS))
    print("Letters truth  " + "  ".join(f"{L}:{Counter(r['label'] for r in rows)[L]:>3}" for L in LETTERS))

    for field, title in (("level", "By level (mean2)"),
                         ("has_options", "By whether OCR found the options (mean2)")):
        groups = defaultdict(list)
        for r in rows:
            groups[r[field]].append(r["correct_mean2"])
        table(title, groups)

    print(f"\nDetails: {path}")


if __name__ == "__main__":
    main()
