#!/usr/bin/env python3
"""Official entry point: answer every problem with one letter, A to E.

    python3 predict.py --input problems.jsonl --output predictions.csv
    python3 predict.py --input problems.jsonl --output predictions.csv --rag 1

Input: one JSON object per line with at least "id" and "text", plus
"options" ({"A": ..., "E": ...}) and "language" when available.
Output: a CSV with id, language, answer, seconds, status.

Rules: runs offline, at most 60 seconds per problem, deterministic.
"""
import argparse
import csv
import json
import os
import sys

from copilot.exam import solve

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--rag", type=int, default=0, metavar="K", help="add K similar solved problems")
    p.add_argument("--alpha", type=float, default=None,
                   help="override CALIBRATION_ALPHA (0 = no letter-bias correction)")
    p.add_argument("--index", default="data/index.db")
    p.add_argument("--solver", choices=("deepseek", "baseline"),
                   help="DeepSeek by default; --rag or --alpha selects the legacy baseline")
    args = p.parse_args()
    solver = args.solver or ("baseline" if args.rag or args.alpha is not None else "deepseek")
    if solver == "deepseek" and (args.rag or args.alpha is not None):
        p.error("--rag and --alpha require --solver baseline")

    # A fresh clone has no results/ directory; the official run should not fail on that.
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    index = None
    if args.rag:
        if not os.path.exists(args.index):
            sys.exit(f"{args.index} not found. Build it first: python3 build_index.py")
        from copilot.rag import Index
        index = Index(args.index)

    with open(args.input) as f:
        problems = [json.loads(line) for line in f if line.strip()]

    with open(args.output, "w", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["id", "language", "answer", "seconds", "status"])
        for n, problem in enumerate(problems, 1):
            result = solve(problem, input_path=args.input, solver=solver, index=index,
                           k=args.rag, alpha=args.alpha)
            writer.writerow([problem["id"], problem.get("language", ""), result["answer"],
                             result["seconds"], result["status"]])
            out.flush()
            print(f"[{n}/{len(problems)}] {problem['id']} -> {result['answer']} "
                  f"in {result['seconds']:.1f} s ({result['status']})", file=sys.stderr)


if __name__ == "__main__":
    main()
