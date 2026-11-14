"""The teaching layer: answer checking, a hint ladder, diagnosis, adaptive practice.

Nothing in this file is imported by predict.py, so the official run is untouched.
Standard library only, like the rest of the board code.

The contract of the tutor
-------------------------
* The dataset ``label`` is the only oracle. A wrong answer is diagnosed from the
  distractors, never by asking the model.
* There is no free-form chat: the student presses buttons (a letter, a hint rung,
  reveal), and nothing else reaches the model.
* Every model hint is checked in code before it is shown. A hint that contains
  the correct value, a spelling of it, or the correct option letter is rejected
  and the student gets the rung-2 method instead.
* The model never picks the next problem and never grades anything.

Tutor problems come from the retrieval index, which holds solved training
problems only. Validation problems are therefore never used for practice, and
the same ``id`` in another language is always excluded before a search.
"""
import contextlib
import os
import random
import re
import sqlite3
import time
import unicodedata
import urllib.error

from . import topics
from .client import post
from .config import CONFIG
from .embed import embed
from .llm import format_problem

LETTERS = "ABCDE"
MAX_LEVEL = 7          # M3Kang levels run 0 (easiest) to 7 (hardest)
SEED = int(CONFIG["SEED"])

# --------------------------------------------------------------------------
# Text and numbers
# --------------------------------------------------------------------------

# A digit that follows a letter is part of a unit or a name ("cm2", "B2"), not a
# quantity. NFKC turns a superscript 2 into "2", so "81 cm²" would otherwise
# claim to contain a second number.
NUMBER_RE = re.compile(r"(?<![^\W\d_])-?\d+(?:\s*/\s*\d+)?(?:[.,]\d+)?")
# Number words in English and Catalan, tens included, so "eighty-one" and
# "vuitanta-un" are both read as 81 by _word_number_values.
WORD_VALUES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
    "un": 1, "una": 1, "dos": 2, "dues": 2, "tres": 3, "quatre": 4, "cinc": 5,
    "sis": 6, "set": 7, "vuit": 8, "nou": 9, "deu": 10, "onze": 11, "dotze": 12,
    "tretze": 13, "catorze": 14, "quinze": 15, "setze": 16, "disset": 17,
    "divuit": 18, "dinou": 19, "vint": 20, "trenta": 30, "quaranta": 40,
    "cinquanta": 50, "seixanta": 60, "setanta": 70, "vuitanta": 80,
    "noranta": 90, "cent": 100,
}


def normalize(text):
    text = unicodedata.normalize("NFKC", text or "")
    for dash in ("\u2212", "\u2013", "\u2014"):
        text = text.replace(dash, "-")
    return text.replace("\u2019", "'").lower()


def numbers(text):
    """Every number in the text as a float, fractions and decimal commas included."""
    found = []
    for match in NUMBER_RE.finditer(normalize(text)):
        token = match.group(0).replace(" ", "")
        try:
            if "/" in token:
                top, bottom = token.split("/")
                found.append(float(top) / float(bottom) if float(bottom) else float(top))
            else:
                found.append(float(token.replace(",", ".")))
        except ValueError:
            continue
    return found


def option_values(value):
    """What an option value is made of: its numbers, and its words when it has none."""
    nums = numbers(value)
    if nums:
        return nums, []
    words = [word for word in re.findall(r"[^\W\d_]{3,}", normalize(value))]
    return [], words


def word_number_values(text):
    """Every number written in words in the text, sums of joined words included."""
    found = []
    # A comma or a full stop ends a number: "eighty-one, vuitanta-un" is two numbers.
    for chunk in re.split(r"[^\w\s-]|_", normalize(text)):
        run = []
        for word in re.split(r"[\s-]+", chunk):
            if word in WORD_VALUES:
                run.append(WORD_VALUES[word])
            elif run:
                found.append(sum(run))
                run = []
        if run:
            found.append(sum(run))
    return found


# --------------------------------------------------------------------------
# The leak check: is this hint giving the answer away?
# --------------------------------------------------------------------------

def _close(a, b):
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


ANSWER_LETTER_RES = (
    # "the answer is C", "option C)", "resposta: B"
    re.compile(r"\b(?:answer|option|choice|correct|solution|resposta|opci[oó]|soluci[oó]|"
               r"correcte)\b[^.\n]{0,40}?\b([A-Ea-e])\b", re.IGNORECASE),
    # a bare option label, "C) 81" -- but not "vitamin C" style prose: require the bracket
    re.compile(r"(?:^|[\s(])([A-Ea-e])\)"),
)


def leak_check(hint, problem, label=None):
    """Return (leaks, reason). Deterministic, so it can be measured.

    A hint leaks when it contains the correct option's value (as a number, as a
    number word, or as a word) or names the correct option letter next to a word
    such as "answer". The check is deliberately generous: rejecting a good hint
    is a fallback, showing a leaky one is not.
    """
    label = label or problem.get("label")
    options = problem.get("options") or {}
    if not hint or not label or label not in options:
        return False, None
    hint_text = normalize(hint)
    hint_numbers = numbers(hint)
    hint_words = word_number_values(hint)

    correct_nums, correct_words = option_values(options[label])
    for value in correct_nums:
        if any(_close(value, seen) for seen in hint_numbers):
            return True, "value"
        if any(_close(value, seen) for seen in hint_words):
            return True, "value-word"
    # A purely worded option, such as "square" or "impossible": the whole value
    # has to be there, not just one of its words.
    if correct_words and all(
            re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", hint_text) for word in correct_words):
        return True, "value-word"

    for pattern in ANSWER_LETTER_RES:
        for match in pattern.finditer(hint):
            if match.group(1).upper() == label:
                return True, "letter"
    return False, None


# --------------------------------------------------------------------------
# Diagnosis from the distractors (rule-based, no model)
# --------------------------------------------------------------------------

DIAGNOSES = {
    "digit_swap": "Your value has the right digits in a different order. Re-read the number you copied.",
    "off_by_one": "You are one away. Check whether the last step should include the item at the end.",
    "power_of_ten": "You are a factor of ten out. Check the units, or where the decimal point goes.",
    "double": "Your value is twice the size the question asks for. Check which quantity you used.",
    "half": "Your value is half the size the question asks for. Check whether you used all the items.",
    "sign": "The size looks right but the direction is not. Check whether the quantity goes up or down.",
    "from_text": "This number is in the problem, but it is one of the quantities to use, not the quantity the question asks for.",
    "one_step": "This option is a combination of two numbers from the problem. Check whether the question asks for one more step.",
    "unit": "The number looks plausible but the unit is different. Convert both to the same unit first.",
}

DIAGNOSIS_NONE = ("Look once more at what the last sentence asks for; the options are "
                  "designed to catch the step before the answer.")


def _digits_key(value):
    return "".join(sorted(re.findall(r"\d", value)))


def diagnose(problem, choice):
    """A guess about *the answer*, not about the student, from the two options.

    Returns {"tag", "text", "wrong_value", "correct_value"} or tag None. The
    tags are hypotheses to show to a teacher; the tutor never stores them as a
    label of the student.
    """
    options = problem.get("options") or {}
    label = problem.get("label")
    if not options or label not in options or choice not in options or choice == label:
        return {"tag": None, "text": DIAGNOSIS_NONE, "wrong_value": None, "correct_value": None}

    right, wrong = options[label], options[choice]
    right_nums, _ = option_values(right)
    wrong_nums, _ = option_values(wrong)
    found = None

    if right_nums and wrong_nums and _close(right_nums[0], wrong_nums[0]):
        found = "unit"
    elif right_nums and wrong_nums:
        a, b = right_nums[0], wrong_nums[0]
        if _close(abs(b), abs(a)):
            found = "sign"
        elif _digits_key(right) == _digits_key(wrong):
            found = "digit_swap"
        elif _close(b, 2 * a):
            found = "double"
        elif a and _close(b, a / 2):
            found = "half"
        elif a and (_close(b, 10 * a) or (b != 0 and _close(b, a / 10))):
            found = "power_of_ten"
        elif _close(abs(b - a), 1):
            found = "off_by_one"

    if found is None and wrong_nums:
        text_numbers = numbers(problem.get("text", ""))
        if any(_close(wrong_nums[0], value) for value in text_numbers):
            found = "from_text"
        elif _is_combination(wrong_nums[0], text_numbers):
            found = "one_step"

    return {
        "tag": found,
        "text": DIAGNOSES.get(found, DIAGNOSIS_NONE) if found else DIAGNOSIS_NONE,
        "wrong_value": wrong,
        "correct_value": right,
    }


def _is_combination(value, pool, limit=8):
    """True if value is a sum, difference or product of two numbers in the text."""
    pool = [n for n in pool if abs(n) < 10 ** 4][:limit]
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            for made in (a + b, a - b, b - a, a * b):
                if _close(made, value) and not _close(value, a) and not _close(value, b):
                    return True
    return False


# --------------------------------------------------------------------------
# The student profile: a local SQLite file, nothing leaves the device
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    ts REAL, key TEXT, id TEXT, language TEXT, level INTEGER, topic TEXT,
    choice TEXT, correct INTEGER, rung INTEGER, seconds REAL
);
CREATE TABLE IF NOT EXISTS topics (
    topic TEXT, language TEXT, tries INTEGER, right INTEGER, level INTEGER,
    PRIMARY KEY (topic, language)
);
"""


class Profile:
    """Attempt history and per-topic counts. Open and close per call: threads share it."""

    def __init__(self, path):
        self.path = path
        with self._open() as db:
            db.executescript(SCHEMA)

    @contextlib.contextmanager
    def _open(self):
        """A short transaction per call: SQLite is the only thing two threads share."""
        db = sqlite3.connect(self.path, timeout=5)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def record(self, problem, choice, correct, rung=0, seconds=0.0, topic=None):
        topic = topic or topics.classify(problem.get("text", ""))["topic"]
        level = int(problem.get("level", 0))
        with self._open() as db:
            db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (time.time(), key_of(problem), problem.get("id"), problem.get("language"),
                        level, topic, choice, int(bool(correct)), int(rung), float(seconds)))
            db.execute(
                "INSERT INTO topics (topic, language, tries, right, level) VALUES (?, ?, 1, ?, ?) "
                "ON CONFLICT(topic, language) DO UPDATE SET tries = tries + 1, "
                "right = right + excluded.right, level = MAX(level, excluded.level)",
                (topic, problem.get("language"), int(bool(correct)), level))
        return {"topic": topic, "level": level}

    def summary(self):
        with self._open() as db:
            tries = db.execute("SELECT COUNT(*), COALESCE(SUM(correct), 0) FROM attempts").fetchone()
            rows = db.execute("SELECT topic, language, tries, right FROM topics "
                              "ORDER BY tries DESC, topic LIMIT 12").fetchall()
        return {
            "tries": tries[0],
            "right": tries[1],
            "topics": [{"topic": t, "language": lang, "tries": n, "right": r}
                       for t, lang, n, r in rows],
        }

    def reset(self):
        with self._open() as db:
            db.execute("DELETE FROM attempts")
            db.execute("DELETE FROM topics")


# --------------------------------------------------------------------------
# Rejected model hints, so the measure tool can report the raw leak rate
# --------------------------------------------------------------------------

def _first_sentence(text, limit=220):
    text = " ".join((text or "").split())
    cut = min([i for i in (text.find("."), text.find("\n")) if i > 0] or [len(text)])
    return text[:min(cut + 1, limit)].strip()


HINT_SYSTEM = (
    "You help a school student start a maths problem. Give one short hint, at most 20 "
    "words, that suggests the first step or the idea behind it. Never state the answer, "
    "never name an option letter, and never repeat any of the option values.")


def model_hint(problem, timeout=20.0, url=None):
    """Ask the 0.8B model for one short hint. Returns (hint, seconds)."""
    from .llm import URL as LLM_URL
    start = time.perf_counter()
    response = post(url or LLM_URL, {
        "messages": [
            {"role": "system", "content": HINT_SYSTEM},
            {"role": "user", "content": format_problem(problem) + "\nHint:"},
        ],
        "max_tokens": 64,
        "temperature": 0,
        "seed": SEED,
        "cache_prompt": True,
    }, timeout=timeout)
    text = response["choices"][0]["message"]["content"]
    return _first_sentence(text), time.perf_counter() - start


# --------------------------------------------------------------------------
# The tutor
# --------------------------------------------------------------------------

def key_of(problem):
    return f"{problem['id']}_{problem['language']}"


class Tutor:
    """Practice bank, hint ladder and adaptive selection, over one Index.

    The index holds solved training problems, so every problem the student sees
    has a label and an embedding, and no validation problem is spent on practice.
    """

    def __init__(self, index, profile_path=None, images=None, llm_timeout=20.0):
        self.rows = list(index.rows)
        self.by_key = {key_of(p): p for _key, p, _vec in self.rows}
        self.twin = {p["id"]: p for _key, p, _vec in self.rows
                     if p.get("language") == "eng"}
        self.vectors = {p["id"] + "|" + p["language"]: vec for _key, p, vec in self.rows}
        self.profile = Profile(profile_path) if profile_path else None
        self.images = images
        self.llm_timeout = llm_timeout
        self._rank_cache = {}

    # -- what the page may see ------------------------------------------------

    def public(self, problem):
        shown = {k: v for k, v in problem.items() if k != "label"}
        shown["key"] = key_of(problem)
        has_image = bool(self.images and _has_image(self.images, key_of(problem)))
        # The image is the source of options/figures; when the problem is
        # text-only and the OCR options are complete, it is redundant.
        shown["image"] = has_image and (not problem.get("text_only") or not problem.get("options"))
        shown["topic"] = topics.classify(problem.get("text", ""))["topic"]
        return shown

    def get(self, key):
        return self.by_key.get(key)

    # -- retrieval ------------------------------------------------------------

    def _vector(self, problem):
        query = self.twin.get(problem.get("id")) or problem
        return embed([format_problem(query)], query=True)[0]

    def rank(self, problem):
        """Every solved problem, most similar first, from the same id excluded."""
        cache_key = key_of(problem)
        if cache_key in self._rank_cache:
            return self._rank_cache[cache_key]
        query = self._vector(problem)
        scored = []
        for stored in self.by_key.values():
            if stored["id"] == problem["id"]:
                continue
            vec = self.vectors.get(stored["id"] + "|" + stored["language"])
            if vec is None:
                continue
            scored.append((sum(a * b for a, b in zip(query, vec)), stored))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if len(self._rank_cache) > 200:
            self._rank_cache.clear()
        self._rank_cache[cache_key] = scored
        return scored

    def similar(self, problem, k=5):
        return self.rank(problem)[:k]

    # -- the hint ladder ------------------------------------------------------

    def _worked_example(self, neighbour, score, easier=True):
        title = ("A similar, easier problem, already solved" if easier
                 else "Another solved problem at this level (there was nothing easier)")
        lines = [f"{title} (level {neighbour.get('level', '?')}):",
                 neighbour.get("text", "").strip()]
        options = neighbour.get("options") or {}
        for letter in LETTERS:
            if letter in options:
                lines.append(f"{letter}) {options[letter]}")
        label = neighbour.get("label")
        if label in options:
            lines.append(f"Solved as {label}) {options[label]}.")
        elif label:
            lines.append(f"Solved as {label}.")
        return "\n".join(lines), {"neighbour_id": neighbour.get("id"),
                                  "neighbour_key": key_of(neighbour),
                                  "neighbour_level": neighbour.get("level"),
                                  "score": round(score, 3)}

    def easier_neighbour(self, problem, k=60):
        """The nearest solved problem that is one or more levels easier.

        At level 0 there is nothing easier, so the nearest solved problem at the
        same level is used; the caller records ``easier: False`` in the detail.
        """
        level = problem.get("level", 0)
        topic = topics.classify(problem.get("text", ""))["topic"]
        try:
            ranked = self.rank(problem)[:k]
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            return None, True
        easier = [(s, p) for s, p in ranked if p.get("level", 0) < level]
        easier_flag = True
        if not easier:
            easier = [(s, p) for s, p in ranked if p.get("level", 0) == level]
            easier_flag = False
        if not easier:
            return None, easier_flag
        same_language = [(s, p) for s, p in easier if p.get("language") == problem.get("language")]
        same_topic = [(s, p) for s, p in same_language
                      if topics.classify(p.get("text", ""))["topic"] == topic]
        # Strict same-topic: a different-topic worked example is not a hint.
        if same_topic:
            return same_topic[0], easier_flag
        return None, easier_flag

    def hint(self, problem, rung):
        """One rung. Returns {"rung", "kind", "text", "status", "seconds", "detail"}."""
        start = time.perf_counter()
        rung = max(1, min(4, int(rung)))
        if rung == 1:
            found, was_easier = self.easier_neighbour(problem)
            if not found:
                return self._method_hint(problem, 1, start, status="fallback",
                                         detail={"reason": "no neighbour to show"})
            score, neighbour = found
            text, detail = self._worked_example(neighbour, score, was_easier)
            detail["easier"] = was_easier
            return {"rung": 1, "kind": "easier_problem", "text": text, "status": "ok",
                    "seconds": round(time.perf_counter() - start, 2), "detail": detail}
        if rung == 2:
            return self._method_hint(problem, 2, start)
        if rung == 3:
            return self._model_hint(problem, start)
        return self.reveal(problem, start)

    def _method_hint(self, problem, rung, start, status="ok", detail=None):
        found = topics.classify(problem.get("text", ""))
        try:
            neighbours = [p for _s, p in self.rank(problem)[:5]]
            shared = topics.shared_topics(neighbours)
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            # The embeddings are down: the keywords still work, so the method
            # is still useful. Never lose rung 2 to a dead server.
            shared = None
        lines = [f"This looks like a {found['topic']} problem.", found["method"]]
        if shared and shared != found["topic"]:
            lines.append(f"Its nearest neighbours are mostly {shared} problems.")
        detail = dict(detail or {})
        detail.update({"topic": found["topic"], "matched": found["matched"],
                       "shared_topic": shared})
        return {"rung": rung, "kind": "method", "text": "\n".join(lines), "status": status,
                "seconds": round(time.perf_counter() - start, 2), "detail": detail}

    def _model_hint(self, problem, start):
        effective = max(1.0, self.llm_timeout)
        try:
            raw, model_seconds = model_hint(problem, timeout=effective)
        except (OSError, urllib.error.URLError, KeyError, ValueError) as error:
            hint = self._method_hint(problem, 3, start, status="unavailable",
                                     detail={"error": str(error)})
            hint["kind"] = "method"
            return hint
        leaks, reason = leak_check(raw, problem)
        if leaks:
            hint = self._method_hint(problem, 3, start, status="rejected",
                                     detail={"reason": reason, "raw": raw,
                                             "model_seconds": round(model_seconds, 2)})
            hint["detail"]["leak"] = reason
            return hint
        return {"rung": 3, "kind": "model", "text": raw, "status": "ok",
                "seconds": round(time.perf_counter() - start, 2),
                "detail": {"raw": raw, "model_seconds": round(model_seconds, 2)}}

    def reveal(self, problem, start=None):
        start = start or time.perf_counter()
        options = problem.get("options") or {}
        label = problem.get("label")
        text = (f"The correct option is {label}) {options[label]}" if label in options
                else f"The correct option is {label}.")
        return {"rung": 4, "kind": "solution", "text": text, "status": "ok",
                "seconds": round(time.perf_counter() - start, 2),
                "detail": {"label": label}}

    # -- checking and diagnosis ----------------------------------------------

    def check(self, problem, choice):
        label = problem.get("label")
        correct = choice == label
        options = problem.get("options") or {}
        result = {"correct": correct, "label": label,
                  "label_value": options.get(label),
                  "chosen_value": options.get(choice)}
        if not correct:
            result["diagnosis"] = diagnose(problem, choice)
        return result

    # -- adaptive practice ---------------------------------------------------

    def first_problem(self, level=1, language="eng", seed=None):
        pool = [p for _k, p, _v in self.rows
                if p.get("level") == level and p.get("language") == language]
        if not pool:
            pool = [p for _k, p, _v in self.rows if p.get("level") == level]
        if not pool:
            pool = [p for _k, p, _v in self.rows]
        # The official run uses SEED; the tutor wants a different topic per
        # session, otherwise strict same-topic practice always starts geometry.
        (random.Random(seed) if seed is not None else random.Random()).shuffle(pool)
        return pool[0] if pool else None

    def next_problem(self, problem, correct, seen_ids=(), prefer_language=None):
        """Right: a neighbour one level up. Wrong: the same topic one level down.

        The choices are tried in this order, so a missing level never drags the
        student backwards after a right answer:

        1. the target level, in the same topic;
        2. the target level;
        3. the nearest level in the same topic, in the direction of travel;
        4. the nearest level in the direction of travel;
        5. the nearest level overall.
        """
        start = time.perf_counter()
        level = int(problem.get("level", 0))
        target = max(0, min(MAX_LEVEL, level + (1 if correct else -1)))
        language = prefer_language or problem.get("language")
        topic = topics.classify(problem.get("text", ""))["topic"]
        seen = set(seen_ids) | {problem.get("id")}

        ranked = self.rank(problem)
        same_language = [(s, p) for s, p in ranked if p.get("language") == language]
        pool = same_language or ranked
        pool = [(s, p) for s, p in pool if p.get("id") not in seen]
        same_topic = [(s, p) for s, p in pool
                      if topics.classify(p.get("text", ""))["topic"] == topic]

        def rank_of(pairs):
            return sorted(pairs, key=lambda pair: (abs(pair[1].get("level", 0) - target), -pair[0]))

        def towards(pairs):
            """Levels beyond the current one in the direction the student is moving."""
            return [(s, p) for s, p in pairs
                    if (p.get("level", 0) > level if correct else p.get("level", 0) < level)]

        def by_distance(pairs):
            """Nearest to the target level; the topic only breaks ties."""
            def key(pair):
                stored = pair[1]
                return (abs(stored.get("level", 0) - target),
                        topics.classify(stored.get("text", ""))["topic"] != topic,
                        -pair[0])
            return sorted(pairs, key=key)

        attempts = [
            [pair for pair in same_topic if pair[1].get("level") == target],
            [pair for pair in pool if pair[1].get("level") == target],
            rank_of(towards(same_topic)),
            rank_of(towards(pool)),
            by_distance(pool),
        ]
        chosen = next((pairs[0] for pairs in attempts if pairs), None)
        if chosen is None:
            fallback = self.first_problem(level=target, language=language)
            if fallback is None:
                return None
            return {"problem": self.public(fallback), "target_level": target,
                    "topic": topics.classify(fallback.get("text", ""))["topic"],
                    "reason": "level only, retrieval had no candidates",
                    "seconds": round(time.perf_counter() - start, 2)}
        score, nxt = chosen
        nxt_topic = topics.classify(nxt.get("text", ""))["topic"]
        if nxt_topic != topic:
            reason = "neighbour, different topic"
        elif nxt.get("level") == target:
            reason = "same topic, one level up" if correct else "same topic, one level down"
        else:
            reason = "same topic, nearest level available"
        return {"problem": self.public(nxt), "target_level": target, "topic": nxt_topic,
                "reason": reason, "score": round(score, 3),
                "seconds": round(time.perf_counter() - start, 2)}


def _has_image(images, key):
    return os.path.exists(os.path.join(images, key + ".png"))


def image_mime(data):
    """The real image type: M3Kang's .png files are JPEG bytes."""
    return "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"


# --------------------------------------------------------------------------
# One student, one problem at a time
# --------------------------------------------------------------------------

class Session:
    """What the page shows, and what the student is doing right now.

    The servers serialise actions: the page disables its buttons and polls
    /api/state while ``busy`` is true, so only one hint or answer is in flight.
    Nothing here needs a lock, and a 20-second model hint never blocks a poll.
    """

    def __init__(self, tutor, level=0, language="eng"):
        self.tutor = tutor
        self.level = int(level)
        self.language = language
        self.seen = []
        self.counts = {"tries": 0, "right": 0}
        self.profile_summary = tutor.profile.summary() if tutor.profile else None
        self._clear()
        self.start()

    def _clear(self):
        self.problem = None
        self.answered = False
        self.chosen = None
        self.result = None
        self.hints = []
        self.revealed = False
        self.revealed_label = None
        self.solution = None
        self.pending = None
        self.last = None

    def start(self, level=None, language=None):
        self.level = int(self.level if level is None else level)
        self.language = language or self.language
        self._clear()
        self.seen = []
        self.counts = {"tries": 0, "right": 0}
        self.problem = self.tutor.first_problem(level=self.level, language=self.language)
        if self.problem:
            # The index may not have this language yet; practise in what it has.
            self.language = self.problem.get("language", self.language)
        return self.state()

    def current_key(self):
        return key_of(self.problem) if self.problem else None

    def state(self):
        problem = self.problem
        public = self.tutor.public(problem) if problem else None
        diagnosis = None
        if self.result and not self.result["correct"] and not self.revealed:
            diagnosis = {"tag": self.result["diagnosis"]["tag"],
                         "text": self.result["diagnosis"]["text"]}
        return {
            "problem": public,
            "level": int(problem.get("level", 0)) if problem else None,
            "language": problem.get("language") if problem else None,
            "topic": public.get("topic") if public else None,
            "answered": self.answered,
            "chosen": self.chosen,
            "correct": bool(self.result["correct"]) if self.result else None,
            "diagnosis": diagnosis,
            "hints": [{"rung": hint["rung"], "kind": hint["kind"], "text": hint["text"],
                       "status": hint["status"], "seconds": hint["seconds"]} for hint in self.hints],
            "next_rung": (len(self.hints) + 1 if len(self.hints) < 3 else None)
            if not self.answered and not self.revealed else None,
            "revealed": self.revealed,
            "revealed_label": self.revealed_label,
            "solution": self.solution,
            "next_ready": bool(self.answered or self.revealed) and self.pending is not None,
            "progress": dict(self.counts),
            "profile": self.profile_summary,
            "last": self.last,
        }

    def _pick_next(self, problem, correct):
        """The adaptive next problem, or a same-level one if embeddings are down."""
        try:
            return self.tutor.next_problem(problem, correct, seen_ids=self.seen,
                                           prefer_language=self.language)
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            fallback = self.tutor.first_problem(level=int(problem.get("level", 0)),
                                                language=self.language)
            if fallback is None or fallback.get("id") in self.seen:
                return None
            return {"problem": self.tutor.public(fallback),
                    "target_level": int(fallback.get("level", 0)),
                    "topic": topics.classify(fallback.get("text", ""))["topic"],
                    "reason": "no embeddings: another problem at the same level",
                    "seconds": 0.0}

    def answer(self, letter):
        letter = (letter or "").strip().upper()
        if self.problem is None:
            return {"error": "no problem is on screen"}
        if self.answered or self.revealed:
            return {"error": "this problem is already checked; press next"}
        if letter not in LETTERS:
            return {"error": "choose A to E"}
        started = time.perf_counter()
        result = self.tutor.check(self.problem, letter)
        self.answered = True
        self.chosen = letter
        self.result = result
        self.counts["tries"] += 1
        self.counts["right"] += int(result["correct"])
        seconds = time.perf_counter() - started
        if self.tutor.profile:
            self.tutor.profile.record(self.problem, letter, result["correct"],
                                      rung=len(self.hints), seconds=seconds)
            self.profile_summary = self.tutor.profile.summary()
        # Decide the next problem now, while the toast is on screen, so "next"
        # costs nothing. The student never sees it until they ask.
        self.pending = self._pick_next(self.problem, result["correct"])
        self.last = {"kind": "answer", "choice": letter, "correct": result["correct"]}
        return self.state()

    def hint(self, rung=None):
        if self.problem is None:
            return {"error": "no problem is on screen"}
        if self.answered or self.revealed:
            return {"error": "this problem is already checked; press next"}
        asked = len(self.hints) + 1
        rung = asked if rung is None else int(rung)
        if rung != asked:
            return {"error": f"ask for rung {asked} first"}
        if asked == 4:
            return {"error": "the last rung is the solution: press reveal"}
        try:
            hint = self.tutor.hint(self.problem, rung)
        except (OSError, urllib.error.URLError) as error:
            return {"error": f"the model is not answering: {error}"}
        self.hints.append(hint)
        self.last = {"kind": "hint", "rung": hint["rung"], "status": hint["status"]}
        return self.state()

    def reveal(self):
        if self.problem is None:
            return {"error": "no problem is on screen"}
        if self.revealed:
            return {"error": "already revealed"}
        solution = self.tutor.reveal(self.problem)
        self.solution = solution["text"]
        self.revealed = True
        self.revealed_label = self.problem.get("label")
        if not self.answered:
            # Asking for the solution is not a wrong answer: it is not counted
            # against the student in the profile, only in the session.
            self.counts["tries"] += 1
        self.pending = self.pending or self._pick_next(self.problem, False)
        self.last = {"kind": "reveal"}
        return self.state()

    def next(self):
        if self.problem is not None:
            self.seen.append(self.problem["id"])
        nxt = self.pending
        if not nxt:
            # A skip, or an empty pending: pick something now.
            nxt = self._pick_next(self.problem, False) if self.problem else None
        if not nxt:
            return {"error": "no more problems"}
        full = self.tutor.get(nxt["problem"]["key"])
        if full is None:
            return {"error": "the next problem is missing from the index"}
        was = self.level
        self._clear()
        self.problem = full
        self.level = int(full.get("level", was))
        self.last = {"kind": "next"}
        return self.state()


# --------------------------------------------------------------------------
# Small helpers the servers share
# --------------------------------------------------------------------------

def summary_line(hint):
    """A one-line log of a rung, for the measure tool and the board log."""
    detail = hint.get("detail") or {}
    extra = detail.get("reason") or detail.get("topic") or ""
    return (f"rung {hint['rung']} {hint['kind']:<14} {hint['status']:<11} "
            f"{hint['seconds']:>5.2f}s {extra}")
