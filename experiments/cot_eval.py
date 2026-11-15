#!/usr/bin/env python3
"""Short chain-of-thought evaluation for a small reasoning model.

    python3 experiments/cot_eval.py --url http://127.0.0.1:8092/v1/chat/completions \
        --limit 100 --max-tokens 120 --name math15b-cot

The model writes a brief reasoning trace and must end with "Answer: X".
The official pipeline stays untouched; this is an experiment.
"""
import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot.client import post  # noqa: E402

LETTERS = "ABCDE"
SYSTEM = ("You are a careful maths tutor. Reason in at most three short sentences. "
          "Then write one final line in exactly this form: Answer: X, where X is A, B, C, D or E. "
          "Do not write anything after that line.")
ANSWER_RE = re.compile(r"answer\s*[:\-]?\s*([A-E])\b", re.IGNORECASE)
REASON_SYSTEM = ("You are a careful maths tutor. Give at most two short sentences of reasoning. "
                 "Do not give the final answer.")
CHOICE_SYSTEM = "You are a careful maths tutor. Reply with one letter A-E only."


def format_problem(problem, suffix=""):
    lines = [problem.get("text", "").strip()]
    options = problem.get("options") or {}
    for letter in LETTERS:
        if letter in options:
            lines.append(f"{letter}) {options[letter]}")
    if suffix:
        lines.append(suffix)
    return "\n".join(lines)


def ask(url, problem, max_tokens, timeout):
    response = post(url, {
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": format_problem(
                         problem, "Think briefly, then end with: Answer: X")}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "seed": 42,
        "cache_prompt": True,
    }, timeout=timeout)
    choice = response["choices"][0]
    text = choice["message"].get("content") or ""
    matches = ANSWER_RE.findall(text)
    letter = matches[-1].upper() if matches else "D"
    return letter, text, bool(matches), response.get("timings", {})


def ask_two_stage(url, problem, reason_tokens, timeout):
    """Bound the reasoning first, then force a one-letter second call."""
    base = format_problem(problem)
    reasoned = post(url, {
        "messages": [{"role": "system", "content": REASON_SYSTEM},
                     {"role": "user", "content": base + "\nReason in at most two sentences."}],
        "max_tokens": reason_tokens,
        "temperature": 0,
        "seed": 42,
        "cache_prompt": True,
    }, timeout=timeout)
    reasoning = (reasoned["choices"][0]["message"].get("content") or "").strip()
    response = post(url, {
        "messages": [{"role": "system", "content": CHOICE_SYSTEM},
                     {"role": "user", "content": base + "\nReasoning:\n" + reasoning +
                                                  "\nAnswer with one option letter only."}],
        "grammar": "root ::= [A-E]",
        "max_tokens": 1,
        "temperature": 0,
        "seed": 42,
        "cache_prompt": True,
    }, timeout=timeout)
    choice = response["choices"][0]
    text = choice["message"].get("content") or ""
    return text.strip()[:1].upper() or "D", reasoning, True, response.get("timings", {})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default="data/val.jsonl")
    p.add_argument("--url", default="http://127.0.0.1:8092/v1/chat/completions")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--max-tokens", type=int, default=120)
    p.add_argument("--strategy", choices=("brief", "two-stage"), default="brief")
    p.add_argument("--reason-tokens", type=int, default=60,
                   help="maximum tokens for the first reasoning call in two-stage mode")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--only-with-options", action="store_true", default=True)
    p.add_argument("--name", default="cot")
    args = p.parse_args()

    rows = [json.loads(line) for line in open(args.input) if line.strip()]
    if args.only_with_options:
        rows = [r for r in rows if r.get("options")]
    if args.limit:
        rows = rows[:args.limit]
    out = []
    for n, problem in enumerate(rows, 1):
        start = time.perf_counter()
        try:
            if args.strategy == "two-stage":
                letter, text, found, timings = ask_two_stage(
                    args.url, problem, args.reason_tokens, args.timeout)
            else:
                letter, text, found, timings = ask(
                    args.url, problem, args.max_tokens, args.timeout)
            status = "ok" if found else "no_answer"
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError) as error:
            letter, text, found, timings = "D", "", False, {}
            status = f"error: {error}"
        row = {"id": problem["id"], "language": problem["language"], "level": problem["level"],
               "text_only": problem["text_only"], "label": problem["label"], "answer": letter,
               "correct": int(letter == problem["label"]), "status": status,
               "seconds": round(time.perf_counter() - start, 2),
               "output_tokens": timings.get("predicted_n", 0), "raw": text}
        out.append(row)
        if n % 10 == 0:
            acc = 100 * sum(r["correct"] for r in out) / len(out)
            print(f"{n}/{len(rows)} running accuracy {acc:.1f}%", flush=True)

    def acc(rs):
        return 100 * sum(r["correct"] for r in rs) / len(rs) if rs else 0

    print(f"\nAccuracy: {sum(r['correct'] for r in out)}/{len(out)} = {acc(out):.1f}%")
    print("Latency: mean %.2fs median %.2fs worst %.2fs" % (
        statistics.mean(r["seconds"] for r in out),
        statistics.median(r["seconds"] for r in out),
        max(r["seconds"] for r in out)))
    print("Status:", dict(Counter(r["status"] for r in out)))
    print("Letters:", dict(Counter(r["answer"] for r in out)))
    for field in ("level", "text_only", "language"):
        groups = {}
        for r in out:
            groups.setdefault(r[field], []).append(r)
        print(f"\nBy {field}")
        for key in sorted(groups, key=str):
            print(f"  {str(key):6} {sum(r['correct'] for r in groups[key]):3}/{len(groups[key]):<3} {acc(groups[key]):5.1f}%")

    os.makedirs("results", exist_ok=True)
    path = f"results/cot-{args.name}.csv"
    fields = ["id", "language", "level", "text_only", "label", "answer", "correct",
              "status", "seconds", "output_tokens", "raw"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out)
    print(f"\nDetails: {path}")


if __name__ == "__main__":
    main()
