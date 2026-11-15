"""Number templates for the dyscalculia and dyslexia modes. Standard library only.

A generated solution can carry an *adaptation*: the problem rewritten with every
data number replaced by a placeholder (`{n1}`), the default (original) and a
smaller value for each placeholder, a formula for the answer, a step-by-step
working in the same placeholders, and a short-line version for dyslexia.

Template syntax:
    {n1}           the value of variable n1
    {=n1*n2+1}     a computed value (+ - * / // % ** and parentheses)

`render(template, values)` fills a template; `eval_formula(expr, values)`
evaluates an expression. Both reject anything that is not plain arithmetic, so
the board can render the small-number version without calling a model.
"""
import ast
import math
import re

_PLACEHOLDER = re.compile(r"\{(=?)([^{}]+)\}")
_NAME = re.compile(r"^n\d+$")
MAX_DYSLEXIA_WORDS = 14  # the prompt asks for 12; allow a little slack


def _eval(node, values):
    if isinstance(node, ast.Expression):
        return _eval(node.body, values)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ValueError(f"unknown variable {node.id}")
        return values[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand, values)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, values), _eval(node.right, values)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.Pow):
            if abs(right) > 12:
                raise ValueError("exponent too large")
            return left ** right
    raise ValueError("unsupported expression")


def eval_formula(expression, values):
    """Evaluate an arithmetic expression over the variables in `values`."""
    expression = str(expression).replace("×", "*").replace("÷", "/").replace("^", "**")
    return _eval(ast.parse(expression.strip(), mode="eval"), values)


def fmt(value):
    """Show 6 as `6`, 2.5 as `2.5`, 1/3 as `0.33`."""
    if isinstance(value, float):
        if math.isfinite(value) and abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def render(template, values):
    """Fill `{n1}` and `{=expr}` placeholders; raise ValueError on a bad one."""
    def fill(match):
        computed, body = match.group(1), match.group(2).strip()
        if computed:
            return fmt(eval_formula(body, values))
        if body not in values:
            raise ValueError(f"unknown placeholder {{{body}}}")
        return fmt(values[body])
    return _PLACEHOLDER.sub(fill, template or "")


def placeholders(template):
    """Variable names a template uses, directly or inside `{=...}`."""
    names = set()
    for computed, body in _PLACEHOLDER.findall(template or ""):
        if computed:
            names.update(re.findall(r"\bn\d+\b", body))
        else:
            names.add(body.strip())
    return names


def value_sets(adapt):
    """(default, small) value dicts from an adaptation's `variables`."""
    variables = adapt.get("variables") or {}
    default = {name: spec["default"] for name, spec in variables.items()}
    small = {name: spec.get("small", spec["default"]) for name, spec in variables.items()}
    return default, small


def check_adaptation(problem, adapt):
    """Return (ok, reason, small_answer). Never trusts the model's arithmetic."""
    from .solutions import value_matches_option
    from .tutor import numbers, normalize

    if not isinstance(adapt, dict) or not adapt.get("template"):
        return False, "no template", None
    variables = adapt.get("variables")
    if not isinstance(variables, dict):
        return False, "variables is not an object", None
    for name, spec in variables.items():
        if not _NAME.match(name):
            return False, f"bad variable name {name}", None
        if not isinstance(spec, dict):
            return False, f"{name} has no default/small", None
        for field in ("default", "small"):
            if not isinstance(spec.get(field), (int, float)) or isinstance(spec.get(field), bool):
                return False, f"{name}.{field} is not a number", None
    default, small = value_sets(adapt)

    template = adapt["template"]
    for field in ("template", "dyslexia_template", "steps_template"):
        text = adapt.get(field) or ""
        unknown = placeholders(text) - set(variables)
        if unknown:
            return False, f"{field} uses unknown {sorted(unknown)}", None
        try:
            render(text, default)
            render(text, small)
        except (ValueError, ZeroDivisionError, SyntaxError, OverflowError) as error:
            return False, f"{field} does not render: {error}", None
    lines = [line for line in (adapt.get("dyslexia_template") or "").split("\n") if line.strip()]
    if not lines:
        return False, "no dyslexia_template", None
    if len(lines) > 8 or any(len(line.split()) > MAX_DYSLEXIA_WORDS for line in lines):
        return False, f"dyslexia_template lines must be short (max {MAX_DYSLEXIA_WORDS} words)", None
    unused = set(variables) - placeholders(template)
    if unused:
        return False, f"template does not use {sorted(unused)}", None

    # Rendering with the defaults must give back numbers that the problem has.
    original = set(round(x, 6) for x in numbers(normalize(problem.get("text", ""))))
    for value in default.values():
        if round(float(value), 6) not in original:
            return False, f"default {fmt(value)} is not a number in the problem", None

    formula = adapt.get("answer_formula")
    small_answer = None
    if formula:
        options = problem.get("options") or {}
        right = options.get(problem.get("label"), "")
        try:
            got = eval_formula(formula, default)
            small_answer = eval_formula(formula, small)
        except (ValueError, ZeroDivisionError, SyntaxError, OverflowError) as error:
            return False, f"answer_formula does not evaluate: {error}", None
        if not value_matches_option(fmt(got), right):
            return False, f"answer_formula gives {fmt(got)}, correct is {right}", None
        if not math.isfinite(float(small_answer)):
            return False, "small answer is not finite", None
    return True, "", small_answer
