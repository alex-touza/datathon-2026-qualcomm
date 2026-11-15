"""A small keyword taxonomy, used by the tutor for rung 2 and for practice.

M3Kang has no topic field, so the topic is derived from words in the problem,
in English and in Catalan. This is deliberately a short, readable table: it
runs in microseconds, needs no model, and is easy to defend in the write-up.
When the keywords miss, the caller falls back to what the nearest solved
problems have in common, and then to "general".

Every method line is one sentence of advice about *starting*, never the answer.
"""
import functools
import re

# topic -> (keywords, method)
# Keywords are matched as substrings of the normalised text (lower case, accents
# kept), so "perimeter" also matches "perimeters".
TOPICS = {
    "counting": (
        ["how many ways", "in how many", "number of ways", "arrangement", "combination",
         "permutation", "order matters", "quantes maneres", "de quantes maneres",
         "quants nombres", "combinaci", "permutaci"],
        "Counting: write the choices in a small table and count the cases before "
        "you decide whether to add or multiply."),
    "probability": (
        ["probability", "chance", "at random", "outcome", "probabilitat", "a l'atzar"],
        "Probability: list every equally likely outcome, then count the ones you want."),
    "geometry": (
        ["area", "perimeter", "triangle", "triangular", "square", "rectangle", "circle", "angle",
         "diagonal", "cube", "volume", "radius", "diameter", "àrea", "perímetre",
         "triangle", "triangular", "quadrat", "rectangle", "cercle", "angle", "cub", "volum", "radi"],
        "Geometry: draw the figure and write the one formula you need next to the "
        "quantities you already know."),
    "fractions": (
        ["fraction", "numerator", "denominator", "half", "third", "quarter",
         "fracci", "numerador", "denominador", "meitat", "terç", "quart"],
        "Fractions: put both quantities over the same denominator before you compare "
        "or add them."),
    "number_theory": (
        ["divisible", "divides", "prime", "remainder", "digit", "multiple", "factor",
         "divisible", "primer", "residu", "xifra", "múltiple"],
        "Number theory: try the smallest cases first and look for what repeats."),
    "measurement": (
        ["cm", "mm", "km", "kg", "gram", "litre", "liter", "metre", "centimetre",
         "kilometre", "minute", "hour", "second", "cm", "kg", "litre", "metre",
         "gram", "hora", "minut", "segon"],
        "Measurement: convert every quantity to the same unit before you calculate."),
    "money": (
        ["euro", "cent", "cost", "price", "discount", "pay", "€", "preu", "diners",
         "descompte", "paga"],
        "Money: work out the total first, then compare it with the amount you have."),
    "time": (
        ["clock", "days", "weeks", "months", "years", "calendar", "rellotge", "dies",
         "setmanes", "mesos", "anys"],
        "Time: count the whole units first, then the leftover ones."),
    "statistics": (
        ["average", "mean", "median", "mitjana"],
        "Statistics: add all the values, then divide by how many there are."),
    "rate": (
        ["per hour", "per minute", "per day", "per second", "rate", "speed", "ratio",
         "proportion", "per hora", "per minut", "per dia", "velocitat", "proporci"],
        "Rate: find the amount for one unit first, then multiply by how many you need."),
    "algebra": (
        ["solve for", "value of x", "equation", "unknown", "express", "equaci",
         "incògnita", "valor de x"],
        "Algebra: name the unknown, write what the sentence says as an equation, "
        "then undo the operations one at a time."),
    "patterns": (
        ["sequence", "next term", "pattern", "series", "successi", "següent", "patró"],
        "Patterns: write the differences between consecutive terms and look at those."),
    "logic": (
        ["always", "never", "must be", "cannot be", "which statement", "true",
         "sempre", "mai", "cert", "fals"],
        "Logic: test the two extreme cases first; a single counterexample is enough "
        "to rule a statement out."),
    "arithmetic": (
        ["sum", "plus", "minus", "product", "twice", "double", "suma", "resta",
         "producte", "doble", "més que", "menys que"],
        "Arithmetic: underline the question, then do one operation per line and keep "
        "the units next to the numbers."),
}

GENERAL_METHOD = ("Read the last sentence twice, underline what is being asked, and write "
                  "down the quantities you are given before choosing an operation.")

# The same one-sentence advice in Catalan. The taxonomy is keyword based in both
# languages, so a Catalan problem classifies without calling a model, and rung 2
# never falls back to English when the page is in Catalan.
GENERAL_METHOD_CA = ("Llegeix dues vegades l'última frase, subratlla què et demanen i escriu "
                     "les dades que et donen abans de triar una operació.")

METHOD_CA = {
    "counting": "Recompte: fes una taula petita amb les opcions i compta els casos abans "
                "de decidir si sumes o multipliques.",
    "probability": "Probabilitat: escriu tots els resultats igual de probables i compta "
                   "els que vols.",
    "geometry": "Geometria: dibuixa la figura i escriu al costat la fórmula que necessites "
                "i les dades que ja tens.",
    "fractions": "Fraccions: posa les dues quantitats amb el mateix denominador abans de "
                 "comparar-les o sumar-les.",
    "number_theory": "Teoria de nombres: prova primer els casos més petits i mira què es repeteix.",
    "measurement": "Mesures: passa totes les quantitats a la mateixa unitat abans de calcular.",
    "money": "Diners: calcula primer el total i després compara'l amb els diners que tens.",
    "time": "Temps: compta primer les unitats senceres i després les que sobren.",
    "statistics": "Estadística: suma tots els valors i divideix pel nombre de valors.",
    "rate": "Proporcions: troba primer la quantitat per a una unitat i després multiplica "
            "pel nombre que necessites.",
    "algebra": "Àlgebra: posa nom a la incògnita, escriu la frase com una equació i desfés "
               "les operacions d'una en una.",
    "patterns": "Patrons: escriu les diferències entre termes seguits i mira aquestes diferències.",
    "logic": "Lògica: prova primer els dos casos extrems; un sol contraexemple ja descarta "
             "una afirmació.",
    "arithmetic": "Aritmètica: subratlla la pregunta, fes una operació per línia i porta "
                  "les unitats al costat dels nombres.",
    "general": GENERAL_METHOD_CA,
}

# What the page writes in the topic chip and in the profile.
NAMES = {
    "eng": {
        "counting": "counting", "probability": "probability", "geometry": "geometry",
        "fractions": "fractions", "number_theory": "number theory",
        "measurement": "measurement", "money": "money", "time": "time",
        "statistics": "statistics", "rate": "rate", "algebra": "algebra",
        "patterns": "patterns", "logic": "logic", "arithmetic": "arithmetic",
        "general": "general",
    },
    "cat": {
        "counting": "recompte", "probability": "probabilitat", "geometry": "geometria",
        "fractions": "fraccions", "number_theory": "teoria de nombres",
        "measurement": "mesures", "money": "diners", "time": "temps",
        "statistics": "estadística", "rate": "proporcions", "algebra": "àlgebra",
        "patterns": "patrons", "logic": "lògica", "arithmetic": "aritmètica",
        "general": "general",
    },
}


def display(topic, language="eng"):
    """The topic's name in the page's language."""
    return NAMES.get(language, NAMES["eng"]).get(topic, topic)



def normalize(text):
    return (text or "").lower().replace("’", "'").replace("–", "-").replace("—", "-")


@functools.lru_cache(maxsize=4096)
def _pattern(keyword):
    """One keyword to a regular expression: whole words, plurals allowed.

    Word boundaries matter: without them "dies" (Catalan for days) matches
    "candies", and "cent" matches "percent". Cached: next_problem classifies
    every candidate, many times per sort.
    """
    escaped = re.escape(keyword)
    if " " in keyword:
        return re.compile(escaped)
    return re.compile(rf"\b{escaped}")


@functools.lru_cache(maxsize=2048)
def _cached(lowered):
    best = None
    for topic, (keywords, method) in TOPICS.items():
        hits, first = [], None
        for word in keywords:
            if not word:
                continue
            found = _pattern(word).search(lowered)
            if found:
                hits.append(word)
                first = found.start() if first is None else min(first, found.start())
        if not hits:
            continue
        # A phrase is worth more than a single word: "in how many ways" is a
        # stronger sign of counting than the word "sum" is of arithmetic.
        strength = sum(2 if " " in word else 1 for word in hits)
        rank = (-strength, first, topic)
        if best is None or rank < best[0]:
            best = (rank, topic, method, hits)
    if best is None:
        return ("general", GENERAL_METHOD, ())
    _rank, topic, method, hits = best
    return (topic, method, tuple(sorted(set(hits))))


def classify(text, language="eng"):
    """Return {"topic", "method", "matched"} for one problem text.

    Ties go to the topic whose keywords appear earliest in the text, which is
    usually the noun that names the situation. ``language`` only chooses the
    language of ``method``; classification itself is keyword based in both.
    """
    topic, method, matched = _cached(normalize(text))
    if language == "cat":
        method = METHOD_CA.get(topic, GENERAL_METHOD_CA)
    return {"topic": topic, "method": method, "matched": list(matched)}


def shared_topics(problems, limit=5):
    """The topic that most of these solved problems have in common, if any."""
    counts = {}
    for problem in problems[:limit]:
        topic = classify(problem.get("text", ""))["topic"]
        if topic != "general":
            counts[topic] = counts.get(topic, 0) + 1
    if not counts:
        return None
    topic, count = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
    return topic if count >= 2 or len(problems) <= 2 else None
