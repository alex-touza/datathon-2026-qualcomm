#!/usr/bin/env python3
"""Build a new synthetic benchmark with ten text and ten visual cases per band."""
import argparse
import hashlib
import json
from math import lcm
import random
from pathlib import Path


def text_problem(level, family, rng):
    a, b, c = (rng.randint(3, 18) for _ in range(3))
    if level == 0:
        if family == 0:
            return f"A tray holds {a} apples. A second tray holds {b}. How many apples are there altogether?", a + b
        return f"There are {a + b} markers in a box. A pupil takes {a}. How many markers remain?", b
    if level == 1:
        if family == 0:
            return f"There are {a} teams with {b} players on each. How many players are there?", a * b
        return f"Share {a * b} tiles equally among {a} bags. How many tiles go into each bag?", b
    if level == 2:
        if family == 0:
            c = min(c, a * b - 1)
            return f"A van delivers {b} crates per trip for {a} trips. It brings back {c} of those crates. How many crates stay delivered?", a * b - c
        return f"A rectangle is {a} metres wide and {a + b} metres long. What is its perimeter in metres?", 2 * (2 * a + b)
    if level == 3:
        if family == 0:
            return f"For every {a} red tiles a pattern uses {b} white tiles. The pattern uses {a * c} red tiles. How many white tiles does it use?", b * c
        return f"A tank starts with {3 * a * b} litres. Add {3 * c} litres, then drain one third of the new amount. How many litres remain?", 2 * (a * b + c)
    if level == 4:
        if family == 0:
            return f"A shop has {3 * a} notebooks. It sells one third, then half of the remaining notebooks. How many are left?", a
        return f"An outer rectangle measures {a + 6} by {b + 6} metres. A rectangle measuring {a} by {b} metres is cut out. How many square metres remain?", (a + 6) * (b + 6) - a * b
    if level == 5:
        if family == 0:
            return f"The sum of three consecutive integers is {3 * (a + 1)}. What is the product of the smallest and largest?", a * (a + 2)
        return f"A class of {10 * a} pupils increases its size by 20 percent, then decreases the new size by 25 percent. What is the final class size?", 9 * a
    if level == 6:
        if family == 0:
            return f"A lock code has two distinct digits, each from 1 to {min(a, 9)}. How many codes have the first digit smaller than the second?", min(a, 9) * (min(a, 9) - 1) // 2
        return f"Two cyclists start together and complete laps every {2 * a} and {3 * b} minutes. After how many minutes will they first arrive at the start together again?", lcm(2 * a, 3 * b)
    if family == 0:
        return f"A box contains {a} red and {b} blue balls. How many unordered pairs of balls contain at least one red ball? Treat balls of the same colour as distinct.", a * b + a * (a - 1) // 2
    return f"How many integers from 1 to {12 * a + c} inclusive are divisible by 4 or 6 but not both?", (12 * a + c) // 4 + (12 * a + c) // 6 - 2 * ((12 * a + c) // 12)


def unique_text_case(level, index, rng, seen):
    family = index % 2
    while True:
        prompt, answer = text_problem(level, family, rng)
        if prompt not in seen:
            seen.add(prompt)
            break
    alternate = random.Random(41003 + 31 * level + index)
    distractors = set()
    while len(distractors) < 4:
        _, value = text_problem(level, family, alternate)
        if value != answer:
            distractors.add(value)
    return prompt, answer, sorted(distractors)


def build(output, seed):
    if __package__:
        from .levels_visual import visual_case
    else:
        from levels_visual import visual_case

    images = output / "images"
    images.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    seen = set()
    seen_visual = set()
    rows = []
    for level in range(8):
        for kind in ("text", "figure"):
            positions = list("ABCDE") * 2
            rng.shuffle(positions)
            for index, letter in enumerate(positions):
                key = f"levels-v1-{level}-{kind}-{index + 1:02d}"
                relative_image = f"images/{key}.png" if kind == "figure" else None
                if relative_image:
                    path = output / relative_image
                    prompt, answer, distractors = visual_case(level, index, rng, path)
                    image_signature = hashlib.sha256(path.read_bytes()).digest()
                    retry_rng = random.Random(f"{seed}:{key}")
                    while image_signature in seen_visual:
                        # Preserve the main draw sequence when a PNG repeats.
                        prompt, answer, distractors = visual_case(level, index, retry_rng, path)
                        image_signature = hashlib.sha256(path.read_bytes()).digest()
                    seen_visual.add(image_signature)
                else:
                    prompt, answer, distractors = unique_text_case(level, index, rng, seen)
                values = list(distractors)
                if len(values) != 4 or len(set([answer, *values])) != 5:
                    raise ValueError(f"Expected five distinct choices in {key}")
                rng.shuffle(values)
                values.insert("ABCDE".index(letter), answer)
                options = dict(zip("ABCDE", map(str, values)))
                rows.append({
                    "id": key, "language": "eng", "level": level, "kind": kind,
                    "text_only": kind == "text", "text": prompt, "options": options,
                    "reviewed_options": options, "label": letter,
                    "image": relative_image, "dataset_origin": "synthetic",
                })
    rng.shuffle(rows)
    return rows


def write_jsonl(path, rows):
    pending = path.with_suffix(".tmp")
    pending.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                       encoding="utf-8")
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261116)
    args = parser.parse_args()
    rows = build(args.out, args.seed)
    write_jsonl(args.out / "pilot.jsonl", rows)
    write_jsonl(args.out / "options-only.jsonl", [
        {**row, "text": "The problem statement is unavailable. Pick the most likely choice.", "image": None}
        for row in rows
    ])
    print(f"Wrote {len(rows)} problems with seed {args.seed} in {args.out}")


if __name__ == "__main__":
    main()
