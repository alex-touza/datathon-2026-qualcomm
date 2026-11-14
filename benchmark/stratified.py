#!/usr/bin/env python3
"""Stage candidates and export an image-reviewed, stratified M3Kang pilot."""

import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import shutil

from PIL import Image
import pyarrow.parquet as pq

if __package__:
    from .prepare import LANGUAGES, REVISION, LETTERS, parse_options
else:
    from prepare import LANGUAGES, REVISION, LETTERS, parse_options

LEVELS = range(8)
FILES = [f"{split}-{i:05d}-of-{count:05d}.parquet"
         for split, count in (("high_quality", 4), ("standard", 10)) for i in range(count)]
METADATA = ["id", "language", "level", "text_only", "label", "text"]


def write_jsonl(path, rows):
    pending = path.with_suffix(".tmp")
    pending.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                       encoding="utf-8")
    pending.replace(path)


def stage(cache, excluded_file, output, seed, per_kind, per_slot):
    files = [cache / name for name in FILES]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise ValueError(f"Missing pinned Parquet shards: {', '.join(missing)}")
    excluded = {json.loads(line)["id"] for line in excluded_file.read_text().splitlines()}
    buckets = defaultdict(dict)
    for path in files:
        table = pq.read_table(path, columns=METADATA, filters=[("language", "in", list(LANGUAGES))])
        for row in table.to_pylist():
            if row["id"] in excluded or row["level"] not in LEVELS:
                continue
            if not row["text"].strip() or row["label"] not in set(LETTERS):
                continue
            key = (row["level"], row["text_only"], row["language"])
            buckets[key].setdefault(row["id"], path)
    picked = []
    used_ids = set()
    for level in LEVELS:
        for modality, plain in enumerate((True, False)):
            for offset in range(per_kind):
                slot_number = (level * 2 + modality) * per_kind + offset
                language = LANGUAGES[slot_number % len(LANGUAGES)]
                bucket = buckets[level, plain, language]
                ranked_ids = sorted((problem_id for problem_id in bucket if problem_id not in used_ids),
                                    key=lambda problem_id: hashlib.sha256(
                                        f"{seed}|{slot_number}|{problem_id}".encode()
                                    ).digest())
                if len(ranked_ids) < per_slot:
                    raise ValueError(f"Too few candidates for level {level}, slot {slot_number}")
                for rank, problem_id in enumerate(ranked_ids[:per_slot]):
                    picked.append((slot_number, rank, problem_id, language, bucket[problem_id]))
                    used_ids.add(problem_id)
    by_shard = defaultdict(set)
    for _, _, problem_id, language, path in picked:
        by_shard[path].add((problem_id, language))
    sources = {}
    for path, keys in by_shard.items():
        table = pq.read_table(path, columns=[*METADATA, "image"], filters=[
            ("id", "in", sorted({key[0] for key in keys})),
            ("language", "in", sorted({key[1] for key in keys})),
        ])
        for row in table.to_pylist():
            key = row["id"], row["language"]
            if key in keys:
                sources.setdefault((path, *key), row)
    (output / "images").mkdir(parents=True, exist_ok=True)
    rows = []
    for slot, rank, problem_id, language, path in picked:
        row = sources[path, problem_id, language].copy()
        original = row.pop("image")["bytes"]
        relative = f"images/{problem_id}_{language}.png"
        with Image.open(io.BytesIO(original)) as image:
            image.convert("RGB").save(output / relative)
        rows.append({**row, "text": row["text"].strip(), "slot": slot, "rank": rank,
                     "image": relative, "shard": path.name,
                     "image_sha256": hashlib.sha256((output / relative).read_bytes()).hexdigest()})
    write_jsonl(output / "candidates.jsonl", rows)
    config = {"revision": REVISION, "seed": seed, "per_kind": per_kind,
              "candidates_per_slot": per_slot}
    (output / "sampling.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def export(staging, reviewed, output):
    from rapidocr_onnxruntime import RapidOCR

    candidates = {(r["slot"], r["rank"]): r for r in map(json.loads, (
        staging / "candidates.jsonl").read_text(encoding="utf-8").splitlines())}
    config = json.loads((staging / "sampling.json").read_text(encoding="utf-8"))
    selected = {}
    used_ids = set()
    for line in reviewed.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        slot, rank, kind, digest, *choices = line.split("\t")
        slot, rank = int(slot), int(rank)
        row = candidates[slot, rank]
        if digest != row["image_sha256"]:
            raise ValueError(f"Candidate image fingerprint changed in slot {slot}")
        if slot in selected or row["id"] in used_ids:
            raise ValueError(f"Repeated slot or problem ID: {slot}, {row['id']}")
        if kind not in ("text", "figure", "graphic") or row["text_only"] != (kind == "text"):
            raise ValueError(f"Incorrect reviewed kind for slot {slot}")
        values = choices[0].split("|") if choices else []
        if len(values) != (0 if kind == "graphic" else 5) or any(not v for v in values):
            raise ValueError(f"Invalid reviewed options in slot {slot}")
        selected[slot] = (row, kind, dict(zip(LETTERS, values)))
        used_ids.add(row["id"])
    if set(selected) != set(range(len(LEVELS) * 2 * config["per_kind"])):
        raise ValueError("The reviewed manifest must cover every stratum slot")
    (output / "images").mkdir(parents=True, exist_ok=True)
    ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    rows = []
    for slot, (candidate, kind, reviewed_options) in sorted(selected.items()):
        row = {name: candidate[name] for name in (*METADATA, "image")}
        image_path = staging / row["image"]
        image_bytes = image_path.read_bytes()
        if hashlib.sha256(image_bytes).hexdigest() != candidate["image_sha256"]:
            raise ValueError(f"Staged image changed: {image_path}")
        boxes, _ = ocr(image_bytes)
        shutil.copyfile(image_path, output / row["image"])
        rows.append({**row, "dataset_origin": "m3kang", "kind": kind,
                     "options": parse_options(boxes or []), "reviewed_options": reviewed_options})
        print(f"Prepared {slot + 1}/{len(selected)}: {row['id']}/{row['language']}", flush=True)
    write_jsonl(output / "pilot.jsonl", rows)
    shutil.copyfile(reviewed, output / "reviewed.tsv")
    config["coverage"] = dict(sorted(Counter(
        f"{row['level']}:{'text' if row['text_only'] else 'image'}" for row in rows
    ).items()))
    config["shards"] = dict(sorted(Counter(
        choice[0]["shard"] for choice in selected.values()
    ).items()))
    (output / "sampling.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--cache", type=Path, default=Path.home() / (
        f".cache/huggingface/hub/datasets--qualcomm--M3Kang/snapshots/{REVISION}/data"))
    stage_parser.add_argument("--exclude", type=Path, required=True)
    stage_parser.add_argument("--out", type=Path, required=True)
    stage_parser.add_argument("--seed", type=int, default=42)
    stage_parser.add_argument("--per-kind", type=int, default=2)
    stage_parser.add_argument("--per-slot", type=int, default=3)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--staging", type=Path, required=True)
    export_parser.add_argument("--reviewed", type=Path, required=True)
    export_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "stage":
        if args.per_kind < 1 or args.per_slot < 1:
            parser.error("--per-kind and --per-slot must be positive")
        stage(args.cache, args.exclude, args.out, args.seed, args.per_kind, args.per_slot)
    else:
        export(args.staging, args.reviewed, args.out)


if __name__ == "__main__":
    main()
