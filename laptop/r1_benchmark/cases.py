#!/usr/bin/env python3
"""Select a balanced, parallel-language M3Kang benchmark from the local cache."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

REVISION = "e772e611d45679cf48f1c92618b2963a40d1d584"
CACHE = Path.home() / f".cache/huggingface/hub/datasets--qualcomm--M3Kang/snapshots/{REVISION}/data"
LANGUAGES = ("cat", "eng", "esp")
LETTERS = set("ABCDE")
FIELDS = ("id", "level", "text_only", "language", "text", "label")
NUMERIC = re.compile(r"[-+0-9 .,/%:=×÷−()²³°]+")


def jsonl(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def main():
    import pyarrow.parquet as pq

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=CACHE, help="Pinned high_quality parquet directory.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Directory containing train.jsonl and val.jsonl with OCR options.")
    parser.add_argument("--out", type=Path, default=Path("results/r1-mac/input"))
    parser.add_argument("--per-cell", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261115)
    args = parser.parse_args()
    if args.per_cell < 1:
        parser.error("--per-cell must be positive")
    shards = sorted(args.source.glob("high_quality-*.parquet"))
    if len(shards) != 4 or not all(shard.is_file() for shard in shards):
        parser.error(f"Expected four local high_quality shards in {args.source}")

    options = {}
    for name in ("train.jsonl", "val.jsonl"):
        for row in jsonl(args.data_dir / name):
            choices = row.get("options", {})
            if choices.keys() == LETTERS:
                options[row["id"], row["language"]] = {k: str(v).strip() for k, v in sorted(choices.items())}

    variants = defaultdict(dict)
    for shard in shards:
        for row in pq.read_table(shard, columns=list(FIELDS),
                                 filters=[("language", "in", list(LANGUAGES))]).to_pylist():
            if row["label"] in LETTERS and (row["text"] or "").strip():
                variants[row["level"], row["text_only"], row["id"]].setdefault(row["language"], (shard, row))

    eligible = defaultdict(list)
    for (level, text_only, ident), group in variants.items():
        if set(group) != set(LANGUAGES):
            continue
        if len({row["label"] for _, row in group.values()}) != 1:
            continue  # ensure answer letters align across the three variants
        if text_only:
            eng_options = options.get((ident, "eng"))
            if (not eng_options or eng_options != options.get((ident, "cat"))
                    or len(set(eng_options.values())) != 5
                    or not all(NUMERIC.fullmatch(v) and any(c.isdigit() for c in v)
                               for v in eng_options.values())):
                continue  # share only identical, language-independent options
        eligible[level, text_only].append(ident)

    def rank(ident):
        return hashlib.sha256(f"{args.seed}:{ident}".encode()).digest()

    manifest = []
    images_by_shard = defaultdict(dict)
    for level in range(8):
        for text_only in (True, False):
            ids = sorted(eligible[level, text_only], key=rank)[:args.per_cell]
            if len(ids) != args.per_cell:
                parser.error(f"Only {len(ids)} eligible paired questions for level {level}, text_only={text_only}")
            for ident in ids:
                for lang in LANGUAGES:
                    shard, row = variants[level, text_only, ident][lang]
                    out = {"id": ident, "language": lang, "level": level, "label": row["label"],
                           "kind": "text" if text_only else "image"}
                    if text_only:
                        out.update(text=row["text"], options=options[ident, "eng"])
                    else:
                        images_by_shard[shard][ident, lang] = out
                    manifest.append(out)

    (args.out / "images").mkdir(parents=True, exist_ok=True)
    for shard, selected in images_by_shard.items():
        filters = [("language", "in", list(LANGUAGES)), ("id", "in", sorted({ident for ident, _ in selected}))]
        for row in pq.read_table(shard, columns=["id", "language", "image"], filters=filters).to_pylist():
            out = selected.get((row["id"], row["language"]))
            if out is None or "image" in out:
                continue
            blob = row["image"]["bytes"]
            suffix = ".jpg" if blob.startswith(b"\xff\xd8") else ".png" if blob.startswith(b"\x89PNG\r\n\x1a\n") else ""
            if not suffix:
                raise ValueError(f"Unrecognized scan format for {row['id']} / {row['language']}")
            path = Path("images") / f"{row['id']}_{row['language']}{suffix}"
            (args.out / path).write_bytes(blob)
            out.update(image=path.as_posix(), image_sha256=hashlib.sha256(blob).hexdigest())

    counts = Counter((row["level"], row["language"], row["kind"]) for row in manifest)
    assert len(counts) == 48 and set(counts.values()) == {args.per_cell}
    assert all("image" in row for row in manifest if row["kind"] == "image")
    target = args.out / "cases.jsonl"
    temp = target.with_suffix(".tmp")
    temp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in manifest), encoding="utf-8")
    temp.replace(target)
    print(f"{target}: {len(manifest)} cases, {len(counts)} cells × {args.per_cell}")


if __name__ == "__main__":
    main()
