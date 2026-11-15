"""Validate a complete benchmark and print Markdown tables from per-case JSONL."""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import statistics


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def key(row):
    return row["level"], row["language"], row["kind"], row["id"]


def p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("out", type=Path, nargs="?", default=Path("results/r1-mac"))
    p.add_argument("--csv", type=Path, help="Save local per-case scores, tokens and 60 s flags.")
    args = p.parse_args()
    manifest = read(args.out / "input/cases.jsonl")
    groups = {kind: read(args.out / f"{kind}.jsonl") for kind in ("text", "image")}
    rows = groups["text"] + groups["image"]
    cells = Counter(key(row)[:3] for row in rows)
    expected = {key(row): row["label"] for row in manifest}
    if len(cells) != 48 or min(cells.values()) < 10 or len(expected) != len(rows) \
            or len({key(row) for row in rows}) != len(rows):
        p.error("Expected distinct cases covering 48 cells, at least 10 each.")
    for row in rows:
        if expected.get(key(row)) != row["label"] or row["status"] != "ok":
            p.error(f"Unmatched or failed case: {key(row)} ({row['status']}).")
        src, dst = row["prompt_tokens"], row["completion_tokens"]
        if src <= 0 or dst <= 0 or src + dst != row["total_tokens"] \
                or (row["answer"] == row["label"]) != row["correct"]:
            p.error(f"Invalid token counts or score: {key(row)}.")

    def estimate(row):
        if row["kind"] == "text":
            return row["prompt_tokens"] / 3.233 + row["completion_tokens"] / 1.837
        return row["token_time_uno_ref_s"]

    if args.csv:
        fields = ("id", "language", "level", "kind", "label", "answer", "correct", "prompt_tokens",
                  "completion_tokens", "total_tokens", "wall_s", "mac_over_60_s",
                  "uno_estimate_s", "uno_estimate_over_60_s")
        with args.csv.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in sorted(rows, key=key):
                seconds = estimate(row)
                writer.writerow({**row, "mac_over_60_s": row["wall_s"] > 60,
                                 "uno_estimate_s": round(seconds, 4),
                                 "uno_estimate_over_60_s": seconds > 60})

    def render(label, group):
        times = [r["wall_s"] for r in group]
        valid = sum(r["answer"] in "ABCDE" for r in group if r["answer"])
        hit = sum(r["correct"] for r in group)
        d_rate = sum(r["label"] == "D" for r in group)
        prompt = statistics.median(r["prompt_tokens"] for r in group)
        response = statistics.median(r["completion_tokens"] for r in group)
        late = sum(t > 60 for t in times)
        estimates = [estimate(r) for r in group]
        print(f"| {label} | {hit}/{len(group)} ({hit/len(group):.1%}) | {d_rate} | {valid} | "
              f"{prompt:g} / {response:g} | {statistics.mean(times):.2f} / {statistics.median(times):.2f} / "
              f"{p95(times):.2f} / {max(times):.2f} | {late} | "
              f"{statistics.median(estimates):.1f} s / {sum(t > 60 for t in estimates)} |")

    for kind, label in (("text", "**DeepSeek R1 1.5B IQ3_M — text**"),
                        ("image", "Qwen3.5 0.8B Q8_0 — original scan")):
        print(f"#### {label}\n")
        print("| Group | Correct | Always D | Valid answers | Median in / out tokens | "
              "Mac mean / p50 / p95 / max (s) | Mac >60 s | UNO Q est. p50 / count >60 s |")
        print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        group = groups[kind]
        render("All", group)
        for lang in ("cat", "eng", "esp"):
            render(lang, [r for r in group if r["language"] == lang])
        for level in range(8):
            render(f"Level {level}", [r for r in group if r["level"] == level])
        print("\nCorrect by level and language (10 questions per cell):\n")
        print("| Level | Catalan | English | Spanish |\n| --- | ---: | ---: | ---: |")
        for level in range(8):
            counts = [f"{sum(r['correct'] for r in group if r['level'] == level and r['language'] == lang)}/"
                      f"{cells[level, lang, kind]}" for lang in ("cat", "eng", "esp")]
            print(f"| {level} | {' | '.join(counts)} |")
        print()


if __name__ == "__main__":
    main()
