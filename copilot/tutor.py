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
from .rag import keyword_score

LETTERS = "ABCDE"
MAX_LEVEL = 7          # M3Kang levels run 0 (easiest) to 7 (hardest)
SEED = int(CONFIG["SEED"])
# A run of clean answers worth celebrating; the page owns the wording.
STREAK_MILESTONES = (3, 5, 10, 25, 50, 100)

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
    re.compile(r"(?:^|[\s(])([A-E])\)"),
)


def leak_check(hint, problem, label=None):
    """Return (leaks, reason). Deterministic, so it can be measured.

    A hint leaks when it *states* the correct option's value: the value itself
    as a number or number word, or -- for an option written as an expression or
    a sequence -- the option text itself, or the correct option letter next to
    a word such as "answer".

    The check is deliberately narrow in what it calls a leak, because a false
    positive throws away a good hint:
      * numbers the problem already gives cannot leak;
      * `48/6` in the working is not the value `8`;
      * `2+0+0+8` is not the option `2×0×0×8`;
      * `abc` inside the working is not the option `√abc`.
    """
    label = label or problem.get("label")
    options = problem.get("options") or {}
    if not hint or not label or label not in options:
        return False, None
    option = options[label]
    hint_text = normalize(hint)
    hint_raw = re.sub(r"\s+", "", hint_text)
    wanted_raw = re.sub(r"\s+", "", normalize(option))
    # The numeric core of the option: `81 cm2` -> `81`, `49/10 kg` -> `49/10`.
    core = re.sub(r"[a-z%°]+\d*$", "", wanted_raw)

    # Numbers the problem already gives cannot be a leak: the hint has to use
    # them. Without this, "the 4 meter fence" or "one cycle" is rejected for
    # repeating a quantity that every correct solution must repeat.
    text = problem.get("text") or ""
    given = numbers(text) + word_number_values(text)
    explicit = bool(re.search(r"\b(?:answer|result|correct|solution|resposta|resultat|"
                              r"soluci[oó]|correcte|opci[oó])\b", hint_text))

    correct_nums, correct_words = option_values(option)
    numeric_option = bool(re.fullmatch(r"-?\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)?", core))

    if numeric_option:
        targets = _value_targets(core)
        tokens = _NUM_TOKEN.findall(hint_text)
        for token in tokens:
            token = token.replace(",", ".").replace(" ", "")
            wanted = core.replace(",", ".")
            if token == wanted:
                return True, "value"
            if "/" in token or "/" in wanted:
                continue
            try:
                if any(_close(float(token), target) for target in targets):
                    return True, "value"
            except ValueError:
                continue
        if float_targets := [t for t in targets if float(t).is_integer() and abs(t) <= 100]:
            for value in float_targets:
                if not explicit and any(_close(value, seen) for seen in given):
                    continue
                if any(_close(value, seen) for seen in word_number_values(hint)):
                    return True, "value-word"
    else:
        # An expression, a sequence ("1-2-4-3-5"), or a symbolic value
        # ("√abc"): only the option text itself leaks, as a whole token, so
        # that `4°` is not found inside `324°`.
        if wanted_raw and re.search(rf"(?<![\w.]){re.escape(wanted_raw)}(?![\w.])", hint_raw):
            return True, "value"
        if correct_words and not re.search(r"[+\-*/×÷^√]", wanted_raw) and all(
                re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", hint_text)
                for word in correct_words):
            return True, "value-word"

    for pattern in ANSWER_LETTER_RES:
        for match in pattern.finditer(hint):
            if match.group(1).upper() == label:
                return True, "letter"
    return False, None


_NUM_TOKEN = re.compile(r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?")


def _value_targets(wanted):
    """The value an option stands for, including the order-flipped reading.

    `55 16` is the OCR order-flipped form of `16/55`, so both readings are
    candidates. Individual digits of a damaged multi-number option are *not*
    candidates: that would call `2` a leak of `12 2 3 4`.
    """
    targets = []
    split = re.fullmatch(r"(-?\d+(?:[.,]\d+)?)/(\d+(?:[.,]\d+)?)", wanted)
    if split:
        top = float(split.group(1).replace(",", "."))
        bottom = float(split.group(2).replace(",", "."))
        targets.append(top / bottom if bottom else top)
        if top:
            targets.append(bottom / top)
    else:
        try:
            targets.append(float(wanted.replace(",", ".")))
        except ValueError:
            pass
    seen, unique = set(), []
    for value in targets:
        key = round(value, 9)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


_ARITHMETIC_HINT = re.compile(r"\d\s+[+\-×÷*/]\s+\d|\d\s*[+\-×÷*/]\s*\d\s*=")


def grounding_check(hint, problem):
    """Return (ok, reason). Is this hint built only from the problem's own numbers?

    A nudge should point at what to look at, not compute. The small model
    sometimes borrows a number from a problem it has seen before: a hint that
    says "she starts with 10 sheets" for a problem with 3 + 2 + 2 sheets is
    fluent, plausible and wrong. A number the problem does not give is the
    cheapest signal of that, so such a hint falls back to the method hint
    instead of teaching something false.
    """
    text = problem.get("text") or ""
    given = numbers(text) + word_number_values(text)
    if _ARITHMETIC_HINT.search(hint):
        return False, "arithmetic in a hint"
    for value in numbers(hint) + word_number_values(hint):
        if value in (1.0, 2.0):
            # "one hour", "two steps": ordinals and step markers, not claims.
            continue
        if not any(_close(value, seen) for seen in given):
            return False, f"number {value:g} is not in the problem"
    return True, ""



# --------------------------------------------------------------------------
# Diagnosis from the distractors (rule-based, no model)
# --------------------------------------------------------------------------

DIAGNOSES = {
    "eng": {
        "digit_swap": "Your value has the right digits in a different order. Re-read the number you copied.",
        "off_by_one": "You are one away. Check whether the last step should include the item at the end.",
        "power_of_ten": "You are a factor of ten out. Check the units, or where the decimal point goes.",
        "double": "Your value is twice the size the question asks for. Check which quantity you used.",
        "half": "Your value is half the size the question asks for. Check whether you used all the items.",
        "sign": "The size looks right but the direction is not. Check whether the quantity goes up or down.",
        "from_text": "This number is in the problem, but it is one of the quantities to use, not the quantity the question asks for.",
        "one_step": "This option is a combination of two numbers from the problem. Check whether the question asks for one more step.",
        "unit": "The number looks plausible but the unit is different. Convert both to the same unit first.",
    },
    "cat": {
        "digit_swap": "El teu valor té les xifres correctes en un altre ordre. Torna a llegir el nombre que has copiat.",
        "off_by_one": "Et falta o et sobra una unitat. Comprova si l'últim pas ha d'incloure l'element del final.",
        "power_of_ten": "El teu valor es desvia per un factor de deu. Comprova les unitats o on va la coma decimal.",
        "double": "El teu valor és el doble del que demana la pregunta. Comprova quina quantitat has fet servir.",
        "half": "El teu valor és la meitat del que demana la pregunta. Comprova si has fet servir tots els elements.",
        "sign": "La mida sembla bé però la direcció no. Comprova si la quantitat puja o baixa.",
        "from_text": "Aquest nombre surt al problema, però és una de les dades que has de fer servir, no la quantitat que demana la pregunta.",
        "one_step": "Aquesta opció combina dos nombres del problema. Comprova si la pregunta demana un pas més.",
        "unit": "El nombre sembla possible però la unitat és diferent. Passa els dos a la mateixa unitat abans de calcular.",
    },
}

DIAGNOSIS_NONE = {
    "eng": ("Look once more at what the last sentence asks for; the options are "
            "designed to catch the step before the answer."),
    "cat": ("Torna a mirar què demana l'última frase; les opcions estan fetes per "
            "aturar-se al pas anterior a la resposta."),
}



def _digits_key(value):
    return "".join(sorted(re.findall(r"\d", value)))


def diagnose(problem, choice, language="eng"):
    """A guess about *the answer*, not about the student, from the two options.

    Returns {"tag", "text", "wrong_value", "correct_value"} or tag None. The
    tags are hypotheses to show to a teacher; the tutor never stores them as a
    label of the student. ``language`` only picks the language of ``text``.
    """
    words = DIAGNOSES.get(language, DIAGNOSES["eng"])
    nothing = DIAGNOSIS_NONE.get(language, DIAGNOSIS_NONE["eng"])
    options = problem.get("options") or {}
    label = problem.get("label")
    if not options or label not in options or choice not in options or choice == label:
        return {"tag": None, "text": nothing, "wrong_value": None, "correct_value": None}

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
        "text": words.get(found, nothing) if found else nothing,
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
    choice TEXT, correct INTEGER, rung INTEGER, seconds REAL, points REAL DEFAULT 0
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
            # A profile made before points existed: add the column in place.
            try:
                db.execute("ALTER TABLE attempts ADD COLUMN points REAL DEFAULT 0")
            except sqlite3.OperationalError:
                pass

    @contextlib.contextmanager
    def _open(self):
        """A short transaction per call: SQLite is the only thing two threads share."""
        db = sqlite3.connect(self.path, timeout=5)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def record(self, problem, choice, correct, rung=0, seconds=0.0, topic=None, points=0.0):
        topic = topic or topics.classify(problem.get("text", ""))["topic"]
        level = int(problem.get("level", 0))
        with self._open() as db:
            db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (time.time(), key_of(problem), problem.get("id"), problem.get("language"),
                        level, topic, choice, int(bool(correct)), int(rung), float(seconds),
                        float(points)))
            db.execute(
                "INSERT INTO topics (topic, language, tries, right, level) VALUES (?, ?, 1, ?, ?) "
                "ON CONFLICT(topic, language) DO UPDATE SET tries = tries + 1, "
                "right = right + excluded.right, level = MAX(level, excluded.level)",
                (topic, problem.get("language"), int(bool(correct)), level))
        return {"topic": topic, "level": level}

    def summary(self):
        with self._open() as db:
            tries = db.execute("SELECT COUNT(*), COALESCE(SUM(correct), 0), "
                               "COALESCE(SUM(points), 0) FROM attempts").fetchone()
            rows = db.execute("SELECT topic, language, tries, right FROM topics "
                              "ORDER BY tries DESC, topic LIMIT 12").fetchall()
        return {
            "tries": tries[0],
            "right": tries[1],
            "points": round(tries[2], 1),   # kept for the profile, never shown as a score
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

HINT_SYSTEM_CA = (
    "Ajudes un estudiant a començar un problema de matemàtiques. Dóna una pista curta, "
    "de com a màxim 20 paraules, que suggereixi el primer pas o la idea. No diguis mai "
    "la resposta, no anomenis cap lletra d'opció i no repeteixis cap dels valors de les "
    "opcions. Respon en català.")

HINT_SYSTEMS = {"eng": HINT_SYSTEM, "cat": HINT_SYSTEM_CA}

# A small model drifts into English even when asked not to. A Catalan hint that
# comes back in English is still not "everything on the page in Catalan", so the
# caller swaps it for the deterministic Catalan method hint.
_ENGLISH_MARKERS = {
    "the", "and", "your", "you", "this", "that", "with", "have", "first", "step",
    "how", "what", "number", "times", "then", "from", "which", "will", "would",
    "can", "should", "each", "total", "add", "subtract", "multiply", "divide",
}
_CATALAN_MARKERS = {
    "el", "la", "els", "les", "de", "del", "dels", "que", "amb", "per", "una",
    "un", "com", "quan", "primer", "pas", "nombre", "quant", "quin", "aquest",
    "aquesta", "després", "suma", "resta", "multiplica", "divideix", "total",
}


def language_of(text):
    """eng, cat, or None when there is not enough prose to tell."""
    words = re.findall(r"[^\W\d_]+", normalize(text))
    english = sum(word in _ENGLISH_MARKERS for word in words)
    catalan = sum(word in _CATALAN_MARKERS for word in words)
    if english == catalan:
        return None
    return "eng" if english > catalan else "cat"


def looks_english(text):
    """True when a Catalan hint came back mostly in English."""
    words = re.findall(r"[^\W\d_]+", normalize(text))
    english = sum(word in _ENGLISH_MARKERS for word in words)
    catalan = sum(word in _CATALAN_MARKERS for word in words)
    return english >= 2 and english > catalan


def model_hint(problem, timeout=20.0, url=None, language="eng"):
    """Ask the 0.8B model for one short hint. Returns (hint, seconds).

    For Catalan the caller passes the English version of the same problem: the
    small model follows English maths far better, and the system prompt asks it
    to answer in Catalan.
    """
    from .llm import URL as LLM_URL
    start = time.perf_counter()
    response = post(url or LLM_URL, {
        "messages": [
            {"role": "system", "content": HINT_SYSTEMS.get(language, HINT_SYSTEM)},
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

    def __init__(self, index, profile_path=None, images=None, llm_timeout=20.0,
                 solutions_path=None):
        self.rows = list(index.rows)
        self.by_key = {key_of(p): p for _key, p, _vec in self.rows}
        self.twin = {p["id"]: p for _key, p, _vec in self.rows
                     if p.get("language") == "eng"}
        # Every row is compared through its English twin's vector: bge-small is
        # an English model, and the Catalan text of the same problem would
        # otherwise point at other Catalan problems instead of at the topic.
        twin_vectors = {p["id"]: vec for _key, p, vec in self.rows
                        if p.get("language") == "eng"}
        self.vectors = {}
        for _key, p, vec in self.rows:
            self.vectors[p["id"] + "|" + p["language"]] = twin_vectors.get(p["id"], vec)
        self.keyword_weight = float(CONFIG.get("RAG_KEYWORD_WEIGHT") or 0.0)
        self.min_score = float(CONFIG.get("RAG_MIN_SCORE") or 0.0)
        self.profile = Profile(profile_path) if profile_path else None
        self.images = images
        self.llm_timeout = llm_timeout
        self.solutions = self._load_solutions(
            solutions_path or CONFIG.get("SOLUTIONS_DB", "data/explanations.db"))
        self._rank_cache = {}

    @staticmethod
    def _load_solutions(path):
        """Verified precomputed parts, keyed id|language. Read-only."""
        if not path or not os.path.exists(path):
            return {}
        try:
            db = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=5)
            rows = list(db.execute(
                "SELECT key, part1, part2, part3 FROM solutions WHERE verified = 1"))
            db.close()
        except sqlite3.Error:
            return {}
        return {key.replace("|", "_"): (part1, part2, part3)
                for key, part1, part2, part3 in rows}

    # -- what the page may see ------------------------------------------------

    def public(self, problem, language=None):
        shown = {k: v for k, v in problem.items() if k != "label"}
        shown["key"] = key_of(problem)
        # The image is the statement, so it must be in the page's language even
        # when the problem text comes from another one (Catalan twin by id).
        image_key = key_of(problem)
        image_language = problem.get("language")
        if language and language != problem.get("language"):
            twin_key = f"{problem['id']}_{language}"
            if self.images and _has_image(self.images, twin_key):
                image_key = twin_key
                image_language = language
        shown["image_key"] = image_key
        shown["image_language"] = image_language
        shown["image"] = bool(self.images and _has_image(self.images, image_key))
        shown["topic"] = topics.classify(problem.get("text", ""))["topic"]
        return shown

    def get(self, key):
        return self.by_key.get(key)

    # -- retrieval ------------------------------------------------------------

    def rank(self, problem):
        """Every solved problem, most similar first, from the same id excluded.

        Cosine similarity plus a share of the word overlap. The query and every
        stored problem are read through their English twin when there is one,
        which is what makes Catalan practice retrieve the right topic.
        """
        cache_key = key_of(problem)
        if cache_key in self._rank_cache:
            return self._rank_cache[cache_key]
        query = self.twin.get(problem.get("id")) or problem
        query_text = format_problem(query)
        vector = embed([query_text], query=True)[0]
        scored = []
        for stored in self.by_key.values():
            if stored["id"] == problem["id"]:
                continue
            vec = self.vectors.get(stored["id"] + "|" + stored["language"])
            if vec is None:
                continue
            english = self.twin.get(stored["id"]) or stored
            score = (sum(a * b for a, b in zip(vector, vec))
                     + self.keyword_weight * keyword_score(query_text, format_problem(english)))
            if self.min_score and score < self.min_score:
                continue
            scored.append((score, stored))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if len(self._rank_cache) > 200:
            self._rank_cache.clear()
        self._rank_cache[cache_key] = scored
        return scored

    def similar(self, problem, k=5):
        return self.rank(problem)[:k]

    # -- the hint ladder ------------------------------------------------------

    def _worked_example(self, neighbour, score, easier=True, language="eng"):
        if language == "cat":
            title = ("Un problema semblant, més fàcil i ja resolt" if easier
                     else "Un altre problema resolt d'aquest nivell (no n'hi havia cap de més fàcil)")
            level_text, solved_text, solved_letter = "nivell", "Resolt com a", "Resolt com a"
        else:
            title = ("A similar, easier problem, already solved" if easier
                     else "Another solved problem at this level (there was nothing easier)")
            level_text, solved_text, solved_letter = "level", "Solved as", "Solved as"
        lines = [f"{title} ({level_text} {neighbour.get('level', '?')}):",
                 neighbour.get("text", "").strip()]
        options = neighbour.get("options") or {}
        for letter in LETTERS:
            if letter in options:
                lines.append(f"{letter}) {options[letter]}")
        label = neighbour.get("label")
        if label in options:
            lines.append(f"{solved_text} {label}) {options[label]}.")
        elif label:
            lines.append(f"{solved_letter} {label}.")
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

    def hint(self, problem, rung, language="eng"):
        """One rung. Returns {"rung", "kind", "text", "status", "seconds", "detail"}."""
        start = time.perf_counter()
        rung = max(1, min(4, int(rung)))
        cached = self.solutions.get(key_of(problem))
        if cached:
            part1, part2, part3 = cached
            # A row generated in the other language would read as a glitch on
            # the page; the ladder is better than the wrong language.
            if language_of(" ".join(cached)) not in (None, problem.get("language")):
                cached = None
        if cached:
            part1, part2, part3 = cached
            if rung == 1:
                text = part1
            elif rung == 2:
                text = "\n\n".join(part for part in (part1, part2) if part)
            else:
                text = "\n\n".join(part for part in (part1, part2, part3) if part)
            if text.strip():
                return {"rung": rung, "kind": "solution", "text": text, "status": "ok",
                        "seconds": round(time.perf_counter() - start, 2),
                        "detail": {"source": "precomputed", "key": key_of(problem)}}
        if rung == 1:
            found, was_easier = self.easier_neighbour(problem)
            if not found:
                return self._method_hint(problem, 1, start, language=language,
                                         status="fallback",
                                         detail={"reason": "no neighbour to show"})
            score, neighbour = found
            text, detail = self._worked_example(neighbour, score, was_easier, language)
            detail["easier"] = was_easier
            return {"rung": 1, "kind": "easier_problem", "text": text, "status": "ok",
                    "seconds": round(time.perf_counter() - start, 2), "detail": detail}
        if rung == 2:
            return self._method_hint(problem, 2, start, language=language)
        if rung == 3:
            return self._model_hint(problem, start, language)
        return self.reveal(problem, start, language)

    def _method_hint(self, problem, rung, start, language="eng", status="ok", detail=None):
        found = topics.classify(problem.get("text", ""), language)
        try:
            neighbours = [p for _s, p in self.rank(problem)[:5]]
            shared = topics.shared_topics(neighbours)
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            # The embeddings are down: the keywords still work, so the method
            # is still useful. Never lose rung 2 to a dead server.
            shared = None
        if language == "cat":
            lines = [f"Sembla un problema de {topics.display(found['topic'], 'cat')}.",
                     found["method"]]
            if shared and shared != found["topic"]:
                lines.append(f"Els problemes més propers són sobretot de "
                             f"{topics.display(shared, 'cat')}.")
        else:
            lines = [f"This looks like a {topics.display(found['topic'], 'eng')} problem.",
                     found["method"]]
            if shared and shared != found["topic"]:
                lines.append(f"Its nearest neighbours are mostly "
                             f"{topics.display(shared, 'eng')} problems.")
        detail = dict(detail or {})
        detail.update({"topic": found["topic"], "matched": found["matched"],
                       "shared_topic": shared})
        return {"rung": rung, "kind": "method", "text": "\n".join(lines), "status": status,
                "seconds": round(time.perf_counter() - start, 2), "detail": detail}

    def _model_hint(self, problem, start, language="eng"):
        effective = max(1.0, self.llm_timeout)
        # Understand the problem in English, answer in the page's language.
        asked = self.twin.get(problem.get("id"), problem) if language == "cat" else problem
        try:
            raw, model_seconds = model_hint(asked, timeout=effective, language=language)
        except (OSError, urllib.error.URLError, KeyError, ValueError) as error:
            hint = self._method_hint(problem, 3, start, language=language, status="unavailable",
                                     detail={"error": str(error)})
            hint["kind"] = "method"
            return hint
        if language == "cat" and looks_english(raw):
            hint = self._method_hint(problem, 3, start, language=language, status="local",
                                     detail={"reason": "answer came back in English",
                                             "raw": raw,
                                             "model_seconds": round(model_seconds, 2)})
            return hint
        leaks, reason = leak_check(raw, problem)
        if leaks:
            hint = self._method_hint(problem, 3, start, language=language, status="rejected",
                                     detail={"reason": reason, "raw": raw,
                                             "model_seconds": round(model_seconds, 2)})
            hint["detail"]["leak"] = reason
            return hint
        grounded, why = grounding_check(raw, problem)
        if not grounded:
            hint = self._method_hint(problem, 3, start, language=language, status="ungrounded",
                                     detail={"reason": why, "raw": raw,
                                             "model_seconds": round(model_seconds, 2)})
            return hint
        return {"rung": 3, "kind": "model", "text": raw, "status": "ok",
                "seconds": round(time.perf_counter() - start, 2),
                "detail": {"raw": raw, "model_seconds": round(model_seconds, 2)}}

    def reveal(self, problem, start=None, language="eng"):
        start = start or time.perf_counter()
        options = problem.get("options") or {}
        label = problem.get("label")
        if language == "cat":
            text = (f"L'opció correcta és {label}) {options[label]}" if label in options
                    else f"L'opció correcta és {label}.")
        else:
            text = (f"The correct option is {label}) {options[label]}" if label in options
                    else f"The correct option is {label}.")
        return {"rung": 4, "kind": "solution", "text": text, "status": "ok",
                "seconds": round(time.perf_counter() - start, 2),
                "detail": {"label": label}}

    # -- checking and diagnosis ----------------------------------------------

    def check(self, problem, choice, language="eng"):
        label = problem.get("label")
        correct = choice == label
        options = problem.get("options") or {}
        result = {"correct": correct, "label": label,
                  "label_value": options.get(label),
                  "chosen_value": options.get(choice)}
        if not correct:
            result["diagnosis"] = diagnose(problem, choice, language)
        return result

    # -- adaptive practice ---------------------------------------------------

    def topic_of(self, problem):
        """The topic label of a problem, classified once and then remembered."""
        cache = getattr(self, "_topic_of", None)
        if cache is None:
            cache = self._topic_of = {}
        known = cache.get(problem.get("id"))
        if known is None:
            known = topics.classify(problem.get("text", ""))["topic"]
            cache[problem.get("id")] = known
        return known

    def topic_counts(self, language="eng"):
        """How many problems each topic has, for the page's picker."""
        counts = {}
        for _key, problem, _vector in self.rows:
            if problem.get("language") != language:
                continue
            name = self.topic_of(problem)
            counts[name] = counts.get(name, 0) + 1
        return [{"topic": name, "count": count}
                for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]

    def topic_pool(self, topic, language="eng"):
        """Every problem of one topic in one language, in a stable order.

        Catalan falls back to the English row of the same id when the topic has
        no Catalan problems, which is how the rest of the page handles a missing
        translation.
        """
        pool = [p for _k, p, _v in self.rows
                if p.get("language") == language and self.topic_of(p) == topic]
        if not pool:
            pool = [p for _k, p, _v in self.rows if self.topic_of(p) == topic]
        pool.sort(key=lambda p: (int(p.get("level", 0)), p.get("id") or ""))
        return pool

    def first_problem(self, level=1, language="eng", seed=None, topic=None):
        def wanted(pool):
            return [p for p in pool if self.topic_of(p) == topic] if topic else pool

        pool = wanted([p for _k, p, _v in self.rows
                       if p.get("level") == level and p.get("language") == language])
        if not pool:
            # Keep the page's language before the page's level: a Catalan
            # student would rather see another Catalan problem than an English
            # one at exactly the level asked for.
            pool = wanted([p for _k, p, _v in self.rows if p.get("language") == language])
        if not pool:
            pool = wanted([p for _k, p, _v in self.rows if p.get("level") == level])
        if not pool:
            pool = wanted([p for _k, p, _v in self.rows])
        if not pool:
            return None
        # The official run uses SEED; the tutor wants a different topic per
        # session, otherwise strict same-topic practice always starts geometry.
        (random.Random(seed) if seed is not None else random.Random()).shuffle(pool)
        return pool[0] if pool else None

    def next_problem(self, problem, correct, seen_ids=(), prefer_language=None, delta=None):
        """Move by ``delta`` levels: default up when right, down when wrong.

        The tutor passes a delta computed from the private score, so a right
        answer that needed hints can hold the level instead of moving up.

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
        step = (1 if correct else -1) if delta is None else int(delta)
        target = max(0, min(MAX_LEVEL, level + step))
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
        elif nxt.get("level") == target and step == 0:
            reason = "same topic, same level (the score says hold)"
        elif nxt.get("level") == target:
            reason = "same topic, one level up" if step > 0 else "same topic, one level down"
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
        self.ui_language = language
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
        self.attempts = []
        self.hints = []
        self.revealed = False
        self.revealed_label = None
        self.solution = None
        self.pending = None
        self.last = None

    def start(self, level=None, language=None, reset_profile=False, topic=None):
        self.level = int(self.level if level is None else level)
        self.language = language or self.language
        # The page's language, not the problem's: if the index has no Catalan
        # problems, the interface still stays Catalan and says so by the text.
        self.ui_language = language or self.ui_language
        # Practising one topic: the picker in the page. The pool is fixed for
        # the session, so every next problem stays in the topic chosen.
        self.topic = topic or None
        self.topic_pool = self.tutor.topic_pool(topic, self.ui_language) if topic else []
        # "Practise again" starts from zero: the counters the page shows under
        # "What you have practised" are cleared with the session. Opening the
        # server, or changing language, leaves the stored profile alone.
        if reset_profile and self.tutor.profile:
            self.tutor.profile.reset()
            self.profile_summary = self.tutor.profile.summary()
        self._clear()
        self.seen = []
        self.counts = {"tries": 0, "right": 0}
        # One practice session: the history feeds the private score, the
        # adaptive difficulty and the end-of-session report.
        self.history = []
        self.points = 0.0
        # A run of clean answers: counted per problem, reset by a wrong option or
        # by a reveal. The page draws it as a flame; the milestones encourage.
        self.streak = 0
        self.best_streak = 0
        self.streak_milestone = None
        self.streak_events = 0
        self.finished = False
        self.report = None
        self.problem_index = 0
        self.problem_started = None
        self.session_started = time.time()
        self.problem = self.tutor.first_problem(level=self.level, language=self.ui_language,
                                                topic=self.topic)
        self._begin_problem()
        return self.state()

    def _begin_problem(self):
        self.problem_index += 1
        self.problem_started = time.time()

    def current_key(self):
        return key_of(self.problem) if self.problem else None

    def state(self):
        problem = self.problem
        public = self.tutor.public(problem, self.ui_language) if problem else None
        diagnosis = None
        if self.result and not self.result["correct"] and not self.revealed:
            diagnosis = {"tag": self.result["diagnosis"]["tag"],
                         "text": self.result["diagnosis"]["text"]}
        return {
            "problem": public,
            "level": int(problem.get("level", 0)) if problem else None,
            "language": problem.get("language") if problem else None,
            "ui_language": self.ui_language,
            "topic": public.get("topic") if public else None,
            "answered": self.answered,
            "solved": bool(self.result and self.result["correct"]),
            "chosen": self.chosen,
            "attempts": list(self.attempts),
            "correct": bool(self.result["correct"]) if self.result else None,
            "diagnosis": diagnosis,
            "hints": [{"rung": hint["rung"], "kind": hint["kind"], "text": hint["text"],
                       "status": hint["status"], "seconds": hint["seconds"]} for hint in self.hints],
            "next_rung": (len(self.hints) + 1 if len(self.hints) < 3 else None)
            if not (self.result and self.result["correct"]) and not self.revealed
            and not self.finished else None,
            "revealed": self.revealed,
            "revealed_label": self.revealed_label,
            "solution": self.solution,
            "next_ready": (bool(self.result and self.result["correct"]) or self.revealed)
            and self.pending is not None and not self.finished,
            "progress": dict(self.counts),
            "topics": self.tutor.topic_counts(self.ui_language),
            "topic_filter": self.topic,
            "streak": self.streak,
            "best_streak": self.best_streak,
            "streak_milestone": self.streak_milestone,
            "streak_event": self.streak_events,
            "profile": self.profile_summary,
            "finished": self.finished,
            "hint_cooldown": float(CONFIG.get("HINT_COOLDOWN_SECONDS") or 0),
            "answer_cooldown": float(CONFIG.get("ANSWER_COOLDOWN_SECONDS") or 0),
            "last": self.last,
        }

    def _recent_points(self, count=4):
        """Mean private points over the last few attempts, including this one."""
        recent = [entry["points"] for entry in self.history[-count:]]
        return sum(recent) / len(recent) if recent else 0.0

    def _difficulty_delta(self, correct):
        """How many levels to move, from the private score, not only correctness.

        A clean right answer (+10) moves up; a right answer that needed hints or
        a wrong guess holds the level; a wrong answer, or a run of expensive
        answers, moves down.
        """
        if not correct:
            return -1
        recent = self._recent_points()
        if recent >= 8.0:
            return 1
        if recent >= 3.0:
            return 0
        return -1

    def _topic_next(self):
        """The next unseen problem of the chosen topic, at this level first."""
        if not self.topic_pool:
            return None
        unseen = [p for p in self.topic_pool if p.get("id") not in self.seen]
        if not unseen:
            unseen = list(self.topic_pool)     # a topic can run out; start again
        same = [p for p in unseen if int(p.get("level", 0)) == self.level]
        chosen = (same or unseen)[0]
        return {"problem": self.tutor.public(chosen, self.ui_language),
                "target_level": int(chosen.get("level", 0)),
                "topic": self.topic,
                "reason": "same topic, as you asked",
                "seconds": 0.0}

    def _update_streak(self, correct, first_try):
        """A clean answer extends the run; a wrong option ends it.

        A problem solved after a wrong guess does not extend it: the flame is
        about getting it right first time. Asking for the solution resets it in
        reveal(), and a new session starts the run again at zero.
        """
        self.streak_milestone = None
        if not correct:
            self.streak = 0
            return
        if not first_try:
            return
        self.streak += 1
        self.best_streak = max(self.best_streak, self.streak)
        if self.streak in STREAK_MILESTONES:
            self.streak_milestone = self.streak
            self.streak_events += 1

    def _pick_next(self, problem, correct):
        """The adaptive next problem, or a same-level one if embeddings are down."""
        if self.topic:
            chosen = self._topic_next()
            if chosen:
                return chosen
        try:
            return self.tutor.next_problem(problem, correct, seen_ids=self.seen,
                                           prefer_language=self.ui_language,
                                           delta=self._difficulty_delta(correct))
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            fallback = self.tutor.first_problem(level=int(problem.get("level", 0)),
                                                language=self.ui_language)
            if fallback is None or fallback.get("id") in self.seen:
                return None
            return {"problem": self.tutor.public(fallback),
                    "target_level": int(fallback.get("level", 0)),
                    "topic": topics.classify(fallback.get("text", ""))["topic"],
                    "reason": "no embeddings: another problem at the same level",
                    "seconds": 0.0}

    def answer(self, letter):
        letter = (letter or "").strip().upper()
        if self.finished:
            return {"error": "the session is over; press restart"}
        if self.problem is None:
            return {"error": "no problem is on screen"}
        if self.revealed:
            return {"error": "the solution is showing; press next"}
        if self.result and self.result["correct"]:
            return {"error": "this problem is already solved; press next"}
        if letter not in LETTERS:
            return {"error": "choose A to E"}
        if any(attempt["choice"] == letter for attempt in self.attempts):
            return {"error": "you already tried that option; choose another one"}
        started = time.perf_counter()
        result = self.tutor.check(self.problem, letter, self.ui_language)
        self.answered = True
        self.chosen = letter
        self.result = result
        first_try = not self.attempts
        self.attempts.append({"choice": letter, "correct": result["correct"]})
        self.counts["tries"] += 1
        self.counts["right"] += int(result["correct"])
        self._update_streak(result["correct"], first_try)
        # A private score, never shown: a right answer is worth 10, a hint costs
        # 1, and a failed attempt already cost 3 when it happened.
        points = max(0.0, 10.0 - 1.0 * len(self.hints)) if result["correct"] else -3.0
        seconds = time.perf_counter() - started
        response = time.time() - (self.problem_started or time.time())
        self.points += points
        self.history.append({
            "index": self.problem_index,
            "id": self.problem.get("id"),
            "topic": topics.classify(self.problem.get("text", ""))["topic"],
            "level": int(self.problem.get("level", 0)),
            "choice": letter,
            "correct": bool(result["correct"]),
            "hints": len(self.hints),
            "seconds": round(response, 1),
            "points": points,
            "tag": (result.get("diagnosis") or {}).get("tag"),
            "revealed": False,
        })
        if self.tutor.profile:
            self.tutor.profile.record(self.problem, letter, result["correct"],
                                      rung=len(self.hints), seconds=seconds, points=points)
            self.profile_summary = self.tutor.profile.summary()
        # Recompute on every attempt: the last one decides the next problem, so
        # a wrong guess followed by the right answer still moves a level up.
        self.pending = self._pick_next(self.problem, result["correct"])
        self.last = {"kind": "answer", "choice": letter, "correct": result["correct"]}
        return self.state()

    def hint(self, rung=None):
        if self.finished:
            return {"error": "the session is over; press restart"}
        if self.problem is None:
            return {"error": "no problem is on screen"}
        if self.revealed:
            return {"error": "the solution is showing; press next"}
        if self.result and self.result["correct"]:
            return {"error": "this problem is already solved; press next"}
        asked = len(self.hints) + 1
        rung = asked if rung is None else int(rung)
        if rung != asked:
            return {"error": f"ask for step {asked} first"}
        if asked == 4:
            return {"error": "the last step is the solution: press reveal"}
        try:
            hint = self.tutor.hint(self.problem, rung, self.ui_language)
        except (OSError, urllib.error.URLError) as error:
            return {"error": f"the model is not answering: {error}"}
        self.hints.append(hint)
        self.last = {"kind": "hint", "rung": hint["rung"], "status": hint["status"]}
        return self.state()

    def reveal(self):
        if self.finished:
            return {"error": "the session is over; press restart"}
        if self.problem is None:
            return {"error": "no problem is on screen"}
        if self.revealed:
            return {"error": "already revealed"}
        solution = self.tutor.reveal(self.problem, language=self.ui_language)
        self.solution = solution["text"]
        self.revealed = True
        self.revealed_label = self.problem.get("label")
        # The solution is not a clean answer, so the run stops here.
        self.streak = 0
        self.streak_milestone = None
        if not self.answered:
            # Asking for the solution is not a wrong answer: it is not counted
            # against the student in the profile, only in the session.
            self.counts["tries"] += 1
            self.history.append({
                "index": self.problem_index,
                "id": self.problem.get("id"),
                "topic": topics.classify(self.problem.get("text", ""))["topic"],
                "level": int(self.problem.get("level", 0)),
                "choice": None,
                "correct": False,
                "hints": len(self.hints),
                "seconds": round(time.time() - (self.problem_started or time.time()), 1),
                "points": 0.0,
                "tag": None,
                "revealed": True,
            })
        self.pending = self.pending or self._pick_next(self.problem, False)
        self.last = {"kind": "reveal"}
        return self.state()

    def finish(self):
        """Close the session and build the feedback and chart report."""
        if not self.finished:
            self.finished = True
            self.pending = None
            from .report import build
            self.report = build(self)
        return self.state()

    def next(self):
        if self.finished:
            return {"error": "the session is over; press restart"}
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
        self._begin_problem()
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
