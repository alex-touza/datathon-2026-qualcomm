"""Verification rules for the precomputed three-part tutor solutions.

The model generates a nudge, the working, and the conclusion. Nothing is
accepted until it passes these checks. The tutor only reads verified rows.

The checks are deliberately conservative: rejecting a good solution costs one
retry, but *accepting* a wrong one teaches a student wrong mathematics. Where a
check cannot read a value with confidence (OCR damage, variables, symbols) it
skips rather than rejects.
"""
import ast
import math
import re

LETTERS = "ABCDE"
MAX_NUDGE_WORDS = 20
_ARITHMETIC = re.compile(r"\d\s+[+\-×÷]\s+\d|\d\s*[+\-×÷]\s*\d\s*=")

# Characters that may appear in a side of a numeric equation we are willing to
# evaluate. Anything else (a variable, a word, a unit) makes us skip the check.
_EXPR_CHARS = set("0123456789+-*/×÷().,√^ \t")
_OCR_SQRT = re.compile(r"\b[vV](?=\s*\d)")
_SPLIT_FRACTION = re.compile(r"(?<![\d/])(\d+)\s+(\d+)(?![\d/])")


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left ** right
    raise ValueError("unsupported arithmetic")


def safe_eval(expression):
    """Evaluate + - * / ** and parentheses without eval()."""
    expression = expression.replace("×", "*").replace("÷", "/").replace(",", ".")
    return _eval(ast.parse(expression, mode="eval"))


def normalize_option(text):
    """Canonical form of an option value, tolerant of the common OCR damage.

    `49 10` -> `49/10`, `V5-V2` -> `√5-√2`, units and spacing dropped.
    """
    text = (text or "").replace("²", "^2").replace("³", "^3").strip().lower()
    text = _OCR_SQRT.sub("√", text)
    text = _SPLIT_FRACTION.sub(r"\1/\2", text)
    text = re.sub(r"\s+", "", text)
    # Drop a trailing unit, including its exponent digit: `81cm2` -> `81`.
    text = re.sub(r"[a-z%°]+\d*$", "", text)
    return text


def _eval_side(text):
    """Evaluate a numeric side of an equation, or None if it is not numeric."""
    if not text or not any(char.isdigit() for char in text):
        return None
    if any(char not in _EXPR_CHARS for char in text):
        return None
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)

    def root(match):
        value = float(match.group(1).replace(",", "."))
        return f"({math.sqrt(value)!r})"

    text = re.sub(r"√\s*(\d+(?:[.,]\d+)?)", root, text)
    if not re.fullmatch(r"[\d+\-*/().\s]+", text):
        return None
    try:
        return safe_eval(text)
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
        return None


def equation_error(part2, part3):
    """Return the first equation that does not balance, or None.

    Only equations whose two sides are *fully* numeric are checked; the whole
    contiguous run around `=` has to be arithmetic. That keeps `3s^2/8 = 5s^2/8`
    or `2500/15 = 166 with remainder` out of the check instead of misreading
    them, and it reads `1+7 = 3+5` and `100/12 = 25/3` correctly.
    """
    from .tutor import _close

    text = part2 + "\n" + part3
    for index, char in enumerate(text):
        if char != "=":
            continue
        left = index
        while left > 0 and text[left - 1] in _EXPR_CHARS:
            left -= 1
        right = index + 1
        while right < len(text) and text[right] in _EXPR_CHARS:
            right += 1
        left_side, right_side = text[left:index], text[index + 1:right]
        if left > 0 and (text[left - 1].isalpha() or text[left - 1] == "_"):
            # "x - 2 = 2": part of a variable expression, not a plain equation.
            continue
        if not any(op in left_side for op in "+-*/×÷^√"):
            continue
        got, want = _eval_side(left_side), _eval_side(right_side)
        if got is None or want is None:
            continue
        if not _close(got, want):
            return f"{left_side.strip()} = {right_side.strip()}"
    return None


def _split_readings(option):
    """Both readings of an OCR split fraction: `55 16` is 55/16 or 16/55."""
    out = []
    for left, right in _SPLIT_FRACTION.findall(option or ""):
        top, bottom = float(left), float(right)
        if bottom:
            out.append(top / bottom)
        if top:
            out.append(bottom / top)
    return out


def answer_tail(part3, label):
    """The text after the last `X)` / `X:` mention of the correct letter."""
    tail = None
    for match in re.finditer(rf"\b{re.escape(label)}\s*[):.]", part3):
        tail = part3[match.end():]
    return part3 if tail is None else tail


def value_matches_option(text, option):
    """Does `text` state the numeric value of `option`?

    An OCR-damaged option such as `1 17` or `49 10` is ambiguous, so we accept
    the whole value *or* any single number it contains: rejecting a correct
    solution costs a retry, but a wrong hint must never reach a student, and the
    arithmetic check still guards the working.
    """
    from .tutor import _close, numbers, normalize

    wanted = normalize_option(option)
    if not wanted:
        return True
    seen = normalize_option(text)
    if wanted in seen:
        return True
    targets = [value for value in (_eval_side(wanted), *_split_readings(option),
                                   *numbers(normalize(option))) if value is not None]
    if not targets:
        # A symbolic option such as `√abc` cannot be compared numerically.
        return bool(wanted) and wanted in seen
    for match in re.finditer(r"[-+]?\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?|√\s*\d+(?:[.,]\d+)?", text):
        got = _eval_side(match.group(0))
        if got is not None and any(_close(got, target) for target in targets):
            return True
    return False


def ends_with_value(tail, option):
    """Does `tail` finish with the option value, with nothing after it?"""
    seen = normalize_option(tail).rstrip(".,;:!?»\"'")
    wanted = normalize_option(option).rstrip(".,;:!?")
    if not wanted:
        return True
    if seen.endswith(wanted):
        return True
    return value_matches_option(tail[-40:], option)


def check_parts(problem, parts, strict_tail=False):
    """Return (ok, reason). `parts` has part1, part2, part3 strings.

    `strict_tail` enforces the v3 format: part 3 must *end* with the correct
    option letter and value and nothing after it. Rows generated before the
    format existed are checked more leniently.
    """
    from .tutor import leak_check

    part1 = (parts.get("part1") or "").strip()
    part2 = (parts.get("part2") or "").strip()
    part3 = (parts.get("part3") or "").strip()
    if not (part1 and part2 and part3):
        return False, "missing one of the three parts"

    words = re.findall(r"\S+", part1)
    if len(words) > MAX_NUDGE_WORDS:
        return False, f"part1 has {len(words)} words (max {MAX_NUDGE_WORDS})"
    if _ARITHMETIC.search(part1):
        return False, "part1 contains an arithmetic calculation"
    leaks, reason = leak_check(part1, problem)
    if leaks:
        return False, f"part1 leaks: {reason}"
    leaks, reason = leak_check(part1 + "\n" + part2, problem)
    if leaks:
        return False, f"part1/2 leaks: {reason}"

    options = problem.get("options") or {}
    label = problem.get("label")
    if label not in options:
        return False, "problem has no valid label"
    right = options[label]

    if not re.search(rf"\b{re.escape(label)}\b", part3):
        return False, "part3 does not name the correct letter"
    if strict_tail:
        tail = answer_tail(part3, label)
        if not ends_with_value(tail, right):
            return False, "part3 must end with the correct option, not a check after it"
    elif not value_matches_option(part3, right):
        return False, "part3 does not name the correct value"

    bad = equation_error(part2, part3)
    if bad:
        return False, f"false arithmetic: {bad}"
    return True, ""
