#!/usr/bin/env python3
"""Run LiquidAI d1-omni-600M on a private M3Kang JSONL pilot.

Loads the pinned local Hugging Face snapshot and calls its typed-choice API
directly. Labels are read only after inference for scoring. This is an
exploratory host benchmark, not a UNO Q performance estimate.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import time
from collections import defaultdict
from pathlib import Path

REVISION = "02b55d7076f15129e59ab3f94783f32c4b088674"
LETTERS = "ABCDE"
REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("HF_HOME", str(REPO_ROOT / "data/omni-runtime/hf-cache"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class Deadline(Exception):
    pass


def alarm_handler(_signum, _frame):
    raise Deadline("request exceeded wall-clock limit")


def option_map(row, source):
    opts = row.get("reviewed_options", {}) if source == "reviewed" else row.get("options", {})
    return {k: str(opts[k]) for k in LETTERS if isinstance(opts, dict) and opts.get(k) not in (None, "")}


def make_question(row, options):
    criteria = {}
    for letter in LETTERS:
        if letter in options:
            criteria[letter] = f"Option {letter}: {options[letter]}"
        else:
            criteria[letter] = f"The answer is option {letter}, shown in the attached problem image."
    return {
        "type": "choice",
        "instructions": "Which answer letter is correct for this multiple-choice math problem? Return the best choice.",
        "criteria": criteria,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", type=Path, default=REPO_ROOT / "data/omni-runtime/model")
    ap.add_argument("--mode", choices=("text", "vision"), required=True)
    ap.add_argument("--options", choices=("ocr", "reviewed"), default="reviewed")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=100)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--language", action="append", default=[])
    ap.add_argument("--kind", action="append", default=[])
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    import torch
    from PIL import Image
    import transformers
    from transformers import AutoModel

    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    model = AutoModel.from_pretrained(args.model, trust_remote_code=True, local_files_only=True,
                                      torch_dtype=torch.float32)
    model.eval()
    # For reproducible local feasibility, use CPU on this host; UNO Q timings
    # must be measured on-board separately.
    model.to("cpu")

    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.language:
        rows = [r for r in rows if r.get("language") in args.language]
    if args.kind:
        rows = [r for r in rows if r.get("kind") in args.kind]
    if args.id:
        wanted = set(args.id)
        rows = [r for r in rows if str(r.get("id")) in wanted]
    if args.limit:
        rows = rows[:args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if args.resume and args.out.exists():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("record_type") == "result":
                    done.add((str(rec.get("id")), rec.get("language")))
            except json.JSONDecodeError:
                pass

    records = []
    open_mode = "a" if args.resume else "w"
    signal.signal(signal.SIGALRM, alarm_handler)
    with args.out.open(open_mode, encoding="utf-8") as out:
        for index, row in enumerate(rows, 1):
            key = (str(row["id"]), row.get("language"))
            if key in done:
                continue
            started = time.monotonic()
            opts = option_map(row, args.options)
            question = make_question(row, opts)
            state = str(row.get("text") or "")
            images = None
            vision_fallback = ""
            if args.mode == "vision":
                image_value = row.get("image")
                if not image_value:
                    status, result = "ok", {}
                    vision_fallback = "text_no_image"
                else:
                    image_file = Path(image_value)
                    if not image_file.is_absolute():
                        image_file = args.input.parent / image_file
                    if not image_file.is_file():
                        status, result = "missing_image", {}
                    else:
                        with Image.open(image_file) as im:
                            image = im.convert("RGB")
                        images = [image]
                        status = "ok"
            else:
                status = "ok"

            record = {"record_type": "result", "id": row.get("id"), "language": row.get("language"),
                      "level": row.get("level"), "kind": row.get("kind"), "mode": args.mode,
                      "vision_fallback": vision_fallback, "options_source": args.options,
                      "answer": "", "raw_answer": None,
                      "status": status, "revision": REVISION}
            if status == "ok":
                try:
                    signal.alarm(max(1, args.timeout))
                    result = model.system_one(state, {"answer": question}, images=images)
                    signal.alarm(0)
                    raw_answer = result.get("answers", {}).get("answer", {})
                    answer = raw_answer.get("choice", "") if isinstance(raw_answer, dict) else str(raw_answer)
                    answer = str(answer).strip().upper()
                    if len(answer) != 1 or answer not in LETTERS:
                        status = "invalid_answer"
                    record.update({"answer": answer if len(answer) == 1 and answer in LETTERS else "", "raw_answer": raw_answer,
                                   "usage": result.get("usage", {}), "status": status})
                except Deadline as exc:
                    signal.alarm(0)
                    record.update({"status": "timeout", "error": str(exc)})
                except Exception as exc:
                    signal.alarm(0)
                    record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            elapsed = time.monotonic() - started
            label = str(row.get("label", "")).upper()
            record.update({"seconds": round(elapsed, 4), "within_60": elapsed <= 60,
                           "within_100": elapsed <= 100, "label": label,
                           "correct": bool(label) and record["answer"] == label})
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            records.append(record)
            print(f"[{index}/{len(rows)}] {row.get('id')} {row.get('language')} -> "
                  f"{record['answer'] or '?'} {elapsed:.1f}s {record['status']}", flush=True)

        # Summaries include only this invocation's new records.
        groups = defaultdict(list)
        for rec in records:
            groups[(rec.get("kind"), rec.get("language"))].append(rec)
        summary = {"record_type": "summary", "backend": "d1-omni-600M", "mode": args.mode,
                   "options_source": args.options, "revision": REVISION, "n_new": len(records),
                   "runtime": {"torch": torch.__version__, "transformers": transformers.__version__,
                               "device": "cpu", "threads": args.threads},
                   "correct_new": sum(r["correct"] for r in records),
                   "accuracy_new": sum(r["correct"] for r in records) / len(records) if records else None,
                   "mean_seconds_new": statistics.mean(r["seconds"] for r in records) if records else None,
                   "within_60_new": sum(r["within_60"] for r in records),
                   "within_100_new": sum(r["within_100"] for r in records),
                   "status_counts": {s: sum(r["status"] == s for r in records)
                                     for s in sorted({r["status"] for r in records})},
                   "groups": {f"{kind}/{lang}": {"n": len(group),
                             "correct": sum(r["correct"] for r in group),
                             "accuracy": sum(r["correct"] for r in group) / len(group),
                             "mean_seconds": statistics.mean(r["seconds"] for r in group)}
                              for (kind, lang), group in sorted(groups.items())}}
        out.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
