#!/usr/bin/env python3
"""Export a local OCR-only manifest from stratified image candidates.

This keeps correct labels out of model prompts. OCR output is written to
`options`; empty OCR results stay empty. Use `--options ocr` for every model
arm so text and vision see identical choices.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys

if __package__:
    from .prepare import parse_options
else:
    from prepare import parse_options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True, help="output from stratified.py stage")
    parser.add_argument("--out", type=Path, required=True, help="private benchmark directory")
    args = parser.parse_args()
    from rapidocr_onnxruntime import RapidOCR

    candidates = [json.loads(line) for line in (args.staging / "candidates.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    config = json.loads((args.staging / "sampling.json").read_text(encoding="utf-8"))
    if len({row["id"] for row in candidates}) != len(candidates):
        raise ValueError("Candidate IDs must be unique across languages and strata")

    (args.out / "images").mkdir(parents=True, exist_ok=True)
    ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    rows = []
    complete_by_stratum = Counter()
    for index, candidate in enumerate(candidates, 1):
        source = args.staging / candidate["image"]
        image_bytes = source.read_bytes()
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest != candidate["image_sha256"]:
            raise ValueError(f"Image changed since staging: {source}")
        boxes, _ = ocr(image_bytes)
        options = parse_options(boxes or [])
        text_only = bool(candidate["text_only"])
        relative_image = candidate["image"]
        destination = args.out / relative_image
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        row = {key: candidate[key] for key in
               ("id", "language", "level", "text_only", "label", "text", "slot", "rank", "shard")}
        row.update({"dataset_origin": "m3kang", "kind": "text" if text_only else "image",
                    "image": relative_image, "image_sha256": digest, "options": options,
                    "ocr_options_complete": set(options) == set("ABCDE")})
        rows.append(row)
        complete_by_stratum[(candidate["level"], "text" if text_only else "image")] += bool(options)
        print(f"[{index}/{len(candidates)}] {candidate['id']}/{candidate['language']} "
              f"OCR={'A-E' if options else 'empty'}", flush=True)

    manifest = args.out / "pilot.jsonl"
    pending = manifest.with_suffix(".tmp")
    pending.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                       encoding="utf-8")
    pending.replace(manifest)
    metadata = {**config, "ocr": "local RapidOCR using benchmark/prepare.py parse_options",
                "unique_ids": len({row['id'] for row in rows}),
                "rows": len(rows),
                "ocr_complete": sum(bool(row["ocr_options_complete"]) for row in rows),
                "ocr_empty": sum(not row["options"] for row in rows),
                "complete_by_level_modality": {f"{level}:{modality}": count
                    for (level, modality), count in sorted(complete_by_stratum.items())}}
    (args.out / "sampling.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
