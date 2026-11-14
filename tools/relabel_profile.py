#!/usr/bin/env python3
"""Relabel historical tutor attempts after a change to copilot/topics.py.

The profile stores the topic at the time of each attempt, so old rows keep the
old label forever. This tool recomputes the topic from the problem text in the
index, updates each attempt, and rebuilds the aggregate topics table.

    python3 tools/relabel_profile.py                 # data/tutor.db, data/index.db
    python3 tools/relabel_profile.py --dry-run
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot import topics  # noqa: E402


def load_problems(path):
    """(id, language) -> problem, from the retrieval index JSON."""
    db = sqlite3.connect(path)
    out = {}
    for _key, problem, _vec in db.execute("SELECT key, problem, vec FROM problems"):
        p = json.loads(problem)
        out[(p["id"], p["language"])] = p
    db.close()
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default="data/tutor.db")
    p.add_argument("--index", default="data/index.db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not os.path.exists(args.profile):
        raise SystemExit(f"{args.profile} does not exist")
    problems = load_problems(args.index)
    db = sqlite3.connect(args.profile)
    rows = list(db.execute("SELECT rowid, id, language, topic FROM attempts"))
    changed = []
    for rowid, pid, language, old in rows:
        problem = problems.get((pid, language))
        if not problem:
            continue
        new = topics.classify(problem.get("text", ""))["topic"]
        if new != old:
            changed.append((pid, language, old, new))
            if not args.dry_run:
                db.execute("UPDATE attempts SET topic = ? WHERE rowid = ?", (new, rowid))

    if not args.dry_run:
        db.execute("DELETE FROM topics")
        for topic, language, tries, right, level in db.execute(
                "SELECT topic, language, COUNT(*), COALESCE(SUM(correct), 0), MAX(level) "
                "FROM attempts GROUP BY topic, language"):
            db.execute("INSERT INTO topics (topic, language, tries, right, level) "
                       "VALUES (?, ?, ?, ?, ?)", (topic, language, tries, right, level))
        db.commit()

    print(f"{len(rows)} attempts, {len(changed)} relabelled")
    for pid, language, old, new in changed:
        print(f"  {pid} {language}: {old} -> {new}")
    if not args.dry_run:
        print("\nCurrent profile:")
        for topic, language, tries, right in db.execute(
                "SELECT topic, language, tries, right FROM topics ORDER BY tries DESC, topic"):
            print(f"  {topic:14} {language}  {right}/{tries}")
    db.close()


if __name__ == "__main__":
    main()
