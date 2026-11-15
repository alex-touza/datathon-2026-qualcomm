"""A retrieval index in SQLite: one solved M3Kang problem per row.

Two things make a small English embedding model behave on Catalan data:

* the same problem exists in several languages, so a Catalan problem is turned
  into its English twin before it is embedded, and a stored Catalan row is
  compared through the English twin's vector;
* embeddings alone are vague, so a share of the word overlap between the two
  problems is added to the cosine score (``RAG_KEYWORD_WEIGHT``) and examples
  below a floor can be dropped (``RAG_MIN_SCORE``). Both default to a value
  that can be measured with ``eval.py --rag 1``.
"""
import json
import re
import sqlite3
from array import array

from .config import CONFIG
from .embed import embed
from .llm import format_problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    key TEXT PRIMARY KEY,   -- id|language
    problem TEXT NOT NULL,  -- the full problem as JSON
    vec BLOB NOT NULL       -- float32 vector
)"""

# Words that carry no topic: they would make every pair look similar.
STOPWORDS = set("""
a an the and or but if then than that this these those of in on at to for from with
by as is are was were be been being it its it's we you your they their he she his her
do does did done have has had will would can could should may might must not no
how what which who whom when where why many much more most some any all each every
problem problems question questions option options answer answers choose correct
mark has have had gives give given gets get got take takes taken shows show shown
same total number numbers value values
--- catalan ---
el la els les un una uns unes de del dels de la que qui amb per pel pels com quan
on es és son està estan hi ho això aquest aquesta aquests aquestes seu seva seus
seves i o però si llavors més menys molt molts moltes cada tot tots totes quin
quina quins quines quant quants quanta quantes problema problemes pregunta
preguntes opció opcions resposta respostes tria correcte correcta dona dóna
tenen té tenia agafa agafat mostra mateix total nombre nombres valor valors
""".split())

WORD_RE = re.compile(r"[^\W\d_]{3,}")


def tokens(text):
    """Content words of a problem, lower case, stopwords removed."""
    return {word for word in WORD_RE.findall((text or "").lower())
            if word not in STOPWORDS}


def keyword_score(query_text, stored_text):
    """Jaccard overlap of content words, 0 to 1. Cheap and deterministic."""
    left, right = tokens(query_text), tokens(stored_text)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _number(name, default):
    try:
        return float(CONFIG.get(name) or default)
    except ValueError:
        return default


def build(db_path, problems, batch=16):
    db = sqlite3.connect(db_path)
    db.execute(SCHEMA)
    twin = {p["id"]: p for p in problems if p.get("language") == "eng"}
    for i in range(0, len(problems), batch):
        chunk = problems[i:i + batch]
        # Embed the English twin of a Catalan problem: bge-small is English.
        texts = [format_problem(twin.get(p["id"], p) if p.get("language") != "eng" else p)
                 for p in chunk]
        vectors = embed(texts)
        db.executemany("INSERT OR REPLACE INTO problems VALUES (?, ?, ?)", [
            (f"{p['id']}|{p['language']}", json.dumps(p, ensure_ascii=False), array("f", v).tobytes())
            for p, v in zip(chunk, vectors)])
        db.commit()
        yield min(i + batch, len(problems))
    db.close()


class Index:
    """Loads every vector into memory once, so each search is a plain loop."""

    def __init__(self, db_path):
        db = sqlite3.connect(db_path)
        self.rows = []
        for key, problem, blob in db.execute("SELECT key, problem, vec FROM problems"):
            vec = array("f")
            vec.frombytes(blob)
            self.rows.append((key, json.loads(problem), vec))
        db.close()
        # The English version of each problem, used for embedding and keywords
        # whenever the student is working in Catalan.
        self.twin = {p["id"]: p for _key, p, _vec in self.rows
                     if p.get("language") == "eng"}
        self.twin_vec = {p["id"]: vec for _key, p, vec in self.rows
                         if p.get("language") == "eng"}

    def english(self, problem):
        """The English twin of this problem, or the problem itself."""
        if problem.get("language") == "eng":
            return problem
        return self.twin.get(problem.get("id"), problem)

    def search(self, problem, k=1, exclude_id=None, min_score=None, keyword_weight=None):
        """Return up to k (score, problem) pairs, most similar first.

        exclude_id skips the same problem in other languages, so validation
        never retrieves its own translation. The score is the cosine similarity
        plus ``keyword_weight`` times the word overlap.
        """
        query = self.english(problem)
        query_text = format_problem(query)
        vector = embed([query_text], query=True)[0]
        weight = _number("RAG_KEYWORD_WEIGHT", 0.0) if keyword_weight is None else keyword_weight
        floor = _number("RAG_MIN_SCORE", 0.0) if min_score is None else min_score
        scored = []
        for _key, stored, own_vec in self.rows:
            if exclude_id and stored["id"] == exclude_id:
                continue
            english = self.english(stored)
            # Prefer the English twin's vector: a Catalan row built by an older
            # index still gets an English comparison.
            vec = self.twin_vec.get(stored["id"], own_vec)
            cosine = sum(a * b for a, b in zip(vector, vec))
            score = cosine + weight * keyword_score(query_text, format_problem(english))
            if floor and score < floor:
                continue
            scored.append((score, stored))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:k]
