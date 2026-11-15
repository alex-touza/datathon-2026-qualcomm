#!/usr/bin/env python3
"""Generate and verify three-part tutor solutions with a cloud model.

Build-time only. The board never calls this tool or the network; it reads the
verified rows from data/explanations.db.

Any OpenAI-compatible endpoint works; see tools/PROVIDERS.md.

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

from copilot.adapt import check_adaptation  # noqa: E402
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
    created_at REAL,
    adapt TEXT,
    adapt_ok INTEGER,
    adapt_reason TEXT
)"""
COLUMNS = ("key", "problem_id", "language", "level", "part1", "part2", "part3", "model",
           "prompt_version", "verified", "reject_reason", "created_at",
           "adapt", "adapt_ok", "adapt_reason")
ADAPT_FIELDS = ("template", "variables", "answer_formula", "steps_template", "dyslexia_template")


def open_db(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(SCHEMA)
    have = {row[1] for row in db.execute("PRAGMA table_info(solutions)")}
    for column, kind in (("adapt", "TEXT"), ("adapt_ok", "INTEGER"), ("adapt_reason", "TEXT")):
        if column not in have:
            db.execute(f"ALTER TABLE solutions ADD COLUMN {column} {kind}")
    db.commit()
    return db


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
    p.add_argument("--prompt-version", default="v4")
    # Provider settings. Any OpenAI-compatible endpoint works; see tools/PROVIDERS.md.
    p.add_argument("--model", default="deepseek-v4-pro")
    p.add_argument("--base-url", default="https://api.deepseek.com")
    p.add_argument("--api-key-env", default="DEEPSEEK_API_KEY",
                   help="environment variable holding the key")
    p.add_argument("--api-key-file", default="",
                   help="read the key from this file instead of the environment")
    p.add_argument("--max-tokens", type=int, default=6000)
    p.add_argument("--reasoning-effort", default="low", choices=("low", "high", "max", ""),
                   help="usually DeepSeek only; pass an empty value for providers without it")
    p.add_argument("--no-json-mode", action="store_true",
                   help="do not send response_format=json_object; some endpoints reject it")
    p.add_argument("--no-seed", action="store_true",
                   help="do not send seed; some endpoints reject it")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--workers", type=int, default=4, help="parallel cloud requests")
    p.add_argument("--force", action="store_true", help="regenerate rows that already exist")
    p.add_argument("--retry-failed", action="store_true", help="retry rows that exist but are unverified")
    p.add_argument("--include-messy", action="store_true",
                   help="allow OCR-garbled options in the selection (off by default)")
    p.add_argument("--stop-on-error", action="store_true")
    p.add_argument("--max-new", type=int, default=0, help="generate at most this many rows, then stop")
    p.add_argument("--price-in", type=float, default=1.9, help="USD per million prompt tokens")
    p.add_argument("--price-out", type=float, default=1.9, help="USD per million completion tokens")
    p.add_argument("--max-spend", type=float, default=0.0,
                   help="stop after this much estimated spend (0 = no cap)")
    p.add_argument("--dry-run", action="store_true", help="print the prompt and selection; no API call")
    p.add_argument("--text-only", action="store_true",
                   help="only problems whose statement does not need the image")
    p.add_argument("--export-prompts", default="",
                   help="write the prompts of the rows to generate as JSONL and stop (no API call)")
    p.add_argument("--import-responses", nargs="*", default=[],
                   help="JSONL files of {key, response}: verify and store them instead of calling an API")
    return p.parse_args()


_OCR_JUNK = re.compile(r"[\u4e00-\u9fff]|^\s*0\d|\d[A-Za-z]\d")


def clean_options(problem):
    """Reject clearly garbled OCR options; a bad option confuses the model.

    Only the damage that makes an option unusable is rejected here. Split
    fractions (`49 10`) and OCR square roots (`V5`) are kept: the verifier knows
    how to read them, and rejecting them costs a real problem.
    """
    options = problem.get("options") or {}
    if len(options) != 5:
        return False
    values = [str(value).strip() for value in options.values()]
    if any(not value or "=" in value or "\n" in value or len(value) > 40 for value in values):
        return False
    if any(_OCR_JUNK.search(value) for value in values):
        return False
    return len(set(values)) == 5


def load_eligible(path, languages, clean=True, text_only=False):
    by_id = defaultdict(dict)
    with open(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            problem = json.loads(line)
            if text_only and not problem.get("text_only"):
                continue
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


def read_prompt(version="v3"):
    with open(os.path.join(PROMPTS, "solution_system_v1.txt")) as handle:
        system = handle.read().strip()
    with open(os.path.join(PROMPTS, f"solution_user_{version}.txt")) as handle:
        user = handle.read()
    return system, user


def format_prompt(template, problem):
    options = "\n".join(f"{letter}) {value}" for letter, value in problem["options"].items())
    label = problem["label"]
    return (template.replace("{{PROBLEM}}", problem["text"].strip())
            .replace("{{OPTIONS}}", options)
            .replace("{{LABEL}}", label)
            .replace("{{VALUE}}", problem["options"][label]))


def read_key(args):
    """The API key, from --api-key-file or the environment variable."""
    if args.api_key_file:
        with open(args.api_key_file) as handle:
            key = handle.read().strip()
        if key:
            return key
        raise SystemExit(f"{args.api_key_file} is empty")
    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        raise SystemExit(f"set {args.api_key_env} (or pass --api-key-file) first")
    return key


def message_text(message):
    """The assistant text, for endpoints that return a string or a list of parts."""
    content = message.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def usage_tokens(body):
    """Token counts, whichever shape the endpoint reports them in."""
    usage = body.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None:
        meta = body.get("usage_metadata") or {}
        prompt = meta.get("prompt_token_count")
        completion = meta.get("candidates_token_count")
    return int(prompt or 0), int(completion or 0)


def request_json(args, system, prompt, image_b64=None, max_tokens=None):
    """One chat-completions call. Any OpenAI-compatible endpoint works."""
    key = read_key(args)
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
        "max_tokens": max_tokens or args.max_tokens,
    }
    if not args.no_seed:
        payload["seed"] = args.seed
    if not args.no_json_mode:
        payload["response_format"] = {"type": "json_object"}
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
            # Show the endpoint's own message: the first failure against a new
            # provider is almost always a flag it does not support.
            try:
                detail = error.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                detail = ""
            last = RuntimeError(f"HTTP {error.code} from {url}: {detail}")
            if error.code in (429, 500, 502, 503, 504) and attempt + 1 < args.retries:
                time.sleep(2 ** attempt)
                continue
            raise last from error
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
            if attempt + 1 < args.retries:
                time.sleep(2 ** attempt)
                continue
            raise
    raise last


def parse_response(content):
    if isinstance(content, dict):
        return content
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
    return data


def parse_parts(content):
    data = parse_response(content)
    return {key: str(data.get(key, "")) for key in ("part1", "part2", "part3")}


def verify_response(problem, data, strict_tail):
    """(parts, ok, reason, adapt_json, adapt_ok, adapt_reason) for one parsed response."""
    parts = {key: str(data.get(key, "")).strip() for key in ("part1", "part2", "part3")}
    parts["part3"] = re.sub(r"\n\s*\n+", "\n", parts["part3"])  # no blank line before the answer
    ok, reason = check_parts(problem, parts, strict_tail=strict_tail)
    adapt = {key: data[key] for key in ADAPT_FIELDS if key in data}
    if not adapt:
        return parts, ok, reason, None, None, ""
    adapt_ok, adapt_reason, small = check_adaptation(problem, adapt)
    if adapt_ok and small is not None:
        adapt["answer_small"] = small
    return parts, ok, reason, json.dumps(adapt, ensure_ascii=False), int(adapt_ok), adapt_reason


def store(db, args, row):
    problem = row["problem"]
    values = (row["key"], problem["id"], problem["language"], problem.get("level"),
              row["part1"], row["part2"], row["part3"], args.model, args.prompt_version,
              int(row["verified"]), "" if row["verified"] else row["reason"], time.time(),
              row.get("adapt"), row.get("adapt_ok"), row.get("adapt_reason") or "")
    db.execute(f"INSERT OR REPLACE INTO solutions ({', '.join(COLUMNS)}) "
               f"VALUES ({', '.join('?' * len(COLUMNS))})", values)
    db.commit()


def import_responses(args, rows, strict_tail):
    """Verify and store responses written elsewhere (e.g. by coding agents)."""
    by_key = {p["id"] + "|" + p["language"]: p for p in rows}
    # Responses may cover rows outside this selection; look them up in the input too.
    with open(args.input) as handle:
        for line in handle:
            if line.strip():
                problem = json.loads(line)
                by_key.setdefault(problem["id"] + "|" + problem["language"], problem)
    db = open_db(args.out)
    review, counts = [], defaultdict(int)
    for path in args.import_responses:
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    problem = by_key[record["key"]]
                    data = parse_response(record.get("response", record))
                    parts, ok, reason, adapt, adapt_ok, adapt_reason = verify_response(
                        problem, data, strict_tail)
                except Exception as error:  # noqa: BLE001 - report the line, keep going
                    print(f"{path}:{number}: skipped ({type(error).__name__}: {error})")
                    counts["unreadable"] += 1
                    continue
                row = {**problem, "problem": problem, "key": record["key"], **parts,
                       "verified": ok, "reason": reason, "adapt": adapt,
                       "adapt_ok": adapt_ok, "adapt_reason": adapt_reason}
                store(db, args, row)
                review.append(row)
                counts["rows"] += 1
                counts["verified"] += int(ok)
                counts["adapt_ok"] += int(bool(adapt_ok))
                print(f"{record['key']}: {'OK' if ok else 'REJECT ' + reason}"
                      f" | adapt {'OK' if adapt_ok else 'REJECT ' + (adapt_reason or 'missing')}")
    db.close()
    os.makedirs("results", exist_ok=True)
    review_path = f"results/solutions-review-{args.name}.csv"
    with open(review_path, "w", newline="", encoding="utf-8") as handle:
        fields = ["key", "id", "language", "level", "text", "part1", "part2", "part3",
                  "verified", "reason", "adapt", "adapt_ok", "adapt_reason"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(review)
    print(f"\n{counts['rows']} rows imported: {counts['verified']} solutions verified, "
          f"{counts['adapt_ok']} adaptations verified, {counts['unreadable']} unreadable"
          f"\nWrote {args.out}\nReview: {review_path}")


def main():
    args = parse_args()
    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    eligible = load_eligible(args.input, languages, clean=not args.include_messy,
                             text_only=args.text_only)
    rows = select_rows(eligible, languages, 0 if args.all else args.limit_ids, args.seed)
    print(f"{len(eligible)} eligible ids; selected {len(rows)} rows "
          f"({len({r['id'] for r in rows})} ids, {languages})", flush=True)

    if args.dry_run:
        system, template = read_prompt(args.prompt_version)
        print("\n=== SYSTEM ===\n" + system)
        print("\n=== USER ===\n" + format_prompt(template, rows[0]))
        return

    strict_tail = args.prompt_version in ("v3", "v4")
    if args.import_responses:
        import_responses(args, rows, strict_tail)
        return
    db = open_db(args.out)
    existing = {(key, verified) for key, verified in db.execute("SELECT key, verified FROM solutions")}
    if args.force:
        existing = set()

    system, template = read_prompt(args.prompt_version)
    review = []
    usage_rows = []
    known = {key for key, _verified in existing}
    failed = {key for key, verified in existing if not verified}
    todo = [p for p in rows if p["id"] + "|" + p["language"] not in known
            or (args.retry_failed and p["id"] + "|" + p["language"] in failed)]
    print(f"{len(todo)} rows to generate", flush=True)
    if args.export_prompts:
        with open(args.export_prompts, "w", encoding="utf-8") as handle:
            for problem in todo:
                handle.write(json.dumps({"key": problem["id"] + "|" + problem["language"],
                                         "system": system,
                                         "prompt": format_prompt(template, problem)},
                                        ensure_ascii=False) + "\n")
        print(f"Wrote {len(todo)} prompts to {args.export_prompts}")
        return

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
        adapt, adapt_ok, adapt_reason = None, None, ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0,
                 "finish": "", "budget": args.max_tokens}
        budget = args.max_tokens
        try:
            for attempt in range(2):
                body = request_json(args, system, prompt, image_b64, max_tokens=budget)
                choice = body["choices"][0]
                prompt_tokens, completion_tokens = usage_tokens(body)
                usage["prompt_tokens"] += prompt_tokens
                usage["completion_tokens"] += completion_tokens
                usage["calls"] += 1
                usage["finish"] = choice.get("finish_reason") or ""
                usage["budget"] = budget
                content = message_text(choice.get("message") or {})
                truncated = str(usage["finish"]).lower() in ("length", "max_tokens")
                if content.strip() and not truncated:
                    parts, ok, reason, adapt, adapt_ok, adapt_reason = verify_response(
                        problem, parse_response(content), strict_tail)
                    break
                if attempt == 0:
                    # The hidden reasoning ate the whole budget and the JSON came
                    # back empty or cut off. Retry once with twice the room.
                    budget = min(budget * 2, 16000)
                    continue
                reasons = (f"truncated: finish_reason={usage['finish']}"
                           if truncated else "empty response")
                ok, reason = False, f"JSON truncated ({reasons}, {budget} tokens)"
                parts = {"part1": "", "part2": "", "part3": ""}
        except Exception as error:  # noqa: BLE001 - record the failure, keep going
            ok, reason = False, f"{type(error).__name__}: {error}"
            parts = parts or {"part1": "", "part2": "", "part3": ""}
        return {**problem, "problem": problem, "key": key, **parts,
                "verified": ok, "reason": reason, "usage": usage,
                "adapt": adapt, "adapt_ok": adapt_ok, "adapt_reason": adapt_reason}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        done = 0
        spent = 0.0
        stop = False
        window = max(1, args.workers) * 2
        for start in range(0, len(todo), window):
            if stop:
                break
            batch = todo[start:start + window]
            for row in pool.map(work, batch):
                done += 1
                problem, key = row["problem"], row["key"]
                spent += (row["usage"]["prompt_tokens"] * args.price_in
                          + row["usage"]["completion_tokens"] * args.price_out) / 1e6
                store(db, args, row)
                review.append(row)
                usage_rows.append({"key": key, "verified": int(row["verified"]),
                                   **row["usage"]})
                print(f"[{done}/{len(todo)}] {key}: "
                      f"{'OK' if row['verified'] else 'REJECT ' + row['reason']} "
                      f"({row['usage']['prompt_tokens']}+{row['usage']['completion_tokens']} tok, "
                      f"~${spent:.3f})", flush=True)
                if not row["verified"] and args.stop_on_error:
                    stop = True
                    break
                if args.max_new and done >= args.max_new:
                    stop = True
                    break
            if args.max_spend and spent >= args.max_spend:
                print(f"\nStopped at the spend cap: ~${spent:.3f} "
                      f"(cap ${args.max_spend:.2f} at the prices given)", flush=True)
                stop = True
        if args.max_spend and spent >= args.max_spend:
            pass
        elif args.max_new and done >= args.max_new:
            print(f"\nStopped at --max-new {args.max_new} rows", flush=True)

    os.makedirs("results", exist_ok=True)
    review_path = f"results/solutions-review-{args.name}.csv"
    with open(review_path, "w", newline="", encoding="utf-8") as handle:
        fields = ["key", "id", "language", "level", "text", "part1", "part2",
                  "part3", "verified", "reason"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(review)
    usage_path = f"results/solutions-usage-{args.name}.csv"
    with open(usage_path, "w", newline="", encoding="utf-8") as handle:
        fields = ["key", "verified", "prompt_tokens", "completion_tokens", "calls",
                  "finish", "budget"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(usage_rows)
    if usage_rows:
        prompt_tokens = sum(row["prompt_tokens"] for row in usage_rows)
        completion_tokens = sum(row["completion_tokens"] for row in usage_rows)
        print(f"\nTokens: {prompt_tokens} in / {completion_tokens} out "
              f"over {len(usage_rows)} rows "
              f"(mean {completion_tokens / len(usage_rows):.0f} out)")
    db.close()
    print(f"\nWrote {args.out}\nReview: {review_path}\nUsage: {usage_path}")


if __name__ == "__main__":
    main()
