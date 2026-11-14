#!/usr/bin/env python3
"""Recover the options that OCR missed, and audit the ones it claims to have read.

About 37% of M3Kang problems come back with `"options": {}`. Many of those have
pictures for options and need a vision model. But a large share are ordinary
text options that `parse_options` rejected because one of the five markers was
lost: `C) 4` merged into the question line, or `D` and its value came back as
two separate boxes, or the option row was cut by the crop. `parse_options`
requires all five in order, so one bad box throws away the whole row.

This script:

1. re-reads every image with no options from a set of bottom crops (where the
   option row lives), with a looser marker parser as a fallback;
2. joins the two language versions of the same problem, which show the same
   options: if one read is missing a letter and the other has it, and every
   letter they share agrees, the missing one is filled in;
3. optionally audits N already-read rows by re-reading them, so the error rate
   of the existing 62% is a measured number and not an assumption.

    python3 laptop/repair_options.py --scope all --audit 300      # report only
    python3 laptop/repair_options.py --scope all --apply          # write the jsonl
"""
import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

LETTERS = "ABCDE"
# (name, fraction of the image height where the crop starts, upscale factor)
VARIANTS = (("bottom45", 0.55, 1), ("bottom30", 0.70, 1), ("bottom20", 0.80, 2),
            ("bottom60", 0.40, 1), ("full", 0.00, 1))

LOOSE = re.compile(r"^[\(\[]?\s*([A-E])\s*[\)\].:]\s*(.*)$")
BARE = re.compile(r"^[\(\[]?\s*([A-E])\s*[\)\].:]?$")
GLUED = re.compile(r"^([A-E])\s*([-+−€$]?\s*\d.*)$")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", nargs="*", default=["data/train.jsonl", "data/val.jsonl"])
    p.add_argument("--images", default="data/images")
    p.add_argument("--out", default="data/options_repair.jsonl")
    p.add_argument("--scope", choices=("text-only", "all"), default="all",
                   help="which empty rows to try: only text_only problems, or all of them")
    p.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    p.add_argument("--limit", type=int, default=0, help="only the first N images (for testing)")
    p.add_argument("--audit", type=int, default=0, help="re-read N already-filled rows and report agreement")
    p.add_argument("--min-letters", type=int, default=5, choices=(4, 5),
                   help="apply only reads with this many option letters (4 also writes partial rows)")
    p.add_argument("--apply", action="store_true", help="also rewrite the --input files in place")
    return p.parse_args()


OPTION_LABEL = re.compile(r"[A-E]\s*[\)\].:]")


def is_noise_value(value):
    """True when a "value" is really a drawn option label, not text.

    Picture-option problems often draw the letters A..E inside the figure. A OCR
    reading of those is not an option list: it would tell the model
    `A) A, B) B, ...`, which is worse than no options at all.
    """
    v = value.strip()
    if re.fullmatch(r"[A-Ea-e]", v):
        return True
    if OPTION_LABEL.search(v):
        return True
    return False


def looks_like_options(options):
    """Reject parses that are technically five letters but obviously noise."""
    if len(options) != 5 or sorted(options) != list(LETTERS):
        return False
    values = list(options.values())
    if any(not v or len(v) > 28 or is_noise_value(v) for v in values):
        return False
    if len(set(values)) != len(values):  # Kangaroo options are distinct
        return False
    return True


def looks_like_partial(options):
    """For the loose parser: at least A, D and E, at most one letter missing."""
    if not 4 <= len(options) <= 5:
        return False
    if "A" not in options or "E" not in options:
        return False
    values = list(options.values())
    return (all(v and len(v) <= 28 and not is_noise_value(v) for v in values)
            and len(set(values)) == len(values))


def _marker(text):
    """(letter, value) if this box starts an option, with or without punctuation."""
    t = text.strip()
    m = LOOSE.match(t)
    if m:
        return m.group(1), m.group(2).strip()
    m = BARE.match(t)
    if m:
        return m.group(1), ""
    m = GLUED.match(t)
    if m:
        return m.group(1), m.group(2).strip()
    return None


def parse_loose(items):
    """Like parse_options, but tolerates a missing marker and a bare letter.

    Only the last A..E run counts, E must be present, and a letter may go
    missing, so a row that lost its C still yields four usable options. Values
    split across boxes are joined, the way parse_options does it.
    """
    for start in reversed([i for i, it in enumerate(items) if (_marker(it["text"]) or ("",))[0] == "A"]):
        options, current, i = {}, None, start
        while i < len(items):
            marker = _marker(items[i]["text"])
            if marker:
                letter, value = marker
                if letter in options:  # a second A..E run: this one is over
                    break
                if not value and i + 1 < len(items) and not _marker(items[i + 1]["text"]):
                    value = items[i + 1]["text"].strip()
                    i += 1
                options[letter] = value
                current = letter
            elif current:
                options[current] = (options[current] + " " + items[i]["text"]).strip()
            i += 1
        if looks_like_partial(options):
            return {k: v.replace("∈", "€").strip() for k, v in options.items()}
    return {}


def _init_worker():
    global _OCR
    from rapidocr_onnxruntime import RapidOCR
    _OCR = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)


def _read_one(job):
    """Try every crop until two of them agree on the same five options.

    For problems marked `text_only: false` the options are usually drawn, so
    only the strict parser runs and two crops must agree: a lone loose read of
    a figure is how you get `A) A, B) B, ...` into the prompt.
    """
    key, path, strict_only = job
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from PIL import Image
    import numpy as np
    from prepare_m3kang import parse_options, reading_order

    try:
        image = Image.open(path).convert("RGB")
    except OSError as error:
        return {"key": key, "error": str(error)}

    parses = {}
    for name, start, scale in VARIANTS:
        band = image.crop((0, int(image.height * start), image.width, image.height))
        factor = scale if scale > 1 else (2 if band.width < 1100 else 1)
        if factor > 1:
            band = band.resize((band.width * factor, band.height * factor), Image.LANCZOS)
        boxes, _ = _OCR(np.array(band))
        options = parse_options(boxes or [])
        if not looks_like_options(options):
            # The strict parser also returns five "options" for a figure that
            # draws its labels as A) A, B) B, ...; those are noise, not options.
            options = {} if strict_only else parse_loose(reading_order(boxes or []))
        if not options:
            continue
        parses[name] = options
        if len(parses) >= 2 and len({tuple(sorted(p.items())) for p in parses.values()}) == 1:
            return {"key": key, "options": parses[name], "variant": name, "confirmed": True}

    if parses and not strict_only:
        votes = Counter(tuple(sorted(p.items())) for p in parses.values())
        best, hits = votes.most_common(1)[0]
        name = next(n for n, p in parses.items() if tuple(sorted(p.items())) == best)
        return {"key": key, "options": dict(best), "variant": name,
                "confirmed": hits >= 2 and len(best) == 5}
    return {"key": key, "options": {}}


def _audit_one(job):
    """Re-read an already-filled row from its most likely crop and compare."""
    key, path, stored = job
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from PIL import Image
    import numpy as np
    from prepare_m3kang import parse_options, reading_order

    try:
        image = Image.open(path).convert("RGB")
    except OSError as error:
        return {"key": key, "stored": stored, "reread": {}, "error": str(error)}

    for _name, start, _scale in VARIANTS[:3]:
        band = image.crop((0, int(image.height * start), image.width, image.height))
        boxes, _ = _OCR(np.array(band))
        options = parse_options(boxes or []) or parse_loose(reading_order(boxes or []))
        if looks_like_options(options):
            return {"key": key, "stored": stored, "reread": options}
    return {"key": key, "stored": stored, "reread": {}}


def twin_merge(rows):
    """Fill a missing letter from the same problem in the other language.

    The image shows the same options in both languages; for text options the
    translations differ, so shared letters must match exactly before the join
    is allowed. That keeps the rule safe: only a numeric or symbolic option set
    can be completed this way.
    """
    by_id = defaultdict(list)
    for row in rows:
        by_id[row["id"]].append(row)
    filled = {}
    for pid, group in by_id.items():
        if len(group) < 2:
            continue
        partial = [r for r in group if 0 < len(r.get("options") or {}) < 5]
        full = [r for r in group if len(r.get("options") or {}) == 5]
        if not partial or not full:
            continue
        for p in partial:
            for f in full:
                shared = set(p["options"]) & set(f["options"])
                if len(shared) < 3:
                    continue
                if all(p["options"][l] == f["options"][l] for l in shared):
                    filled[f"{p['id']}|{p['language']}"] = dict(f["options"])
                    break
    return filled


def coverage_table(rows, label):
    per = defaultdict(lambda: [0, 0])
    for r in rows:
        per[r["language"]][1] += 1
        per[r["language"]][0] += bool(r["options"])
    total = [sum(v[0] for v in per.values()), sum(v[1] for v in per.values())]
    cells = "  ".join(f"{lang}: {a}/{b} ({a / b:.0%})" for lang, (a, b) in sorted(per.items()))
    print(f"  {label:10} {cells}  | all {total[0]}/{total[1]} ({total[0] / total[1]:.1%})")


def main():
    args = parse_args()
    if args.workers < 1:
        sys.exit("--workers must be at least 1")

    rows = []
    for path in args.input:
        with open(path, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f if line.strip())

    todo, seen = [], set()
    for row in rows:
        key = f"{row['id']}|{row['language']}"
        if row.get("options") or key in seen:
            continue
        if args.scope == "text-only" and not row.get("text_only"):
            continue
        image = os.path.join(args.images, key.replace("|", "_") + ".png")
        if not os.path.exists(image):
            continue
        seen.add(key)
        todo.append((key, image, not row.get("text_only")))
    if args.limit:
        todo = todo[:args.limit]

    print(f"Coverage now:")
    coverage_table(rows, "before")
    print(f"{len(todo)} images with no options to re-read "
          f"({args.scope}); {len(rows)} rows in {', '.join(args.input)}", flush=True)

    results = []
    if todo:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("spawn"),
                                 initializer=_init_worker) as pool:
            for done, result in enumerate(pool.map(_read_one, todo, chunksize=4), 1):
                results.append(result)
                if done % 100 == 0:
                    print(f"  {done}/{len(todo)}, recovered {sum(1 for r in results if r['options'])}",
                          flush=True)

    found = {r["key"]: r for r in results if r["options"]}
    full = {k: v["options"] for k, v in found.items() if looks_like_options(v["options"])}
    partial = {k: v["options"] for k, v in found.items()
               if k not in full and looks_like_partial(v["options"])}

    # Fill the gaps from the other language of the same problem.
    probe = [dict(r) for r in rows]
    for row in probe:
        key = f"{row['id']}|{row['language']}"
        if key in partial:
            row["options"] = partial[key]
    merged = twin_merge(probe)
    for key, options in merged.items():
        if key not in full:
            full[key] = options
            partial.pop(key, None)

    patch = {**full, **{k: v for k, v in partial.items() if k not in merged}}
    if args.min_letters == 5:
        patch = {k: v for k, v in patch.items() if len(v) == 5}
    confirmed = sum(1 for k in full if found.get(k, {}).get("confirmed"))
    variants = Counter(found[k].get("variant", "") for k in full if k in found)

    with open(args.out, "w", encoding="utf-8") as f:
        for key, options in sorted(patch.items()):
            f.write(json.dumps({"key": key, "options": options,
                                "variant": found.get(key, {}).get("variant", "twin"),
                                "confirmed": key in full and bool(found.get(key, {}).get("confirmed")),
                                "complete": len(options) == 5}, ensure_ascii=False) + "\n")

    after = [dict(r) for r in rows]
    for row in after:
        key = f"{row['id']}|{row['language']}"
        if key in patch:
            row["options"] = patch[key]

    print(f"\nRecovered {len(patch)}/{len(todo)}: {len(full)} complete, {len(partial)} still partial")
    print(f"  confirmed by two independent crops: {confirmed}")
    print(f"  completed by joining the other language: {len(merged)}")
    print(f"  by crop: {dict(variants)}")
    print("Coverage after:")
    coverage_table(after, "after")

    if args.audit:
        have = [r for r in rows if looks_like_options(r.get("options") or {})]
        random.Random(42).shuffle(have)
        sample = have[:args.audit]
        jobs = [(f"{r['id']}|{r['language']}", os.path.join(args.images, f"{r['id']}_{r['language']}.png"),
                 r["options"]) for r in sample]
        jobs = [j for j in jobs if os.path.exists(j[1])]
        print(f"\nAudit: re-reading {len(jobs)} already-filled rows...", flush=True)
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        agree = disagree = unreadable = 0
        examples = []
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("spawn"),
                                 initializer=_init_worker) as pool:
            for result in pool.map(_audit_one, jobs, chunksize=4):
                if not result["reread"]:
                    unreadable += 1
                elif result["reread"] == result["stored"]:
                    agree += 1
                else:
                    disagree += 1
                    if len(examples) < 8:
                        examples.append((result["key"], result["stored"], result["reread"]))
        total = agree + disagree
        print(f"  agreement {agree}/{total} ({agree / max(total, 1):.1%}), "
              f"disagreement {disagree}, unreadable on re-read {unreadable}")
        for key, stored, reread in examples:
            print(f"  {key}: stored {stored} != re-read {reread}")

    if args.apply and patch:
        for path in args.input:
            backup = path + ".bak"
            if not os.path.exists(backup):
                with open(path, "rb") as src, open(backup, "wb") as dst:
                    dst.write(src.read())
            lines, filled = [], 0
            with open(path, encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    key = f"{row['id']}|{row['language']}"
                    if not row.get("options") and key in patch:
                        row["options"] = patch[key]
                        filled += 1
                    lines.append(json.dumps(row, ensure_ascii=False))
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"  filled {filled} rows in {path} (backup {backup})")


if __name__ == "__main__":
    main()
