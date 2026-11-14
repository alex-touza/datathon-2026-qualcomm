"""Verification rules for the precomputed three-part tutor solutions.

The model generates a nudge, the working, and the conclusion. Nothing is
accepted until it passes these checks. The tutor only reads verified rows.
"""
import ast
import re

LETTERS = "ABCDE"
MAX_NUDGE_WORDS = 20
_ARITHMETIC = re.compile(r"\d\s*[+\-*/×÷]\s*\d")
_EQUATION = re.compile(
    r"([-+]?\d+(?:[.,]\d+)?(?:\s*[+\-*/×÷]\s*[-+]?\d+(?:[.,]\d+)?)+)"
    r"\s*=\s*([-+]?\d+(?:[.,]\d+)?)")


def _number(text):
    return float(text.replace("×", "*").replace("÷", "/").replace(",", "."))


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
    raise ValueError("unsupported arithmetic")


def safe_eval(expression):
    """Evaluate + - * / and parentheses without eval()."""
    expression = expression.replace("×", "*").replace("÷", "/").replace(",", ".")
    return _eval(ast.parse(expression, mode="eval"))


def check_parts(problem, parts):
    """Return (ok, reason). `parts` has part1, part2, part3 strings."""
    from .tutor import _close, leak_check, numbers, option_values

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
    right_nums, _ = option_values(right)

    def norm(text):
        return re.sub(r"\s+", "", text.lower().replace("²", "2").replace("³", "3"))

    if not re.search(rf"\b{re.escape(label)}\b", part3):
        return False, "part3 does not name the correct letter"
    if right.strip() and norm(right) not in norm(part3):
        if not (right_nums and any(_close(value, n)
                                   for value in numbers(part3) for n in right_nums)):
            return False, "part3 does not name the correct value"
    # The model may write "Correct option: D) 10" rather than "Answer: D".
    # The letter and value checks above are what matter.

    for expression, expected in _EQUATION.findall(part2 + "\n" + part3):
        try:
            got = safe_eval(expression)
            want = _number(expected)
        except (ValueError, SyntaxError, ZeroDivisionError):
            continue
        if not _close(got, want):
            return False, f"false arithmetic: {expression} = {expected}"

    if right_nums:
        found = numbers(part3)
        if not found:
            return False, "part3 has no numbers to check"
        if not any(_close(found[-1], value) for value in right_nums):
            return False, "part3 must end with the correct option, not a check after it"
    return True, ""
