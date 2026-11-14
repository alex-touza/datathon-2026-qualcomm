#!/usr/bin/env python3
"""Generate and verify three-part tutor solutions with a cloud model.

Build-time only. The board never calls this tool or the network; it reads the
verified rows from data/explanations.db.

    export DEEPSEEK_API_KEY=...
    python3 tools/generate_solutions.py --dry-run
    python3 tools/generate_solutions.py --limit-ids 5 --name pilot-v1
    python3 tools/generate_solutions.py --limit-ids 200 --name demo-v1

Default selection: 200 problem ids, each in English and Catalan (400 rows),
stratified by level. Only training problems with readable options are eligible.
"""
import argparse
import base64
import concurrent.futures
import csv
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot.solutions import check_parts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS = os.path.join(ROOT, "tools", "prompts")
SCHEMA = """
CREATE TABLE IF NOT EXISTS solutions (
    key TEXT PRIMARY KEY,
    problem_id TEXT,
    language TEXT,
    level INTEGER,
    part1 TEXT,
    part2 TEXT,
    part3 TEXT,
    model TEXT,
    prompt_version TEXT,
    verified INTEGER,
    reject_reason TEXT,
    created_at REAL
)"""


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default="data/train.jsonl")
    p.add_argument("--images", default="data/images")
    p.add_argument("--out", default="data/explanations.db")
    p.add_argument("--languages", default="eng,cat")
    p.add_argument("--limit-ids", type=int, default=200, help="unique problems; each is generated in every language")
    p.add_argument("--all", action="store_true", help="use every eligible id instead of a 200-id sample")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--name", default="solutions-v1")
    p.add_argument("--model", default="deepseek-v4-pro")
    p.add_argument("--base-url", default="https://api.deepseek.com")
    p.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    p.add_argument("--max-tokens", type=int, default=6000)
    p.add_argument("--reasoning-effort", default="low", choices=("low", "high", "max", ""),
                   help="v4 Pro reasoning budget; low is enough for a structured solution")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--workers", type=int, default=4, help="parallel cloud requests")
    p.add_argument("--force", action="store_true", help="regenerate rows that already exist")
    p.add_argument("--retry-failed", action="store_true", help="retry rows that exist but are unverified")
    p.add_argument("--include-messy", action="store_true",
                   help="allow OCR-garbled options in the selection (off by default)")
    p.add_argument("--stop-on-error", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="print the prompt and selection; no API call")
    return p.parse_args()


def clean_options(problem):
    """Reject clearly garbled OCR options; a bad option confuses the model."""
    options = problem.get("options") or {}
    if len(options) != 5:
        return False
    values = [str(value).strip() for value in options.values()]
    if any(not value or "=" in value or "\n" in value or len(value) > 40 for value in values):
        return False
    return len(set(values)) == 5


def load_eligible(path, languages, clean=True):
    by_id = defaultdict(dict)
    with open(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            problem = json.loads(line)
            if problem.get("options") and (not clean or clean_options(problem)):
                by_id[problem["id"]][problem["language"]] = problem
    return {pid: langs for pid, langs in by_id.items()
            if all(lang in langs for lang in languages)}


def select_rows(eligible, languages, limit_ids, seed):
    if not limit_ids:
        ids = sorted(eligible)
    else:
        by_level = defaultdict(list)
        for pid, langs in eligible.items():
            by_level[langs[languages[0]].get("level", 0)].append(pid)
        rng = random.Random(seed)
        for ids in by_level.values():
            rng.shuffle(ids)
        levels = sorted(by_level)
        quota = max(1, math.ceil(limit_ids / len(levels)))
        chosen = []
        for level in levels:
            chosen.extend(by_level[level][:quota])
            if len(chosen) >= limit_ids:
                break
        if len(chosen) < limit_ids:
            rest = [pid for level in levels for pid in by_level[level] if pid not in chosen]
            rng.shuffle(rest)
            chosen.extend(rest[:limit_ids - len(chosen)])
        ids = sorted(set(chosen))[:limit_ids]
    return [eligible[pid][lang] for pid in ids for lang in languages]


def read_prompt():
    with open(os.path.join(PROMPTS, "solution_system_v1.txt")) as handle:
        system = handle.read().strip()
    with open(os.path.join(PROMPTS, "solution_user_v1.txt")) as handle:
        user = handle.read()
    return system, user


def format_prompt(template, problem):
    options = "\n".join(f"{letter}) {value}" for letter, value in problem["options"].items())
    label = problem["label"]
    return (template.replace("{{PROBLEM}}", problem["text"].strip())
            .replace("{{OPTIONS}}", options)
            .replace("{{LABEL}}", label)
            .replace("{{VALUE}}", problem["options"][label]))


def request_json(args, system, prompt, image_b64=None):
    key = os.environ.get(args.api_key_env)
    if not key:
        raise SystemExit(f"set {args.api_key_env} first")
    url = args.base_url.rstrip("/") + "/chat/completions"
    content = prompt
    if image_b64:
        content = [
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
            {"type": "text", "text": prompt},
        ]
    payload = {
        "model": args.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": content}],
        "temperature": 0,
        "seed": 42,
        "max_tokens": args.max_tokens,
        "response_format": {"type": "json_object"},
    }
    if args.reasoning_effort:
        payload["reasoning_effort"] = args.reasoning_effort
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
    last = None
    for attempt in range(max(1, args.retries)):
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            last = error
            if error.code in (429, 500, 502, 503, 504) and attempt + 1 < args.retries:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
            if attempt + 1 < args.retries:
                time.sleep(2 ** attempt)
                continue
            raise
    raise last


def parse_parts(content):
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    return {key: str(data.get(key, "")) for key in ("part1", "part2", "part3")}


def main():
    args = parse_args()
    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    eligible = load_eligible(args.input, languages, clean=not args.include_messy)
    rows = select_rows(eligible, languages, 0 if args.all else args.limit_ids, args.seed)
    print(f"{len(eligible)} eligible ids; selected {len(rows)} rows "
          f"({len({r['id'] for r in rows})} ids, {languages})", flush=True)

    if args.dry_run:
        system, template = read_prompt()
        print("\n=== SYSTEM ===\n" + system)
        print("\n=== USER ===\n" + format_prompt(template, rows[0]))
        return

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    db = sqlite3.connect(args.out)
    db.execute(SCHEMA)
    db.commit()
    existing = {(key, verified) for key, verified in db.execute("SELECT key, verified FROM solutions")}
    if args.force:
        existing = set()

    system, template = read_prompt()
    review = []
    known = {key for key, _verified in existing}
    failed = {key for key, verified in existing if not verified}
    todo = [p for p in rows if p["id"] + "|" + p["language"] not in known
            or (args.retry_failed and p["id"] + "|" + p["language"] in failed)]
    print(f"{len(todo)} rows to generate", flush=True)

    def work(problem):
        key = problem["id"] + "|" + problem["language"]
        prompt = format_prompt(template, problem)
        image_b64 = None
        if not problem.get("text_only", True):
            path = os.path.join(args.images, key.replace("|", "_") + ".png")
            if os.path.exists(path):
                with open(path, "rb") as handle:
                    image_b64 = base64.b64encode(handle.read()).decode()
                prompt = ("The problem includes the attached image; use it as part of "
                          "the problem.\n\n" + prompt)
        parts, reason = None, ""
        try:
            body = request_json(args, system, prompt, image_b64)
            parts = parse_parts(body["choices"][0]["message"]["content"])
            ok, reason = check_parts(problem, parts)
        except Exception as error:  # noqa: BLE001 - record the failure, keep going
            ok, reason = False, f"{type(error).__name__}: {error}"
            parts = parts or {"part1": "", "part2": "", "part3": ""}
        return {**problem, "problem": problem, "key": key, **parts,
                "verified": ok, "reason": reason}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, problem): problem for problem in todo}
        for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
            row = future.result()
            problem, key = row["problem"], row["key"]
            db.execute(
                "INSERT OR REPLACE INTO solutions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, problem["id"], problem["language"], problem.get("level"),
                 row["part1"], row["part2"], row["part3"], args.model, args.name,
                 int(row["verified"]), "" if row["verified"] else row["reason"], time.time()))
            db.commit()
            review.append(row)
            print(f"[{n}/{len(todo)}] {key}: "
                  f"{'OK' if row['verified'] else 'REJECT ' + row['reason']}", flush=True)
            if not row["verified"] and args.stop_on_error:
                for pending in futures:
                    pending.cancel()
                break

    os.makedirs("results", exist_ok=True)
    review_path = f"results/solutions-review-{args.name}.csv"
    with open(review_path, "w", newline="", encoding="utf-8") as handle:
        fields = ["key", "id", "language", "level", "text", "part1", "part2",
                  "part3", "verified", "reason"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(review)
    db.close()
    print(f"\nWrote {args.out}\nReview: {review_path}")


if __name__ == "__main__":
    main()
