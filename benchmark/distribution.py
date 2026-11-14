#!/usr/bin/env python3
"""Load only metadata from the pinned Parquet files and run distribution.sql."""
import argparse
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq

REVISION = "e772e611d45679cf48f1c92618b2963a40d1d584"
COLUMNS = ["id", "language", "level", "text_only"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / (
        f".cache/huggingface/hub/datasets--qualcomm--M3Kang/snapshots/{REVISION}/data"
    ))
    args = parser.parse_args()
    files = sorted(args.cache.glob("*.parquet"))
    if len(files) != 14:
        parser.error(f"Expected all 14 Parquet files in {args.cache}, found {len(files)}")
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE problems(split TEXT, id TEXT, language TEXT, level INTEGER, text_only INTEGER)")
        for path in files:
            split = path.name.split("-", 1)[0]
            for batch in pq.ParquetFile(path).iter_batches(columns=COLUMNS):
                columns = [value.to_pylist() for value in batch.columns]
                db.executemany("INSERT INTO problems VALUES (?,?,?,?,?)",
                               ((split, *row) for row in zip(*columns)))
        queries = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
        for query in queries.split(";"):
            if not query.strip():
                continue
            heading = query.strip().splitlines()[0].removeprefix("-- ")
            cursor = db.execute(query)
            print(f"[{heading}]")
            print("\t".join(column[0] for column in cursor.description))
            for row in cursor:
                print("\t".join(str(value) for value in row))
            print()


if __name__ == "__main__":
    main()
