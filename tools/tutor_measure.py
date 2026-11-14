#!/usr/bin/env python3
"""Measure the tutor: latency per rung, hint leak rate, and a simulated student path.

    python3 tools/tutor_measure.py --limit 20 --name tutor-v1
    python3 tools/tutor_measure.py --limit 20 --name tutor-v1 --fake     # no models, plumbing only
    python3 tools/tutor_measure.py --limit 20 --name tutor-v1 --no-llm   # rungs 1 and 2 only

What it does, on the practice problems from data/val.jsonl (the tutor never
practises on them, so they are a fair sample):

1. Rungs 1, 2 and 3 on each problem, recording seconds and status.
2. The raw hint from the 0.8B model is checked with the same code the tutor
   uses, so the report shows how often the model leaks and how often the tutor
   would have shown it. Shipping a leak is a failure and is printed as one.
3. Simulated students (right below a level, wrong above it) walk the adaptive
   path, so we can see whether it stays on topic and settles at a level.
4. The hint texts are dumped for hand judging: results/tutor-hints-<name>.json

Writes results/tutor-measure-<name>.json and appends a line per rung to
results/tutor-measure.csv. Everything in results/ is gitignored.

--fake starts tools/fake_servers.py instead of llama.cpp. Use it to check the
plumbing on a laptop with no models. Its numbers are not measurements.
"""
import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

RESULTS = os.path.join(ROOT, "results")
FAKE_EMBED_PORT = 9092
FAKE_LLM_PORT = 9091


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=20, help="problems to try (20 is the brief)")
    parser.add_argument("--name", default="tutor", help="tag for the result files")
    parser.add_argument("--input", default="data/val.jsonl", help="problems to measure on")
    parser.add_argument("--corpus", default="data/train.jsonl", help="problems the index is built from")
    parser.add_argument("--index", default="data/index.db")
    parser.add_argument("--solutions", default="data/explanations.db")
    parser.add_argument("--steps", type=int, default=16, help="steps per simulated student")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fake", action="store_true", help="use tools/fake_servers.py, not the models")
    parser.add_argument("--no-llm", action="store_true", help="skip rung 3")
    parser.add_argument("--build", action="store_true", help="build the index if it is missing")
    return parser.parse_args()


def setup_fake():
    import fake_servers
    fake_servers.start(FAKE_EMBED_PORT, FAKE_LLM_PORT)
    os.environ["EMBED_PORT"] = str(FAKE_EMBED_PORT)
    os.environ["LLM_PORT"] = str(FAKE_LLM_PORT)


def build_index(path, corpus, batch=16):
    from copilot.rag import build
    with open(corpus) as handle:
        problems = [json.loads(line) for line in handle if line.strip()]
    print(f"building {path} from {len(problems)} problems", flush=True)
    for done in build(path, problems, batch=batch):
        if done % 512 == 0 or done == len(problems):
            print(f"  {done}/{len(problems)}", flush=True)


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def describe(values):
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "worst": 0.0}
    return {"n": len(values), "mean": round(statistics.fmean(values), 2),
            "median": round(statistics.median(values), 2),
            "p95": round(percentile(values, 0.95), 2), "worst": round(max(values), 2)}


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


# --------------------------------------------------------------------------
# 1 and 2: latency and leaks
# --------------------------------------------------------------------------

def measure_rungs(tutor, problems, rungs):
    from copilot.tutor import leak_check

    rows, raw_leaks, shipped_leaks, shipped = [], 0, 0, 0
    per_rung = {rung: [] for rung in rungs}
    statuses = {rung: {} for rung in rungs}
    leaks_examples = []

    for number, problem in enumerate(problems, 1):
        for rung in rungs:
            hint = tutor.hint(problem, rung)
            per_rung[rung].append(hint["seconds"])
            statuses[rung][hint["status"]] = statuses[rung].get(hint["status"], 0) + 1
            row = {"problem": problem["id"], "language": problem["language"],
                   "level": problem["level"], "has_options": bool(problem.get("options")),
                   "rung": rung, "kind": hint["kind"], "status": hint["status"],
                   "seconds": hint["seconds"], "text": hint["text"]}
            if rung == 3:
                raw = (hint.get("detail") or {}).get("raw")
                if raw:
                    raw_leak, raw_reason = leak_check(raw, problem)
                    raw_leaks += int(raw_leak)
                else:
                    raw_reason = None
                if hint["kind"] == "model":
                    shown_leak, shown_reason = leak_check(hint["text"], problem)
                    shipped += 1
                    shipped_leaks += int(shown_leak)
                    row["shipped_leak"] = shown_reason if shown_leak else None
                    row["raw_leak"] = raw_reason
                    if shown_leak:
                        leaks_examples.append({"id": problem["id"], "text": hint["text"],
                                               "reason": shown_reason})
                    elif raw_reason:
                        leaks_examples.append({"id": problem["id"], "text": raw,
                                               "reason": f"rejected: {raw_reason}"})
            rows.append(row)
        print(f"  [{number}/{len(problems)}] {problem['id']} "
              + " ".join(f"r{r}={per_rung[r][-1]:.2f}s" for r in rungs), flush=True)
    return {"rows": rows, "per_rung": {r: describe(per_rung[r]) for r in rungs},
            "statuses": statuses, "raw_leaks": raw_leaks, "raw_checked": len(problems),
            "shipped": shipped, "shipped_leaks": shipped_leaks,
            "leak_examples": leaks_examples}


# --------------------------------------------------------------------------
# 3: simulated students
# --------------------------------------------------------------------------

def wrong_letter(problem, seed):
    label = problem.get("label")
    options = problem.get("options") or {}
    choices = [letter for letter in "ABCDE" if letter != label and letter in options]
    if not choices:
        choices = [letter for letter in "ABCDE" if letter != label]
    return random.Random(f"{seed}-{problem['id']}").choice(choices)


def simulate(tutor, threshold, steps, seed):
    """A student who is right when the level is at or below threshold, else wrong."""
    from copilot import topics
    from copilot.tutor import key_of

    problem = tutor.first_problem(level=max(0, min(3, threshold)), language="eng")
    seen, path, step_seconds = [], [], []
    if problem is None:
        return {"threshold": threshold, "steps": 0, "path": []}

    for _step in range(steps):
        level = int(problem.get("level", 0))
        topic = topics.classify(problem.get("text", ""))["topic"]
        correct = level <= threshold
        choice = problem["label"] if correct else wrong_letter(problem, seed)
        checked = tutor.check(problem, choice)
        if checked["correct"] != correct:
            raise RuntimeError(f"student model disagrees with the label on {problem['id']}")
        start = time.perf_counter()
        nxt = tutor.next_problem(problem, correct, seen_ids=seen,
                                 prefer_language=problem.get("language"))
        step_seconds.append(time.perf_counter() - start)
        path.append({"key": key_of(problem), "id": problem["id"], "level": level,
                     "topic": topic, "correct": correct,
                     "next_level": nxt["target_level"] if nxt else None,
                     "next_topic": nxt["topic"] if nxt else None,
                     "reason": nxt["reason"] if nxt else None})
        if not nxt:
            break
        seen.append(problem["id"])
        problem = tutor.get(nxt["problem"]["key"])
        if problem is None:
            raise RuntimeError(f"the tutor picked a problem it cannot serve: {nxt['problem']['key']}")

    last_half = path[len(path) // 2:]
    levels = [step["level"] for step in last_half]
    topic_matches = sum(1 for a, b in zip(path, path[1:]) if a["topic"] == b["topic"])
    return {"threshold": threshold, "steps": len(path), "path": path,
            "settled_level": statistics.mode(levels) if levels else None,
            "level_spread": sorted(set(levels)),
            "on_topic_share": round(topic_matches / max(1, len(path) - 1), 2),
            "seconds": describe(step_seconds)}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def main():
    args = parse_args()
    if args.fake:
        setup_fake()
    from copilot.tutor import Tutor
    from copilot.rag import Index

    index_path = args.index
    if args.fake and index_path == "data/index.db":
        index_path = "data/index-fake.db"
    if not os.path.exists(index_path):
        if not (args.build or args.fake):
            sys.exit(f"{index_path} is missing. Run build_index.py on the board, or pass --build.")
        build_index(index_path, args.corpus)
    os.chdir(ROOT)
    index = Index(index_path)

    with open(args.input) as handle:
        pool = [json.loads(line) for line in handle if line.strip()]
    rng = random.Random(args.seed)
    problems = rng.sample(pool, min(args.limit, len(pool)))

    profile_path = os.path.join(RESULTS, f"tutor-profile-{args.name}.db")
    if os.path.exists(profile_path):
        os.remove(profile_path)
    tutor = Tutor(index, profile_path=profile_path, llm_timeout=25.0,
                  solutions_path=args.solutions)

    rungs = [1, 2] if args.no_llm else [1, 2, 3]
    print(f"\nRungs {rungs} on {len(problems)} problems from {args.input}"
          f"{' (FAKE SERVERS: not a measurement)' if args.fake else ''}\n")
    leaks = measure_rungs(tutor, problems, rungs)

    print("\nLatency per rung (seconds, wall clock as the student waits)")
    for rung in rungs:
        stats = leaks["per_rung"][rung]
        print(f"  rung {rung}: n={stats['n']:>3} mean {stats['mean']:>6.2f} "
              f"median {stats['median']:>6.2f} p95 {stats['p95']:>6.2f} worst {stats['worst']:>6.2f}"
              f"   {leaks['statuses'][rung]}")
    if 3 in rungs:
        raw_rate = 100.0 * leaks["raw_leaks"] / max(1, leaks["raw_checked"])
        shipped_rate = 100.0 * leaks["shipped_leaks"] / max(1, leaks["shipped"])
        print(f"\nHint leaks: model leaked {leaks['raw_leaks']}/{leaks['raw_checked']} "
              f"({raw_rate:.0f}%); tutor showed {leaks['shipped_leaks']}/{leaks['shipped']} "
              f"({shipped_rate:.0f}%)")
        if leaks["shipped_leaks"]:
            print("  FAIL: a leaky hint reached the student. See leak_examples.")

    students = []
    print("\nSimulated students (adaptive practice, no model in the loop)")
    for threshold in (6, 3, 1, -1):
        student = simulate(tutor, threshold, args.steps, args.seed)
        students.append(student)
        levels = [step["level"] for step in student["path"]]
        print(f"  right up to level {threshold:>2}: {student['steps']:>2} steps, levels "
              f"{levels}, settled {student['settled_level']}, on topic "
              f"{student['on_topic_share']:.0%}, {student['seconds']['median']:.3f}s/step")

    os.makedirs(RESULTS, exist_ok=True)
    report = {"name": args.name, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
              "commit": git_commit(), "fake": args.fake, "no_llm": args.no_llm,
              "limit": len(problems), "index": index_path, "rungs": rungs,
              "latency": leaks["per_rung"], "statuses": leaks["statuses"],
              "raw_leaks": leaks["raw_leaks"], "raw_checked": leaks["raw_checked"],
              "shipped_leaks": leaks["shipped_leaks"], "shipped": leaks["shipped"],
              "leak_examples": leaks["leak_examples"], "students": students}

    json_path = os.path.join(RESULTS, f"tutor-measure-{args.name}.json")
    with open(json_path, "w") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    hints_path = os.path.join(RESULTS, f"tutor-hints-{args.name}.json")
    with open(hints_path, "w") as handle:
        json.dump([row for row in leaks["rows"]], handle, ensure_ascii=False, indent=2)

    csv_path = os.path.join(RESULTS, "tutor-measure.csv")
    new = not os.path.exists(csv_path)
    with open(csv_path, "a") as handle:
        if new:
            handle.write("when,name,fake,commit,limit,rung,kind,status,n,mean_s,median_s,"
                         "p95_s,worst_s,raw_leaks,shipped_leaks\n")
        for rung in rungs:
            stats = leaks["per_rung"][rung]
            kinds = sorted({row["kind"] for row in leaks["rows"] if row["rung"] == rung})
            statuses = ",".join(f"{k}:{v}" for k, v in sorted(leaks["statuses"][rung].items()))
            handle.write(f"{report['when']},{args.name},{int(args.fake)},{report['commit']},"
                         f"{len(problems)},{rung},{'+'.join(kinds)},{statuses},{stats['n']},"
                         f"{stats['mean']},{stats['median']},{stats['p95']},{stats['worst']},"
                         f"{leaks['raw_leaks'] if rung == 3 else ''},"
                         f"{leaks['shipped_leaks'] if rung == 3 else ''}\n")

    print(f"\nWrote {json_path}\nWrote {hints_path}  (judge these 10 by hand)")


if __name__ == "__main__":
    main()
