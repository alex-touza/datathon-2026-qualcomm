#!/usr/bin/env python3
"""Build the reviewed M3Kang pilot from a local research-dataset shard."""

import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import sys

from PIL import Image
import pyarrow.parquet as pq
from rapidocr_onnxruntime import RapidOCR

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from laptop.prepare_m3kang import LETTERS, parse_options

REVISION = "e772e611d45679cf48f1c92618b2963a40d1d584"
SHARD = "high_quality-00000-of-00004.parquet"
SHARD_SHA256 = "bfe7daf8eea5d7ab1bb6e89e095897a7c5c3956734b4e6b2cc8b5737873052bb"
LANGUAGES = ("eng", "cat", "esp")


def load_manifest(path):
    reviewed = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        problem_id, kind, image_digests, *option_field = line.split("\t")
        choices = option_field[0].split("|") if option_field else []
        digests = image_digests.split("|")
        if problem_id in reviewed or kind not in {"text", "figure", "graphic"}:
            raise ValueError(f"Invalid or duplicate manifest entry: {problem_id}")
        expected_choices = 0 if kind == "graphic" else len(LETTERS)
        if len(choices) != expected_choices or any(not choice for choice in choices):
            raise ValueError(f"Invalid reviewed choices: {problem_id}")
        if len(digests) != len(LANGUAGES):
            raise ValueError(f"Missing reviewed image hashes: {problem_id}")
        reviewed[problem_id] = (kind, dict(zip(LANGUAGES, digests)), dict(zip(LETTERS, choices)))
    if not reviewed:
        raise ValueError("The pilot manifest is empty")
    return reviewed


def build(shard, out, reviewed):
    rows = pq.read_table(
        shard,
        columns=["id", "language", "level", "text_only", "label", "text", "image"],
        filters=[("id", "in", list(reviewed)), ("language", "in", list(LANGUAGES))],
    ).to_pylist()
    grouped = defaultdict(dict)
    for row in rows:
        problem_id, language = row["id"], row["language"]
        kind, digests, _ = reviewed[problem_id]
        if language in grouped[problem_id]:
            raise ValueError(f"Duplicate row: {problem_id}/{language}")
        if row["text_only"] != (kind == "text") or row["label"] not in set(LETTERS):
            raise ValueError(f"Unexpected row metadata: {problem_id}/{language}")
        if hashlib.sha256(row["image"]["bytes"]).hexdigest() != digests[language]:
            raise ValueError(f"Image differs from reviewed source: {problem_id}/{language}")
        grouped[problem_id][language] = row
    for problem_id in reviewed:
        group = grouped[problem_id]
        if group.keys() != set(LANGUAGES):
            raise ValueError(f"Missing translations: {problem_id}")
        if len({row["label"] for row in group.values()}) != 1:
            raise ValueError(f"Labels differ between translations: {problem_id}")

    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    prepared = []
    for problem_id, (kind, _, choices) in reviewed.items():
        for language in LANGUAGES:
            row = grouped[problem_id][language]
            source_image = row.pop("image")["bytes"]
            boxes, _ = ocr(source_image)
            row["options"] = parse_options(boxes or [])
            row["reviewed_options"] = choices
            row["kind"] = kind
            row["dataset_origin"] = "m3kang"
            row["text"] = row["text"].strip()
            row["image"] = f"images/{problem_id}_{language}.png"
            with Image.open(io.BytesIO(source_image)) as original:
                original.convert("RGB").save(out / row["image"])
            prepared.append(row)
    pending = out / "pilot.jsonl.tmp"
    pending.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in prepared),
        encoding="utf-8",
    )
    pending.replace(out / "pilot.jsonl")
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, help="path to the cached high_quality shard 0")
    parser.add_argument("--reviewed", type=Path, required=True, help="private reviewed pilot TSV")
    parser.add_argument("--out", type=Path, required=True, help="private destination directory")
    args = parser.parse_args()
    shard = args.parquet or Path.home() / (
        f".cache/huggingface/hub/datasets--qualcomm--M3Kang/snapshots/{REVISION}/data/{SHARD}"
    )
    if not shard.is_file():
        parser.error(f"Cannot find {shard}. Provide a local shard with --parquet.")
    with shard.open("rb") as source:
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != SHARD_SHA256:
        parser.error(f"Expected {SHARD} from revision {REVISION}.")
    prepared = build(shard, args.out, load_manifest(args.reviewed))
    print(f"Wrote {len(prepared)} rows to {args.out / 'pilot.jsonl'}")
    for kind, count in sorted(Counter(row["kind"] for row in prepared).items()):
        print(f"  {kind}: {count} rows")


if __name__ == "__main__":
    main()
