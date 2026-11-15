"""End-of-session feedback and charts, standard library only.

Shiny for Python runs as a separate server with pip packages, and the board has
no pip and no network. The same five charts are drawn here as inline SVG, so the
page can show them without a dependency: a radar of topics, a bar chart of how
many hints each solved problem needed, a rolling learning curve, a scatter of
response time against correctness, and a heatmap of accuracy by position in the
session.
"""
import html
import math
import time

from . import topics

LETTERS = "ABCDE"

TEXT = {
    "eng": {
        "title": "Session feedback",
        "result": "1. Session result",
        "knowledge": "2. Knowledge achieved",
        "errors": "3. Error analysis",
        "recommendation": "4. Tutor recommendation",
        "problems": "{n} problems worked on: {solved} solved, {first} on the first try.",
        "hints": "{n} hints used in total.",
        "hints_one": "1 hint used in total.",
        "time": "{minutes} min of practice, {seconds} seconds per problem on average.",
        "none": "No problems finished yet. Try a few and come back.",
        "topic_line": "{topic}: {solved}/{total} solved ({percent}%).",
        "topic_mastered": "Going well: {topics}.",
        "topic_weak": "Needs another look: {topics}.",
        "no_errors": "No wrong answers: keep this level and try a slightly harder problem next time.",
        "error_line": "{name}: {n} times.",
        "errors_by_topic": "Most mistakes were in: {topics}.",
        "unknown": "not classified",
        "unknown_topic": "general",
        "rec_weak": "Practise {topic} at a lower level, with one hint available and no timer.",
        "rec_hints": "You solve the problems, but you lean on hints. Try the next one without hints before asking.",
        "rec_errors": "Re-read what the last sentence asks before choosing; most errors came from stopping one step early.",
        "rec_good": "You are ready for harder problems: the next one goes up a level.",
        "rec_start": "Finish a few problems so the tutor can recommend a next step.",
        "chart_radar": "Accuracy by topic",
        "chart_hints": "Hints needed per solved problem",
        "chart_learning": "Learning curve (rolling accuracy, window {window})",
        "chart_scatter": "Response time vs correctness",
        "chart_box": "Response time by outcome (box plot)",
        "chart_heatmap": "Accuracy by position in the session",
        "box_first": "first try",
        "box_retry": "after a retry",
        "box_unsolved": "not solved",
        "box_n": "n={n}",
        "box_none": "–",
        "hints_label": "{n} hints",
        "correct": "correct",
        "wrong": "wrong",
        "seconds": "seconds",
        "position": "problems",
        "accuracy": "accuracy",
        "not_enough": "Not enough data for this chart yet.",
    },
    "cat": {
        "title": "Resum de la sessió",
        "result": "1. Resultat de la sessió",
        "knowledge": "2. Coneixements assolits",
        "errors": "3. Anàlisi dels errors",
        "recommendation": "4. Recomanació del tutor",
        "problems": "{n} problemes treballats: {solved} resolts, {first} al primer intent.",
        "hints": "{n} pistes consultades en total.",
        "hints_one": "1 pista consultada en total.",
        "time": "{minutes} min de pràctica, {seconds} s per problema de mitjana.",
        "none": "Encara no has acabat cap problema. Prova'n uns quants i torna.",
        "topic_line": "{topic}: {solved}/{total} resolts ({percent}%).",
        "topic_mastered": "Van bé: {topics}.",
        "topic_weak": "Cal repassar: {topics}.",
        "no_errors": "Cap resposta incorrecta: mantén aquest nivell i prova un problema una mica més difícil.",
        "error_line": "{name}: {n} vegades.",
        "errors_by_topic": "La majoria d'errors han estat a: {topics}.",
        "unknown": "sense classificar",
        "unknown_topic": "general",
        "rec_weak": "Practica {topic} a un nivell més baix, amb una pista disponible i sense pressa.",
        "rec_hints": "Resols els problemes, però t'ajudes massa de les pistes. Prova el següent sense demanar-ne cap.",
        "rec_errors": "Torna a llegir què demana l'última frase abans de triar; la majoria d'errors venen d'aturar-se un pas abans.",
        "rec_good": "Estàs a punt per a problemes més difícils: el següent puja un nivell.",
        "rec_start": "Acaba uns quants problemes perquè el tutor et pugui recomanar el següent pas.",
        "chart_radar": "Encerts per tema",
        "chart_hints": "Pistes necessàries per problema resolt",
        "chart_learning": "Corba d'aprenentatge (mitjana mòbil, finestra {window})",
        "chart_scatter": "Temps de resposta vs encert",
        "chart_box": "Temps de resposta segons el resultat (diagrama de caixa)",
        "chart_heatmap": "Encerts segons la posició a la sessió",
        "box_first": "primer intent",
        "box_retry": "després de reintentar",
        "box_unsolved": "no resolt",
        "box_n": "n={n}",
        "box_none": "–",
        "hints_label": "{n} pistes",
        "correct": "encert",
        "wrong": "error",
        "seconds": "segons",
        "position": "problemes",
        "accuracy": "encerts",
        "not_enough": "Encara no hi ha prou dades per a aquest gràfic.",
    },
}

TAG_NAMES = {
    "eng": {
        "unit": "different unit", "sign": "wrong direction", "digit_swap": "digits swapped",
        "off_by_one": "off by one", "power_of_ten": "factor of ten", "double": "double",
        "half": "half", "from_text": "a number copied from the text",
        "one_step": "stopped one step early", None: "not classified",
    },
    "cat": {
        "unit": "unitat diferent", "sign": "direcció equivocada", "digit_swap": "xifres canviades",
        "off_by_one": "una unitat de més o menys", "power_of_ten": "factor de deu", "double": "el doble",
        "half": "la meitat", "from_text": "un nombre copiat del text",
        "one_step": "s'ha aturat un pas abans", None: "sense classificar",
    },
}


def _t(language, key, **values):
    text = TEXT.get(language, TEXT["eng"]).get(key, key)
    return text.format(**values) if values else text


def _esc(text):
    return html.escape(str(text))


def _svg(width, height, body, title):
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(title)}" '
            f'xmlns="http://www.w3.org/2000/svg" '
            f'style="width:100%;height:auto;font-family:system-ui,sans-serif">'
            f'{body}</svg>')


def _text(x, y, content, size=11, anchor="middle", color="var(--muted)", weight="500"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'fill="{color}" font-weight="{weight}">{_esc(content)}</text>')


def _axis(width, height, pad=28):
    return (f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" '
            f'stroke="var(--line)"/>'
            f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="var(--line)"/>')


def _problems(session):
    """One record per problem shown, from the attempt log."""
    found = {}
    for entry in session.history:
        item = found.setdefault(entry["index"], {
            "index": entry["index"], "topic": entry["topic"], "level": entry["level"],
            "solved": False, "first_try": False, "hints": 0, "attempts": 0,
            "seconds": entry["seconds"], "revealed": False, "points": 0.0,
        })
        item["attempts"] += 1
        item["hints"] = max(item["hints"], entry["hints"])
        item["points"] += entry["points"]
        item["revealed"] = item["revealed"] or entry["revealed"]
        if entry["correct"]:
            item["solved"] = True
            if item["attempts"] == 1:
                item["first_try"] = True
    return [found[key] for key in sorted(found)]


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------

def radar_chart(problems, language):
    title = _t(language, "chart_radar")
    if len(problems) < 3:
        return _empty(title, language)
    by_topic = {}
    for item in problems:
        stats = by_topic.setdefault(item["topic"], {"total": 0, "solved": 0})
        stats["total"] += 1
        stats["solved"] += int(item["solved"])
    rows = sorted(by_topic.items(), key=lambda pair: (-pair[1]["total"], pair[0]))[:7]
    if len(rows) < 3:
        return _empty(title, language)
    width, height = 360, 300
    cx, cy, radius = width / 2, height / 2 - 4, 100
    body = []
    for ring in (0.25, 0.5, 0.75, 1.0):
        points = []
        for i in range(len(rows)):
            angle = -math.pi / 2 + 2 * math.pi * i / len(rows)
            points.append(f"{cx + radius * ring * math.cos(angle):.1f},"
                          f"{cy + radius * ring * math.sin(angle):.1f}")
        body.append(f'<polygon points="{" ".join(points)}" fill="none" '
                    f'stroke="var(--line)" stroke-width="1"/>')
    values = []
    for i, (topic, stats) in enumerate(rows):
        angle = -math.pi / 2 + 2 * math.pi * i / len(rows)
        x, y = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        body.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" '
                    f'stroke="var(--line)" stroke-width="1"/>')
        label_x, label_y = cx + (radius + 18) * math.cos(angle), cy + (radius + 18) * math.sin(angle)
        name = topics.display(topic, language)
        name = name if len(name) <= 14 else name[:13] + "…"
        anchor = "middle" if abs(math.cos(angle)) < 0.35 else ("start" if math.cos(angle) > 0 else "end")
        body.append(_text(label_x, label_y, name, 9, anchor))
        accuracy = stats["solved"] / stats["total"] if stats["total"] else 0
        values.append((cx + radius * accuracy * math.cos(angle),
                       cy + radius * accuracy * math.sin(angle)))
    body.append(f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in values)}" '
                f'fill="var(--accent)" fill-opacity="0.28" stroke="var(--accent)" stroke-width="2"/>')
    return _svg(width, height, "".join(body), title)


def hints_chart(problems, language):
    title = _t(language, "chart_hints")
    solved = [item for item in problems if item["solved"]]
    if not solved:
        return _empty(title, language)
    counts = [0, 0, 0, 0]
    for item in solved:
        counts[min(3, item["hints"])] += 1
    width, height, pad = 360, 190, 30
    body = [_axis(width, height, pad)]
    top = max(counts) or 1
    slot = (width - 2 * pad) / 4
    for i, count in enumerate(counts):
        bar_width = slot * 0.55
        x = pad + slot * i + (slot - bar_width) / 2
        bar_height = (height - 2 * pad) * count / top
        y = height - pad - bar_height
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
                    f'height="{bar_height:.1f}" rx="4" fill="var(--accent)" fill-opacity="0.75"/>')
        if count:
            body.append(_text(x + bar_width / 2, y - 4, str(count), 10))
        body.append(_text(x + bar_width / 2, height - pad + 14,
                          _t(language, "hints_label", n=i), 9))
    return _svg(width, height, "".join(body), title)


def learning_chart(problems, language, window=5):
    title = _t(language, "chart_learning", window=window)
    if len(problems) < 2:
        return _empty(title, language)
    width, height, pad = 360, 210, 32
    body = [_axis(width, height, pad)]
    for value in (0, 50, 100):
        y = height - pad - (height - 2 * pad) * value / 100
        body.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{width - pad}" y2="{y:.1f}" '
                    f'stroke="var(--line)" stroke-dasharray="3 4"/>')
        body.append(_text(pad - 6, y + 3, str(value), 8, "end"))
    n = len(problems)
    step = (width - 2 * pad) / max(1, n - 1)
    for i, item in enumerate(problems):
        x = pad + step * i
        y = height - pad if item["solved"] else pad
        color = "var(--good)" if item["solved"] else "var(--bad)"
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}" fill-opacity="0.35"/>')
    rolling = []
    for i in range(n):
        chunk = problems[max(0, i - window + 1):i + 1]
        rolling.append(sum(1 for item in chunk if item["solved"]) / len(chunk) * 100)
    points = []
    for i, value in enumerate(rolling):
        x = pad + step * i
        y = height - pad - (height - 2 * pad) * value / 100
        points.append(f"{x:.1f},{y:.1f}")
    body.append(f'<polyline points="{" ".join(points)}" fill="none" '
                f'stroke="var(--accent)" stroke-width="2.2" stroke-linejoin="round"/>')
    body.append(_text(width / 2, height - 6, _t(language, "position"), 9))
    return _svg(width, height, "".join(body), title)


def scatter_chart(session, language):
    title = _t(language, "chart_scatter")
    entries = [entry for entry in session.history if entry.get("seconds") is not None]
    if len(entries) < 2:
        return _empty(title, language)
    width, height, pad = 360, 210, 36
    body = [_axis(width, height, pad)]
    max_seconds = max(entry["seconds"] for entry in entries) or 1
    for value in (0, max_seconds / 2, max_seconds):
        x = pad + (width - 2 * pad) * value / max_seconds
        body.append(_text(x, height - pad + 14, f"{value:.0f}", 8))
    body.append(_text(width / 2, height - 4, _t(language, "seconds"), 9))
    for value, label in ((0, _t(language, "wrong")), (1, _t(language, "correct"))):
        y = height - pad - (height - 2 * pad) * value
        body.append(_text(pad - 6, y + 3, label, 8, "end"))
    for entry in entries:
        x = pad + (width - 2 * pad) * entry["seconds"] / max_seconds
        # A little vertical jitter so repeated 0s and 1s do not overlap.
        jitter = ((entry["index"] * 37 + len(entry.get("choice") or "")) % 7) - 3
        y = height - pad - (height - 2 * pad) * (1 if entry["correct"] else 0) + jitter
        color = "var(--good)" if entry["correct"] else "var(--bad)"
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}" fill-opacity="0.55"/>')
    return _svg(width, height, "".join(body), title)


def _quantile(values, q):
    """Linear-interpolation quantile of an already sorted list."""
    if not values:
        return 0.0
    position = (len(values) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (position - low)


def box_chart(problems, language):
    """Time per problem grouped by outcome: does hesitation track failure?

    Each box is one group (first try, after a retry, not solved). The box is the
    interquartile range, the line is the median, the whiskers reach 1.5 IQR, and
    the dots are the outliers, so the chart answers a real question: whether the
    student spends more time on the problems they get wrong.
    """
    title = _t(language, "chart_box")
    groups = [
        (_t(language, "box_first"),
         [item["seconds"] for item in problems if item["solved"] and item["first_try"]]),
        (_t(language, "box_retry"),
         [item["seconds"] for item in problems if item["solved"] and not item["first_try"]]),
        (_t(language, "box_unsolved"),
         [item["seconds"] for item in problems if not item["solved"]]),
    ]
    if not any(values for _label, values in groups):
        return _empty(title, language)
    width, height, pad = 380, 230, 38
    body = [_axis(width, height, pad)]
    everyone = [value for _label, values in groups for value in values]
    top = max(everyone) or 1.0
    for value in (0, top / 2, top):
        y = height - pad - (height - 2 * pad) * value / top
        body.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{width - pad}" y2="{y:.1f}" '
                    f'stroke="var(--line)" stroke-dasharray="3 4"/>')
        body.append(_text(pad - 6, y + 3, f"{value:.0f}s", 8, "end"))
    slot = (width - 2 * pad) / len(groups)
    for i, (label, values) in enumerate(groups):
        centre = pad + slot * i + slot / 2
        values = sorted(values)
        if not values:
            body.append(_text(centre, height - pad + 16, label, 8))
            body.append(_text(centre, height - pad + 28, _t(language, "box_none"), 8))
            continue
        q1, median, q3 = (_quantile(values, q) for q in (0.25, 0.5, 0.75))
        spread = q3 - q1
        low = min(value for value in values if value >= q1 - 1.5 * spread)
        high = max(value for value in values if value <= q3 + 1.5 * spread)

        def y_of(value):
            return height - pad - (height - 2 * pad) * value / top

        box_width = min(34, slot * 0.55)
        left = centre - box_width / 2
        body.append(f'<line x1="{centre:.1f}" y1="{y_of(high):.1f}" '
                    f'x2="{centre:.1f}" y2="{y_of(low):.1f}" stroke="var(--accent)"/>')
        body.append(f'<line x1="{centre - 9:.1f}" y1="{y_of(high):.1f}" '
                    f'x2="{centre + 9:.1f}" y2="{y_of(high):.1f}" stroke="var(--accent)"/>')
        body.append(f'<line x1="{centre - 9:.1f}" y1="{y_of(low):.1f}" '
                    f'x2="{centre + 9:.1f}" y2="{y_of(low):.1f}" stroke="var(--accent)"/>')
        body.append(f'<rect x="{left:.1f}" y="{y_of(q3):.1f}" width="{box_width:.1f}" '
                    f'height="{max(1.0, y_of(q1) - y_of(q3)):.1f}" rx="3" '
                    f'fill="var(--accent)" fill-opacity="0.30" stroke="var(--accent)"/>')
        body.append(f'<line x1="{left:.1f}" y1="{y_of(median):.1f}" '
                    f'x2="{left + box_width:.1f}" y2="{y_of(median):.1f}" '
                    f'stroke="var(--accent)" stroke-width="2"/>')
        for value in values:
            if value < low or value > high:
                body.append(f'<circle cx="{centre + ((value * 7) % 5 - 2):.1f}" '
                            f'cy="{y_of(value):.1f}" r="2.4" fill="var(--accent)" '
                            f'fill-opacity="0.7"/>')
        body.append(_text(centre, height - pad + 16, label, 8))
        body.append(_text(centre, height - pad + 28, _t(language, "box_n", n=len(values)), 8))
    body.append(_text(width / 2, 16, _t(language, "seconds"), 9))
    return _svg(width, height, "".join(body), title)


def heatmap_chart(problems, language, bin_size=5):
    title = _t(language, "chart_heatmap")
    if not problems:
        return _empty(title, language)
    width, height = 360, 150
    bins = []
    for start in range(0, len(problems), bin_size):
        chunk = problems[start:start + bin_size]
        solved = sum(1 for item in chunk if item["solved"])
        bins.append((start + 1, start + len(chunk), solved / len(chunk)))
    slot = (width - 20) / len(bins)
    body = []
    for i, (first, last, accuracy) in enumerate(bins):
        x = 10 + slot * i
        hue = 140 if accuracy >= 0.5 else 8
        alpha = 0.18 + 0.55 * accuracy
        body.append(f'<rect x="{x:.1f}" y="18" width="{slot - 3:.1f}" height="64" rx="6" '
                    f'fill="hsl({hue} 65% 45%)" fill-opacity="{alpha:.2f}"/>')
        body.append(_text(x + (slot - 3) / 2, 52, f"{accuracy * 100:.0f}%", 10,
                          color="var(--ink)", weight="700"))
        body.append(_text(x + (slot - 3) / 2, 98, f"{first}–{last}", 8))
    body.append(_text(width / 2, 124, _t(language, "position"), 9))
    return _svg(width, height, "".join(body), title)


def _empty(title, language):
    return _svg(360, 120,
                _text(180, 64, _t(language, "not_enough"), 11),
                title)


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------

def feedback(session, problems):
    language = session.ui_language
    t = lambda key, **values: _t(language, key, **values)
    tags = TAG_NAMES.get(language, TAG_NAMES["eng"])
    result, knowledge, errors, recommendation = [], [], [], []
    if not problems:
        return [{"key": "result", "title": t("result"), "lines": [t("none")]}]

    solved = sum(1 for item in problems if item["solved"])
    first = sum(1 for item in problems if item["first_try"])
    hints = sum(item["hints"] for item in problems if item["solved"])
    elapsed = max(0.0, time.time() - session.session_started)
    result.append(t("problems", n=len(problems), solved=solved, first=first))
    result.append(t("hints_one") if hints == 1 else t("hints", n=hints))
    result.append(t("time", minutes=max(1, round(elapsed / 60)),
                    seconds=round(sum(item["seconds"] for item in problems) / len(problems), 1)))

    by_topic = {}
    for item in problems:
        stats = by_topic.setdefault(item["topic"], {"total": 0, "solved": 0})
        stats["total"] += 1
        stats["solved"] += int(item["solved"])
    rows = sorted(by_topic.items(), key=lambda pair: (-pair[1]["total"], pair[0]))
    mastered, weak = [], []
    for topic, stats in rows:
        percent = round(100 * stats["solved"] / stats["total"]) if stats["total"] else 0
        name = topics.display(topic, language)
        knowledge.append(t("topic_line", topic=name, solved=stats["solved"],
                           total=stats["total"], percent=percent))
        if stats["solved"] >= 1 and percent >= 70:
            mastered.append(name)
        elif stats["total"] >= 1 and percent < 60:
            weak.append((name, percent))
    if mastered:
        knowledge.append(t("topic_mastered", topics=", ".join(mastered)))
    if weak:
        knowledge.append(t("topic_weak", topics=", ".join(name for name, _ in weak)))

    wrong = [entry for entry in session.history if not entry["correct"] and not entry["revealed"]]
    if not wrong:
        errors.append(t("no_errors"))
    else:
        by_tag = {}
        for entry in wrong:
            by_tag[entry.get("tag")] = by_tag.get(entry.get("tag"), 0) + 1
        for tag, count in sorted(by_tag.items(), key=lambda pair: (-pair[1], str(pair[0])))[:4]:
            errors.append(t("error_line", name=tags.get(tag, tags[None]), n=count))
        wrong_by_topic = {}
        for entry in wrong:
            name = topics.display(entry["topic"], language)
            wrong_by_topic[name] = wrong_by_topic.get(name, 0) + 1
        top = sorted(wrong_by_topic.items(), key=lambda pair: -pair[1])[:2]
        errors.append(t("errors_by_topic", topics=", ".join(name for name, _ in top)))

    if weak:
        recommendation.append(t("rec_weak", topic=weak[0][0]))
    elif solved and hints / max(1, solved) >= 1.5:
        recommendation.append(t("rec_hints"))
    elif wrong and len(wrong) >= len(problems):
        recommendation.append(t("rec_errors"))
    elif problems:
        recommendation.append(t("rec_good"))
    else:
        recommendation.append(t("rec_start"))

    return [
        {"key": "result", "title": t("result"), "lines": result},
        {"key": "knowledge", "title": t("knowledge"), "lines": knowledge},
        {"key": "errors", "title": t("errors"), "lines": errors},
        {"key": "recommendation", "title": t("recommendation"), "lines": recommendation},
    ]


def build(session):
    """Return the whole end-of-session payload the page draws."""
    language = session.ui_language
    problems = _problems(session)
    charts = [
        {"key": "radar", "title": _t(language, "chart_radar"),
         "svg": radar_chart(problems, language)},
        {"key": "hints", "title": _t(language, "chart_hints"),
         "svg": hints_chart(problems, language)},
        {"key": "learning", "title": _t(language, "chart_learning", window=5),
         "svg": learning_chart(problems, language)},
        {"key": "scatter", "title": _t(language, "chart_scatter"),
         "svg": scatter_chart(session, language)},
        {"key": "box", "title": _t(language, "chart_box"),
         "svg": box_chart(problems, language)},
        {"key": "heatmap", "title": _t(language, "chart_heatmap"),
         "svg": heatmap_chart(problems, language)},
    ]
    return {"title": _t(language, "title"),
            "feedback": feedback(session, problems),
            "charts": charts}
