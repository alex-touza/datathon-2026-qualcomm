#!/usr/bin/env python3
"""Summarize benchmark JSONL files by language, modality, and school level."""
import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def load_results(path):
    records = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if row.get("record_type") == "result":
                key = (row["id"], row["language"])
                if key in records:
                    raise ValueError(f"Duplicate result for {key} in {path}")
                records[key] = row
    if not records:
        raise ValueError(f"No result rows in {path}")
    return records


def describe(rows):
    times = sorted(row["seconds"] for row in rows)
    return {
        "n": len(rows),
        "correct": sum(row.get("correct") is True for row in rows),
        "correct_60": sum(row.get("correct") is True and row.get("within_60", row["seconds"] <= 60) for row in rows),
        "correct_100": sum(row.get("correct") is True and row.get("within_100", row["seconds"] <= 100) for row in rows),
        "median_s": round(statistics.median(times), 3),
        "p95_s": round(times[math.ceil(0.95 * len(times)) - 1], 3),
        "max_s": round(times[-1], 3),
        "statuses": dict(Counter(row["status"] for row in rows)),
    }


def summarize(records):
    rows = list(records.values())
    result = {"all": describe(rows)}
    for field in ("language", "kind", "level"):
        groups = defaultdict(list)
        for row in rows:
            groups[str(row.get(field))].append(row)
        result[field] = {name: describe(group) for name, group in sorted(groups.items())}
    by_level_and_modality = defaultdict(list)
    for row in rows:
        kind = row.get("kind")
        if kind in ("text", "figure", "graphic", "image"):
            modality = "text" if kind == "text" else "image"
            by_level_and_modality[(row.get("level"), modality)].append(row)
    result["level_and_modality"] = {
        f"{level}/{modality}": describe(group)
        for (level, modality), group in sorted(by_level_and_modality.items(), key=lambda entry: str(entry[0]))
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()
    suites = {path.stem: load_results(path) for path in args.results}
    if len(suites) != len(args.results):
        parser.error("Results must have distinct filenames")
    for name, records in suites.items():
        print(json.dumps({"run": name, **summarize(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
