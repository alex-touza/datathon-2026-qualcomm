#!/usr/bin/env python3
"""Run one benchmark arm over a JSONL manifest and write resumable JSONL results.

The input is a benchmark manifest, not a model prompt. `label` is only read after
inference for scoring and is never copied into any request payload.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import statistics
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
from pathlib import Path

LETTERS = "ABCDE"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
SYSTEM_PROMPT = ("You solve multiple-choice math competition problems for school students. "
                "Read the problem and the options, then reply with the letter of the correct option.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_manifest(path: Path):
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {e}") from e
            if not isinstance(row, dict) or "id" not in row or "text" not in row:
                raise ValueError(f"{path}:{line_no}: each row needs id and text")
            rows.append(row)
    return rows


def selected_options(row, source):
    opts = row.get("options", {}) if source == "ocr" else row.get("reviewed_options", {})
    if not isinstance(opts, dict):
        opts = {}
    return {k: str(opts[k]) for k in LETTERS if k in opts and opts[k] not in (None, "")}


def prompt_for(row, options):
    lines = ["Solve the multiple-choice math problem. Return only one capital letter: A, B, C, D, or E.",
             "Do not include explanation or punctuation.", "", str(row.get("text") or "")]
    if options:
        lines += ["", "Answer choices:"] + [f"{k}. {options[k]}" for k in LETTERS if k in options]
    else:
        lines += ["", "The answer choices may be shown in the attached image. Choose the correct letter."]
    return "\n".join(lines)


def image_path(row, manifest):
    value = row.get("image")
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else manifest.parent / p


def call_json(url, payload, headers, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from None


def llama_request(row, manifest, endpoint, vision, options, timeout, model):
    prompt = prompt_for(row, options)
    user_content = prompt
    path = image_path(row, manifest) if vision else None
    if path is not None:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        user_content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]
    payload = {"model": model, "messages": [
                   {"role": "system", "content": SYSTEM_PROMPT},
                   {"role": "user", "content": user_content}],
               "temperature": 0, "seed": 42, "max_tokens": 1,
               "grammar": "root ::= [A-E]", "cache_prompt": True, "stream": False}
    status, result = call_json(endpoint.rstrip("/") + "/v1/chat/completions", payload, {}, timeout)
    answer = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = result.get("usage", {})
    return answer, {"http_status": status, "usage": usage, "timings": result.get("timings", {})}


def typesafe_request(row, manifest, endpoint, vision, options, timeout, model, key):
    text = prompt_for(row, options)
    ref = str(uuid.uuid4())
    payload = {"model": model, "state": {"problem": text}, "questions": {
        "answer": {"type": "choice", "instructions": "Which answer letter is correct for `problem`? Return the single best choice.",
                    "criteria": {letter: (f"Option {letter}: {options[letter]}" if letter in options else f"The correct answer is option {letter}.") for letter in LETTERS}}
    }}
    headers = {"Authorization": f"Bearer {key}"}
    path = image_path(row, manifest) if vision else None
    if path is not None:
        ext = path.suffix.lower()
        fmt = "png" if ext == ".png" else "jpg" if ext in (".jpg", ".jpeg") else None
        if fmt is None:
            raise ValueError(f"TypeSafe image must be PNG/JPEG: {path}")
        payload["state"]["problem_image"] = ref
        payload["assets"] = {ref: {"type": "image", "format": fmt,
                                   "data_base64": base64.b64encode(path.read_bytes()).decode("ascii")}}
        headers["TypeSafe-Danger-Zone-Images"] = "true"
        payload["questions"]["answer"]["instructions"] += " The choices may appear in `problem_image`."
    status, result = call_json(endpoint, payload, headers, timeout)
    answers = result.get("answers", {})
    answer = answers.get("answer", "") if isinstance(answers, dict) else ""
    if isinstance(answer, dict):
        answer = answer.get("choice", answer.get("value", answer))
    return str(answer), {"http_status": status, "usage": result.get("usage", {}), "timings": result.get("timings", {})}


def normalize(answer):
    candidate = str(answer).strip()
    return candidate if len(candidate) == 1 and candidate in LETTERS else ""


def load_key_file(path):
    """Read a protected key file containing either the raw key or KEY=value."""
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError("key file must be private (mode 0600 or stricter)")
    contents = path.read_text(encoding="utf-8").strip()
    if contents.startswith("export "):
        contents = contents[7:].strip()
    if "=" in contents:
        name, value = contents.split("=", 1)
        if name.strip() != "TYPESAFE_API_KEY":
            raise ValueError("key file variable must be TYPESAFE_API_KEY")
        contents = value.strip().strip("\"'")
    if not contents:
        raise ValueError("key file contains an empty API key")
    return contents


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--backend", choices=("llama-text", "llama-vision", "typesafe-text", "typesafe-vision"), required=True)
    p.add_argument("--endpoint", help="llama OpenAI-compatible base URL; TypeSafe always uses its official endpoint")
    p.add_argument("--model", default=None)
    p.add_argument("--options", choices=("ocr", "reviewed"), default=None, help="options field; defaults to reviewed for vision and OCR for text")
    p.add_argument("--key-file", type=Path, help="read TypeSafe API key from file (must have mode 0600 or stricter)")
    p.add_argument("--timeout", type=float, default=100, help="per-request timeout seconds (default 100)")
    p.add_argument("--limit", type=int, default=0, help="max samples; 0 means all")
    p.add_argument("--language", action="append", default=[])
    p.add_argument("--kind", action="append", default=[])
    p.add_argument("--id", action="append", default=[])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--resume", action="store_true", help="skip IDs already present in output")
    args = p.parse_args()
    vision = args.backend.endswith("vision")
    is_typesafe = args.backend.startswith("typesafe")
    endpoint = TYPESAFE_URL if is_typesafe else (args.endpoint or "http://127.0.0.1:8081")
    if is_typesafe and args.endpoint and args.endpoint != TYPESAFE_URL:
        p.error("TypeSafe credentials are only sent to the official API endpoint")
    options_source = args.options or ("reviewed" if vision else "ocr")
    model = args.model or ("jev-latest" if is_typesafe else "local-model")
    key = os.environ.get("TYPESAFE_API_KEY")
    if args.key_file:
        try:
            key = load_key_file(args.key_file)
        except (OSError, ValueError) as e:
            p.error(str(e))
    if is_typesafe and not key:
        p.error("TypeSafe requires TYPESAFE_API_KEY or --key-file")
    rows = read_manifest(args.input)
    if args.language:
        rows = [r for r in rows if r.get("language") in args.language]
    if args.kind:
        rows = [r for r in rows if r.get("kind") in args.kind]
    if args.id:
        ids = set(args.id)
        rows = [r for r in rows if str(r.get("id")) in ids]
    if args.limit:
        rows = rows[:args.limit]
    if is_typesafe:
        unsafe = [str(r.get("id", "?")) for r in rows if r.get("dataset_origin") != "synthetic"]
        if unsafe:
            p.error("Refusing hosted TypeSafe run: every selected row must have dataset_origin='synthetic'; "
                    "M3Kang data may not be sent to third parties. Unsafe row ids: " + ", ".join(unsafe[:10]))
    if not is_typesafe and any(r.get("dataset_origin") == "m3kang" for r in rows):
        url = urlsplit(endpoint)
        if url.scheme not in ("http", "https") or url.hostname not in ("127.0.0.1", "::1"):
            p.error("M3Kang runs require a numeric loopback endpoint")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if args.resume and args.out.exists():
        with args.out.open(encoding="utf-8") as f:
            for line in f:
                try:
                    prior = json.loads(line)
                    if prior.get("record_type") == "result":
                        if (prior.get("backend"), prior.get("options_source"), prior.get("model")) != (
                            args.backend, options_source, model
                        ):
                            p.error("--resume requires the same backend, options source and model")
                        done.add((str(prior.get("id")), str(prior.get("language") or "")))
                except json.JSONDecodeError:
                    continue
    mode = "a" if args.resume else "w"
    outcomes = []
    with args.out.open(mode, encoding="utf-8") as out:
        for i, row in enumerate(rows, 1):
            if (str(row["id"]), str(row.get("language") or "")) in done:
                continue
            started = time.monotonic()
            record = {"record_type": "result", "id": row["id"], "language": row.get("language"),
                      "level": row.get("level"), "kind": row.get("kind"), "backend": args.backend,
                      "options_source": options_source, "model": model, "image_present": bool(image_path(row, args.input) and image_path(row, args.input).is_file()),
                      "vision_requested": vision, "answer": "", "status": "error"}
            try:
                opts = selected_options(row, options_source)
                if is_typesafe:
                    raw_answer, meta = typesafe_request(row, args.input, endpoint, vision, opts, args.timeout,
                                                        model, key)
                else:
                    raw_answer, meta = llama_request(row, args.input, endpoint, vision, opts, args.timeout,
                                                     model)
                answer = normalize(raw_answer)
                record.update({"answer": answer, "raw_answer": raw_answer, "status": "ok" if answer else "invalid_answer", **meta})
            except Exception as e:
                record["error"] = str(e) if isinstance(e, ValueError) else ("timeout" if isinstance(e, TimeoutError) else type(e).__name__)
                if isinstance(e, urllib.error.URLError) and isinstance(e.reason, TimeoutError):
                    record["error"] = "timeout"
                record["status"] = "timeout" if record["error"] == "timeout" else "error"
            elapsed = time.monotonic() - started
            record["seconds"] = round(elapsed, 4)
            record["within_60"] = elapsed <= 60
            record["within_100"] = elapsed <= 100
            # Labels are deliberately used only here, after all request construction/inference.
            label = str(row.get("label", "")).strip().upper()
            if len(label) != 1 or label not in LETTERS:
                record["status"] = "invalid_label"
                record["label"] = None
                record["correct"] = None
            else:
                record["label"] = label
                record["correct"] = record["answer"] == label
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            outcomes.append(record)
            print(f"[{i}/{len(rows)}] {row['id']} -> {record['answer'] or '?'} {elapsed:.1f}s {record['status']}", flush=True)
        valid = [r for r in outcomes if r["correct"] is not None]
        summary = {"record_type": "summary", "backend": args.backend, "input": str(args.input),
                   "options_source": options_source, "n_new": len(outcomes), "n_scored_new": len(valid),
                   "accuracy_new": (sum(r["correct"] for r in valid) / len(valid) if valid else None),
                   "mean_seconds_new": (statistics.mean(r["seconds"] for r in outcomes) if outcomes else None),
                   "correct_within_60_new": sum(bool(r["correct"]) and r["within_60"] for r in valid),
                   "correct_within_100_new": sum(bool(r["correct"]) and r["within_100"] for r in valid),
                   "completed_within_60_new": sum(r["within_60"] and r["status"] in ("ok", "invalid_answer") for r in outcomes),
                   "completed_within_100_new": sum(r["within_100"] and r["status"] in ("ok", "invalid_answer") for r in outcomes),
                   "timeout_seconds": args.timeout, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        out.write(json.dumps(summary) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
