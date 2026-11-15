#!/usr/bin/env bash
# Checks that must pass before the repository goes public.
#
#   ./tools/publish_checks.sh
#
# Exit code 0 means nothing printed. Run it after every push from any teammate,
# not just once: history can grow, and a later commit can add a file back.
set -uo pipefail
cd "$(dirname "$0")/.."
FAIL=0
note() { echo "FAIL  $1"; FAIL=1; }

echo "== M3Kang data tracked in the working tree"
git ls-files | grep -E '^data/' | grep -v -E '^data/(sample_problems\.jsonl|README\.md)$' \
  | while read -r f; do note "tracked data file: $f"; done

echo "== databases, models and problem dumps tracked"
git ls-files | grep -E '\.(db|gguf|jsonl|png)$' | grep -v '^data/sample_problems.jsonl$' \
  | while read -r f; do note "tracked binary/data: $f"; done

echo "== M3Kang data anywhere in the history"
git log --all --name-only --format= | sort -u \
  | grep -E '^data/(train|val)\.jsonl$|^data/images/|\.db$' \
  | while read -r f; do note "in history: $f"; done

echo "== secrets in any blob of any ref"
python3 - <<'PY'
import re, subprocess, sys
patterns = {
    "sk- token": re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    "AWS key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "GitHub token": re.compile(rb"gh[pou]_[A-Za-z0-9]{20,}"),
    "Google key": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "Bearer literal": re.compile(rb"Bearer\s+[A-Za-z0-9._-]{20,}"),
    "apiKey assignment": re.compile(rb"api[_\-]?key\s*[:=]\s*[\"'][A-Za-z0-9._-]{20,}", re.I),
    "Wi-Fi PSK": re.compile(rb"psk\s*[:=]\s*[\"'][^\"']{8,}", re.I),
}
skip = re.compile(r"\.(png|jpg|jpeg|gguf|bin|pdf|zip|ico|woff2?|ttf)$|^\.venv|^jaume/")
found = 0
objs = subprocess.run(["git", "rev-list", "--objects", "--all"],
                      capture_output=True, text=True).stdout.split("\n")
for line in objs:
    if " " not in line:
        continue
    sha, path = line.split(" ", 1)
    if skip.search(path):
        continue
    data = subprocess.run(["git", "cat-file", "-p", sha], capture_output=True).stdout
    if len(data) > 1_000_000:
        continue
    for name, rx in patterns.items():
        if rx.search(data):
            print(f"FAIL  {name} in {path} (blob {sha[:8]})")
            found = 1
sys.exit(found)
PY
[ $? -eq 0 ] || FAIL=1

echo "== relative links in the documentation resolve"
python3 - <<'PY'
import os, re, sys
missing = 0
for path in ("README.md", "CLAUDE.md", "docs/project-hub.md", "docs/datathon-guide.md", "LICENSE"):
    if not os.path.exists(path):
        continue
    text = open(path).read()
    for match in re.finditer(r"\]\(([^)\s]+)\)", text):
        link = match.group(1)
        if link.startswith(("http", "#", "mailto")):
            continue
        target = os.path.normpath(os.path.join(os.path.dirname(path), link.split("#")[0]))
        if not os.path.exists(target):
            print(f"FAIL  {path} links to missing {link}")
            missing = 1
sys.exit(missing)
PY
[ $? -eq 0 ] || FAIL=1

if [ "$FAIL" -eq 0 ]; then
  echo "== all publish checks passed"
else
  echo "== publish checks FAILED"
fi
exit "$FAIL"
