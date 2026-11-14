#!/usr/bin/env python3
"""Compare two eval.py runs on the same problems: McNemar's exact test.

    python3 experiments/mcnemar.py results/eval-A.csv results/eval-B.csv

On 50 problems, 22% vs 14% is 11 against 7 right answers: a difference that
looks large in percentage and is usually noise. McNemar looks only at the
problems where the two runs disagree, which is the only evidence there is, and
reports an exact binomial p-value so nobody has to trust a rule of thumb.

Stdlib only, like everything that runs on the board.
"""
import argparse
import csv
import math
from collections import Counter


def load(path):
    with open(path, newline="") as f:
        return {f"{row['id']}|{row['language']}": row for row in csv.DictReader(f)}


def binomial_two_sided(k, n):
    """Exact two-sided p for k successes out of n at p=0.5."""
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("a", help="first eval CSV (the reference)")
    p.add_argument("b", help="second eval CSV (the change)")
    args = p.parse_args()

    a, b = load(args.a), load(args.b)
    shared = sorted(set(a) & set(b))
    if not shared:
        raise SystemExit("the two files share no id|language rows")

    table = Counter()
    for key in shared:
        ca = a[key]["correct"] in ("True", "1")
        cb = b[key]["correct"] in ("True", "1")
        table[(ca, cb)] += 1
    both = table[(True, True)]
    only_a = table[(True, False)]
    only_b = table[(False, True)]
    neither = table[(False, False)]
    n = len(shared)
    acc_a = 100 * (both + only_a) / n
    acc_b = 100 * (both + only_b) / n
    discordant = only_a + only_b
    p_value = binomial_two_sided(min(only_a, only_b), discordant)

    print(f"{n} shared problems")
    print(f"  {args.a.split('/')[-1]:45} {acc_a:5.1f}%")
    print(f"  {args.b.split('/')[-1]:45} {acc_b:5.1f}%")
    print(f"  delta {acc_b - acc_a:+.1f} points")
    print(f"\n  right in both {both}, wrong in both {neither}")
    print(f"  right only in A {only_a}, right only in B {only_b}")
    print(f"  McNemar exact two-sided p = {p_value:.3f} "
          f"({'significant' if p_value < 0.05 else 'not significant'} at 0.05)")
    if discordant < 10:
        print("  fewer than 10 discordant problems: treat this as a direction, not a result")


if __name__ == "__main__":
    main()
