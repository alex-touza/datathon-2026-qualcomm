#!/usr/bin/env python3
"""Find the best CALIBRATION_ALPHA from an eval run, without touching the board.

First make a run with CALIBRATION_ALPHA=0 so the CSV has the model's scores:

    python3 eval.py --limit 0 --name scores

Then, on the laptop or the board:

    python3 tools/fit_calibration.py results/eval-<stamp>-scores.csv

It prints the accuracy for each alpha. Pick the one that wins on a run you did
NOT fit on (fit on half the rows, or on data/train.jsonl), then put it in
config.env and repeat the run with --name calibrated. The honest number is the
second run, not the best cell in this table.
"""
import argparse
import csv
import random
import sys

sys.path.insert(0, ".")
from copilot.decide import choose, label_prior  # noqa: E402


def parse_scores(text):
    scores = {}
    for piece in (text or "").split(","):
        if ":" in piece:
            letter, value = piece.split(":", 1)
            try:
                scores[letter.strip().upper()] = float(value)
            except ValueError:
                pass
    return scores


def accuracy(rows, alpha, prior):
    hits = 0
    for row in rows:
        answer = choose(row["scores"], fallback="D", prior=prior, alpha=alpha)
        hits += answer == row["label"]
    return hits / len(rows) if rows else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--split", type=float, default=0.5,
                    help="share of rows used to FIT the alpha (the rest reports it)")
    args = ap.parse_args()

    rows = []
    with open(args.csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scores = parse_scores(row.get("scores"))
            if scores:
                rows.append({"scores": scores, "label": (row.get("label") or "").upper()})
    if not rows:
        sys.exit("no rows with scores: run eval.py again with the current code, CALIBRATION_ALPHA=0")

    prior = label_prior()
    print("letter prior (train): " + "  ".join(f"{l}:{100 * p:.1f}%" for l, p in prior.items()))

    random.Random(42).shuffle(rows)
    cut = max(1, int(len(rows) * args.split))
    fit, report = rows[:cut], rows[cut:]

    print(f"\n{len(rows)} rows with scores: {len(fit)} to fit, {len(report)} to report\n")
    print(f"  {'alpha':>5}  {'fit':>8}  {'unseen':>8}")
    best = (0.0, -1.0)
    for step in range(0, 16):
        alpha = step / 10
        hit_fit = accuracy(fit, alpha, prior)
        hit_new = accuracy(report, alpha, prior) if report else 0.0
        print(f"  {alpha:5.1f}  {100 * hit_fit:7.1f}%  {100 * hit_new:7.1f}%")
        if hit_fit > best[1]:
            best = (alpha, hit_fit)
    print(f"\nbest on the fitting half: alpha={best[0]:.1f} ({100 * best[1]:.1f}%)")
    print("Put it in config.env as CALIBRATION_ALPHA, then run a fresh eval --limit 0.")


if __name__ == "__main__":
    main()
