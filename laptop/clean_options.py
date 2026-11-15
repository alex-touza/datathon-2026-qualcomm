#!/usr/bin/env python3
"""Clean the OCR noise in the option values the tutor shows.

The options are read from the picture by `prepare_m3kang.py`, and OCR leaves a
few defects behind that a student sees on the page:

- the parenthesis after a letter read as a one: the picture says "B) 15/6/2055"
  and the row says "1 15/6/2055";
- runs of spaces, and a stray replacement character where a "²" was decoded;
- boxes read as junk: a Chinese character in an English problem, "735186 = 一",
  "42 4m" or a lone "Q".

This script rewrites the option values of `data/train.jsonl` and
`data/val.jsonl`, and removes the whole option row (`"options": {}`) when a value
is still junk afterwards, so the page falls back to the picture with the five
letter buttons instead of showing nonsense. It also fixes the same noise in the
statement text.

    python3 laptop/clean_options.py                     # report only
    python3 laptop/clean_options.py --apply             # rewrite the jsonl files

Then rebuild the index on the board so the page serves the cleaned rows:

    python3 build_index.py --input data/train.jsonl --db data/index.db --only-with-options
"""
import argparse
import json
import re
import sys

try:  # the report prints the junk values, and a Windows console cannot show them
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FALSE_PARENTHESIS = re.compile(r"^1\s+(?=[\d(\[.])")
NON_LATIN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
ODD_SYMBOLS = re.compile(r"[=\ufffd]")
MAX_LENGTH = 24


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", nargs="*", default=["data/train.jsonl", "data/val.jsonl"])
    parser.add_argument("--apply", action="store_true", help="rewrite the files in place")
    parser.add_argument("--limit", type=int, default=8, help="examples to print per kind")
    return parser.parse_args()


def clean_text(text):
    """The statement: collapse the OCR spacing, keep the words as they are."""
    return " ".join(str(text or "").split())


def clean_value(text):
    """One option value: drop the false parenthesis and the stray whitespace."""
    value = " ".join(str(text or "").split()).replace("\ufffd", "")
    value = FALSE_PARENTHESIS.sub("", value)
    return value.strip(" )")


def is_junk(value):
    """True when the value cannot be an answer, whatever the problem is."""
    if not value:
        return True
    if len(value) > MAX_LENGTH:
        return True
    if NON_LATIN.search(value) or ODD_SYMBOLS.search(value):
        return True
    # "42 4m", "12 m 1m": two numbers and a unit glued together by a bad box.
    if re.fullmatch(r"\S+\s+\S+\s+\S+", value):
        return True
    if not re.search(r"[\w\d]", value):
        return True
    return False


def main():
    args = parse_args()
    fixed_values = 0
    dropped_rows = 0
    fixed_texts = 0
    examples = {"fixed": [], "dropped": [], "text": []}
    total_rows = 0

    for path in args.input:
        rows = []
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        rows.append(json.loads(line))
        except FileNotFoundError:
            print(f"{path}: not found, skipped", file=sys.stderr)
            continue

        for row in rows:
            total_rows += 1
            text = clean_text(row.get("text"))
            if text != (row.get("text") or ""):
                fixed_texts += 1
                if len(examples["text"]) < args.limit:
                    examples["text"].append((row.get("id"), (row.get("text") or "")[:60], text[:60]))
                row["text"] = text

            options = row.get("options") or {}
            if not options:
                continue
            cleaned = {letter: clean_value(value) for letter, value in options.items()}
            changed = cleaned != options
            junk = any(is_junk(value) for value in cleaned.values())
            if junk:
                dropped_rows += 1
                if len(examples["dropped"]) < args.limit:
                    examples["dropped"].append((row.get("id"), options, "-> {}"))
                row["options"] = {}
            elif changed:
                fixed_values += sum(1 for letter in options if cleaned[letter] != options[letter])
                if len(examples["fixed"]) < args.limit:
                    examples["fixed"].append((row.get("id"), options, cleaned))
                row["options"] = cleaned

        if args.apply:
            with open(path, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"rows read: {total_rows}")
    print(f"statements with OCR spacing fixed: {fixed_texts}")
    print(f"option values cleaned: {fixed_values}")
    print(f"option rows dropped as junk: {dropped_rows}")
    for kind, rows in examples.items():
        if not rows:
            continue
        print(f"\n{kind}:")
        for row in rows:
            print("  ", row)
    if not args.apply:
        print("\nreport only: add --apply to rewrite the files")


if __name__ == "__main__":
    main()
