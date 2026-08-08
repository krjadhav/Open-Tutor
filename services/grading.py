"""The deterministic grader. This is the ONLY thing in the system that decides correctness.

Why this module exists at all, rather than asking the model: learning-design.md section 14
finding 2. Across three passes the diagnosis model scored 83% on error cases but only 4/6 on
controls, and the single worst outcome in the whole experiment was it inventing a
sign-distribution error inside `x - 5 + x + 2`, a correct answer that was merely unsimplified.
Telling a student who was right that they were wrong is the most damaging thing this product can
do. A CAS cannot make that mistake, so the CAS decides and the model is only ever shown answers
the CAS has already ruled wrong.

Two consequences run through everything below:

  1. **Be generous about form.** `x - 5 + x + 2`, `2x - 3` and `-3 + 2x` are the same answer.
     So are `y = 5x/3 - 16/3` and `5x/3 - 16/3`, because students restate the variable they were
     asked for. Equivalence is decided symbolically, never by string comparison.
  2. **Never raise, and never guess.** Malformed student input returns an error, it does not
     blow up the request. An item whose answer key we cannot machine-check returns
     `error=NOT_CHECKABLE` rather than a fabricated verdict, so the caller can route around it.

The item bank is honest about this: 132 of 376 items were generated spec-first and carry an
`answer_sympy` that round-trips (section 17). The other 244 are scraped OpenStax answer keys,
many of which are prose ("Discontinuous at 1; removable"), multi-part ("a. ... b. ..."), or
mojibake from the source HTML. Those cannot be graded and we say so.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import sympy as sp

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:          # the repo is not installed as a package
    sys.path.insert(0, str(_REPO_ROOT))

from engine.types import Item                                          # noqa: E402
from pipeline.drill_tasks import (P, check_student_answer,             # noqa: E402
                                  load_answer, serialize_answer)

DEFAULT_ITEM_BANK = "data/items/items.json"

#: Returned when the item's answer key is not something a CAS can check. It is a routing signal,
#: not a verdict on the student: the caller should skip the item or fall back to self-report.
NOT_CHECKABLE = "not machine checkable"


@dataclass(frozen=True)
class GradeResult:
    """`correct` is the verdict. `error` being set means we could not grade, not that the student
    was wrong: always read `error` before showing `correct` to anyone."""
    correct: bool
    normalised: Optional[str] = None       # the student's answer as sympy re-rendered it
    error: Optional[str] = None


# --------------------------------------------------------------------------- item bank

def load_item_bank(path: str | Path = DEFAULT_ITEM_BANK) -> dict[str, Item]:
    """Load `items.json` into `{item_id: Item}`.

    Relative paths resolve against the repo root as well as the cwd, so a caller started from
    anywhere still finds the bank. Unknown keys in the JSON (`check`, `answer_is_checkable`) are
    pipeline bookkeeping and are deliberately dropped: `Item` is the contract.
    """
    import json

    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = _REPO_ROOT / path
    raw = json.loads(p.read_text())
    rows = raw["items"] if isinstance(raw, dict) else raw

    bank: dict[str, Item] = {}
    for r in rows:
        bank[r["item_id"]] = Item(
            item_id=r["item_id"],
            node_id=r["node_id"],
            stem_latex=r["stem_latex"],
            answer_latex=r.get("answer_latex"),
            answer_sympy=r.get("answer_sympy"),
            answer_kind=r.get("answer_kind"),
            difficulty_b=float(r.get("difficulty_b") or 0.0),
            encompasses=tuple(r.get("encompasses") or ()),
            source=r.get("source", "openstax"),
        )
    return bank


# --------------------------------------------------------------------------- latex fallback

# Names allowed to be longer than one character in a parsed answer. Anything else means we are
# looking at prose ("DNE", "Nowhere", "Answers may vary") or at a latex macro that survived the
# conversion, and in both cases sympy would happily parse it into a product of symbols and we
# would grade against nonsense.
_SAFE_NAMES = {"sin", "cos", "tan", "sec", "csc", "cot", "ln", "log", "exp", "sqrt",
               "asin", "acos", "atan", "arcsin", "arccos", "arctan",
               "sinh", "cosh", "tanh", "pi", "E"}

_MACROS = {"\\sin": "sin", "\\cos": "cos", "\\tan": "tan", "\\sec": "sec", "\\csc": "csc",
           "\\cot": "cot", "\\ln": "ln", "\\log": "log", "\\exp": "exp", "\\pi": "pi"}

# "the thing on the left of the equals sign is a name, not mathematics": f'(x), y, dy/dx,
# \frac{\partial z}{\partial y}, f_{xy}. Used to drop restated labels from both the answer key
# and the student's typing.
_LABEL = re.compile(r"^[A-Za-z\\][A-Za-z0-9_^{}'()\\,/. ]*$")

# i, j, k are unit vectors in the OpenStax answer keys, not scalars. Parsing them as symbols
# produces an expression that looks checkable and is not.
_UNIT_VECTORS = {sp.Symbol("i"), sp.Symbol("j"), sp.Symbol("k")}


def _strip_label(part: str) -> str:
    """`y = 5x/3 - 16/3` -> `5x/3 - 16/3`. Applied to student input as well as to answer keys."""
    if part.count("=") != 1:
        return part
    lhs, rhs = part.split("=")
    if rhs.strip() and _LABEL.match(lhs.strip()):
        return rhs.strip()
    return part


def normalise_typed(typed: str) -> str:
    """Tidy what the student typed without changing its mathematics.

    Applied per comma-separated component so that `x = 2, x = 3` on a solve item becomes
    `2, 3`, which is what `load_answer("set", ...)` expects.
    """
    s = (typed or "").strip()
    s = s.replace("\u2212", "-").replace("\u00b7", "*").replace("\u00d7", "*")
    # Students type `x^2`. sympy's standard transformations read `^` as XOR, which either fails
    # to parse or, worse, parses into something else entirely. Exclusive-or is not a thing anyone
    # types into a calculus answer box, so this substitution is free.
    s = s.replace("^", "**").replace("****", "**")
    if not s:
        return s
    return ", ".join(_strip_label(part.strip()) for part in s.split(","))


def latex_to_sympy_src(raw: str, *, require_math: bool = True,
                       allow_names: frozenset[str] | set[str] = frozenset()) -> Optional[str]:
    """Convert a small, explicitly whitelisted subset of LaTeX to sympy source, or return None.

    Returning None is the normal outcome and is not a failure: it means "refuse to guess". The
    rule everywhere below is that anything unrecognised aborts the conversion rather than being
    stripped, because stripping is how you get silent wrong answers. Two real examples from the
    bank that this policy catches:

      - `-\\frac{13}{{(4x-3)}^{2}}` has nested braces. An earlier version whose \\frac regex did
        not handle them left the literal `frac` behind, deleted the backslash, and sympy parsed
        `frac(13)(...)` as a function call evaluating to 0. The answer key would have been zero.
      - `10\\frac{3}{4}` is the mixed number 43/4, not 10 * 3/4. We refuse mixed numbers rather
        than get them wrong.

    With `require_math=False` the `$...$` wrapper is optional, which lets the same converter be
    a second chance at student input typed in LaTeX. `allow_names` carries the multi-character
    variables the item legitimately uses (`w1`, `w2` in the backprop drills), which would
    otherwise be rejected as prose.
    """
    if not raw:
        return None
    a = raw.strip()
    if a.startswith("$") and a.endswith("$") and a.count("$") == 2:
        body = a[1:-1].strip()
    elif "$" in a:
        return None                                  # prose interleaved with math
    elif require_math and not re.fullmatch(r"-?\d+(\.\d+)?", a):
        return None                                  # bare text: only plain numbers are trusted
    else:
        body = a
    body = body.rstrip(".").strip()
    if not body:
        return None
    if re.search(r"\d\s*\\[dt]?frac", body):
        return None                                  # mixed number, ambiguous
    if re.search(r"\b[a-e]\.\s", body) or ";" in body:
        return None                                  # multi-part answer
    body = _strip_label(body)
    if "=" in body:
        return None

    s = body
    s = s.replace("\\left", "").replace("\\right", "")
    s = s.replace("\\,", " ").replace("\\;", " ").replace("\\!", "").replace("\\ ", " ")
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("\u00b7", "*")
    s = s.replace("\u2212", "-")
    for macro, fn in _MACROS.items():
        s = s.replace(macro, fn)
    s = _expand_braced(s, r"\\[dt]?frac\s*\{(%s)\}\s*\{(%s)\}", r"((\1)/(\2))")
    s = _expand_braced(s, r"\\sqrt\s*\{(%s)\}", r"sqrt(\1)")
    s = _expand_braced(s, r"\^\s*\{(%s)\}", r"**(\1)")
    s = re.sub(r"\^\s*(-?[0-9A-Za-z])", r"**(\1)", s)
    if "\\" in s:
        return None                                  # an unhandled macro is left: refuse
    s = s.replace("{", "(").replace("}", ")")
    if s.count("(") != s.count(")"):
        return None
    s = s.strip()
    if not s or not re.fullmatch(r"[0-9A-Za-z_+\-*/(). ]+", s):
        return None
    for name in set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", s)):
        if len(name) > 1 and name not in _SAFE_NAMES and name not in allow_names:
            return None                              # prose, or a macro that lost its backslash
    return s


_BRACED = r"(?:[^{}]|\{[^{}]*\})*"


def _expand_braced(s: str, pattern: str, repl: str, limit: int = 20) -> str:
    """Apply a brace-taking latex macro repeatedly, innermost first."""
    rx = re.compile(pattern % ((_BRACED,) * pattern.count("%s")))
    for _ in range(limit):
        new = rx.sub(repl, s)
        if new == s:
            return s
        s = new
    return s


def _from_latex(answer_latex: Optional[str]):
    """Answer key -> sympy expression, or None if it is not safely convertible."""
    src = latex_to_sympy_src(answer_latex or "")
    if src is None:
        return None
    try:
        expr = P(src)
    except Exception:                                # noqa: BLE001
        return None
    try:
        if expr.free_symbols & _UNIT_VECTORS:
            return None
        # `e^{x}` in a textbook is Euler's number, and a student typing exp(x) must not be
        # marked wrong for it.
        expr = expr.subs(sp.Symbol("e"), sp.E)
    except Exception:                                # noqa: BLE001
        return None
    return expr


# --------------------------------------------------------------------------- comparison

def _decimals(text: Optional[str]) -> Optional[int]:
    """Decimal places in a printed answer key, e.g. `-2.67` -> 2.

    OpenStax rounds numeric answers. A student who computes the exact value is right, so when
    the key is a rounded decimal we compare at the key's own precision.
    """
    if not text:
        return None
    places = [len(m) for m in re.findall(r"\.(\d+)", text)]
    return max(places) if places else None


def _numeric_match(expected, got, key_text: Optional[str] = None) -> bool:
    """Second-chance comparison for number-valued answers.

    `sp.simplify` occasionally fails to close a surd identity, and textbook keys are rounded.
    Neither is the student's fault. Symbolic equality is still tried first; this only ever
    turns a False into a True.
    """
    try:
        if getattr(expected, "free_symbols", set()) or getattr(got, "free_symbols", set()):
            return False
        a, b = complex(sp.N(expected)), complex(sp.N(got))
    except Exception:                                # noqa: BLE001
        return False
    if abs(a - b) <= 1e-9 * max(1.0, abs(a)):
        return True
    d = _decimals(key_text)
    if d is not None and abs(a.imag) < 1e-12 and abs(b.imag) < 1e-12:
        return round(b.real, d) == round(a.real, d)
    return False


# --------------------------------------------------------------------------- grade

def grade(item: Item, typed_answer: str) -> GradeResult:
    """Is `typed_answer` equivalent to this item's answer key?

    Never raises. Every failure path returns a `GradeResult` with `error` set, because this
    function sits directly under a text box that a fifteen-year-old is typing into.
    """
    typed_raw = "" if typed_answer is None else str(typed_answer)
    if not typed_raw.strip():
        return GradeResult(False, None, "no answer given")

    typed = normalise_typed(typed_raw)
    kind = item.answer_kind or "expr"

    # ---- path 1: a spec-generated item with a real sympy answer. This is the trusted path.
    if item.answer_sympy:
        try:
            expected = load_answer(kind, item.answer_sympy)
        except Exception as e:                       # noqa: BLE001
            return GradeResult(False, None, f"stored answer could not be loaded: {e}")
        student, typed, err = _parse_student(kind, typed, expected)
        if student is None:
            return GradeResult(False, None, err)
        correct = check_student_answer(expected, typed)
        if not correct and kind == "expr":
            correct = _numeric_match(expected, student, item.answer_sympy)
        return GradeResult(bool(correct), _render(student), None)

    # ---- path 2: a scraped answer key. Only graded when the latex converts unambiguously.
    expected = _from_latex(item.answer_latex)
    if expected is None:
        return GradeResult(False, None, NOT_CHECKABLE)
    student, typed, err = _parse_student("expr", typed, expected)
    if student is None:
        return GradeResult(False, None, err)
    try:
        student = student.subs(sp.Symbol("e"), sp.E)
        correct = sp.simplify(sp.together(expected - student)) == 0
    except Exception:                                # noqa: BLE001
        correct = False
    if not correct:
        correct = _numeric_match(expected, student, item.answer_latex)
    return GradeResult(bool(correct), _render(student), None)


def _parse_student(kind: str, typed: str, expected=None):
    """(expression, sympy-syntax source, None) or (None, typed, error message).

    Tries plain syntax first, then the LaTeX subset. The second return value is what the caller
    must hand to `check_student_answer`: if the student typed LaTeX, the raw string is not
    parseable by the drill grader and passing it through would silently mark them wrong.
    """
    try:
        return load_answer(kind, typed), typed, None
    except Exception:                                # noqa: BLE001
        pass
    names = set()
    try:
        names = {str(sym) for sym in expected.free_symbols}
    except Exception:                                # noqa: BLE001
        pass
    src = latex_to_sympy_src(typed, require_math=False, allow_names=names)
    if src is not None:
        try:
            return load_answer(kind, src), src, None
        except Exception:                            # noqa: BLE001
            pass
    return None, typed, "could not read that answer"


def _render(student) -> Optional[str]:
    try:
        return serialize_answer(student)[1]
    except Exception:                                # noqa: BLE001
        return None
