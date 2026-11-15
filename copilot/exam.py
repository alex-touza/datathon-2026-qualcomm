"""Offline scorer used by both predict.py and eval.py (Linux/macOS main thread)."""

import http.client
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from .config import CONFIG

LETTERS = "ABCDE"
FALLBACK = "D"
TIME_LIMIT = min(60.0, float(CONFIG["TIME_LIMIT_SECONDS"]))
MARGIN = 3.0
SEED = 42
GRAMMAR = 'root ::= " "? [A-E]'
MARKER = re.compile(r"(?<!\S)([A-E])\s*[)）.\]:]\s*")


class DeadlineExceeded(BaseException):
    """Escape socket reads and all ordinary error handlers at the wall deadline."""


def _alarm(_signum, _frame):
    raise DeadlineExceeded


def _options(raw):
    matches = list(MARKER.finditer(raw))
    for start in range(len(matches) - 5, -1, -1):
        run = matches[start:start + 5]
        if "".join(match[1] for match in run) != LETTERS:
            continue
        values = [" ".join(raw[match.end():run[i + 1].start() if i < 4 else len(raw)].split())
                  for i, match in enumerate(run)]
        if all(values):
            return dict(zip(LETTERS, values))
    return {}


def _image(problem, input_path):
    base = Path(input_path).resolve().parent
    if isinstance(problem.get("image_path"), str):
        path = base / problem["image_path"]
        if path.is_file():
            return path
    # prepare_m3kang.py --save-images uses this naming. The second location
    # supports JSONL and images stored in the repository's data directories.
    stem = f"{problem['id']}_{problem.get('language', '')}"
    if Path(stem).name != stem:
        return None
    for folder in (base / "images", Path(__file__).resolve().parents[1] / "data/images"):
        for suffix in (".png", ".jpg", ".jpeg"):
            path = folder / (stem + suffix)
            if path.is_file():
                return path
    return None


def _ocr(image, language, deadline):
    cmd = [os.environ.get("OCR_BIN", "tesseract"), str(image), "stdout"]
    tessdata = os.environ.get("OCR_TESSDATA_DIR")
    if tessdata:
        cmd += ["--tessdata-dir", tessdata]
    cmd += ["-l", os.environ.get("OCR_LANG", "cat" if language == "cat" else "eng"), "--psm", "6"]
    # Leave room for killing and reaping Tesseract before the outer watchdog.
    timeout = min(4.0, deadline - time.monotonic() - 0.25)
    if timeout <= 0:
        return ""
    try:
        output = subprocess.run(cmd, capture_output=True, check=True, encoding="utf-8",
                                errors="replace", timeout=timeout,
                                env={**os.environ, "OMP_THREAD_LIMIT": "2"})
        return output.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _prompt(problem, input_path, deadline):
    question = problem.get("text", "").strip()
    supplied = problem.get("options") or {}
    options = {k: str(supplied[k]).strip() for k in LETTERS
               if supplied.get(k) is not None and str(supplied[k]).strip()}
    language = problem.get("language", "")
    figure = problem.get("text_only") is False
    raw = ""
    read_options = {}
    if figure or len(options) < 5 or not question:
        image = _image(problem, input_path)
        if image:
            raw = _ocr(image, language, deadline)
            read_options = _options(raw)
            options = {**read_options, **options}

    if figure and read_options:
        # DeepSeek is text-only; OCR can preserve labels but not visual geometry.
        content = raw
    elif options:
        content = question or raw
        content += "\n" + "\n".join(f"{k}. {options[k]}" for k in LETTERS if k in options)
    elif raw and MARKER.search(raw):
        content = raw
    else:
        return None

    instruction = "Tria A, B, C, D o E." if language == "cat" else "Choose A, B, C, D or E."
    return f"<｜User｜>{instruction}\n{content}<｜Assistant｜>Answer:"


def _completion(prompt, timeout):
    # Direct loopback HTTP: no proxies, DNS, redirects, keys or external calls.
    port = int(os.environ.get("DEEPSEEK_PORT", "18738"))
    body = json.dumps({"prompt": prompt, "n_predict": 2, "cache_prompt": False,
                       "temperature": 0, "seed": SEED, "grammar": GRAMMAR, "stream": False})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", "/completion", body, {"Content-Type": "application/json"})
        with conn.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"llama-server HTTP {response.status}")
            return json.loads(response.read(131072))
    finally:
        conn.close()


def solve(problem, *, input_path, solver="deepseek", index=None, k=0, alpha=None,
          time_limit=TIME_LIMIT):
    """Return one letter within the shared OCR + inference wall-clock budget."""
    start = time.monotonic()
    budget = min(TIME_LIMIT, time_limit) - MARGIN
    result = {"answer": FALLBACK, "status": "ok", "prompt_tokens": 0, "retrieved": ""}

    if budget <= 0:
        result.update(status="timeout", seconds=0.0)
        return result

    old_handler = signal.signal(signal.SIGALRM, _alarm)
    try:
        deadline = start + budget
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded
        signal.setitimer(signal.ITIMER_REAL, remaining)
        if solver == "baseline":
            from .pipeline import solve as baseline
            legacy = baseline(problem, index=index, k=k, exclude_id=problem["id"],
                              alpha=alpha, time_limit=min(time_limit, TIME_LIMIT))
            result.update(legacy)
            if result["status"] != "ok" or result["answer"] not in tuple(LETTERS):
                result["answer"] = FALLBACK
        else:
            prompt = _prompt(problem, input_path, deadline)
            if prompt is None:
                result["status"] = "missing_options"
            else:
                payload = _completion(prompt, timeout=max(0.001, deadline - time.monotonic()))
                letter = payload["content"].strip()
                if letter not in tuple(LETTERS):
                    raise ValueError("invalid model answer")
                result["prompt_tokens"] = payload.get("timings", {}).get("prompt_n", 0)
                result["answer"] = letter
    except (DeadlineExceeded, TimeoutError):
        result.update(answer=FALLBACK, status="timeout")
    except (OSError, http.client.HTTPException, RuntimeError, AttributeError,
            KeyError, TypeError, ValueError) as error:
        result.update(answer=FALLBACK, status=f"error: {error}")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)

    result["seconds"] = round(time.monotonic() - start, 2)
    return result
