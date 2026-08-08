"""The drill templates, and the two properties of the generated bank that have actually broken.

Two failure modes are worth this much test code, because both shipped:

  1. **The answer drifting from the question.** Every answer here is computed by sympy from the
     same spec that renders the stem, so the tests re-derive each answer by an independent route
     (`sp.idiff` for implicit differentiation, substitute-then-differentiate for the multivariable
     chain rule) rather than by calling the function under test twice.
  2. **LaTeX the app cannot draw.** `app/static/app.js` has no LaTeX library: any command outside
     the twenty it knows loses its backslash and prints as a literal word. `\\begin{matrix}` reached
     a card that way once and `\\sec` a second time, so `unsupported_commands` is asserted over
     every stem and every answer in the whole generated bank, not merely over new items.

A third property is asserted bank-wide and is what makes the first one checkable at all: every
generated item still rebuilds from its own `check` spec, byte for byte. An item whose stored
answer no longer follows from its stored question is unfalsifiable otherwise.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import sympy as sp

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import drill_tasks as dt                                    # noqa: E402
from services.grading import grade                                        # noqa: E402
from engine.types import Item                                             # noqa: E402

ITEMS_PATH = ROOT / "data" / "items" / "items.json"
GENERATED_PATH = ROOT / "data" / "items" / "generated_items.json"

BANK = json.loads(ITEMS_PATH.read_text())["items"]
GENERATED = [i for i in BANK if i["source"] == "generated"]
GENERATED_SOURCE = json.loads(GENERATED_PATH.read_text())["items"]

#: The nine nodes that had no generated items and were served entirely from OpenStax. Their
#: OpenStax stems carry a "For the following exercises, ..." group instruction, run to 193
#: characters and are not translated, which is what these drills exist to replace. A stem that
#: does not fit a phone card has not replaced anything, so the length is asserted.
REPLACED_OPENSTAX_ONLY_NODES = frozenset({
    "lim.concept", "lim.direct-substitution", "lim.indeterminate-factoring",
    "der.definition", "der.chain-rule", "der.implicit",
    "mv.partial-derivative", "mv.chain-rule-multivar", "mv.directional-derivative",
})
CARD_STEM_LIMIT = 110

x, y, z, t, h = sp.symbols("x y z t h")


def as_item(row: dict) -> Item:
    return Item(item_id=row["item_id"], node_id=row["node_id"], stem_latex=row["stem_latex"],
                answer_latex=row.get("answer_latex"), answer_sympy=row.get("answer_sympy"),
                answer_kind=row.get("answer_kind"),
                difficulty_b=float(row.get("difficulty_b") or 0.0),
                encompasses=tuple(row.get("encompasses") or ()), source=row["source"])


def answer_of(task: str, params: dict):
    return dt.build(task, params)[2]


# --------------------------------------------------------------------------------------
# The new tasks compute the right answer, derived independently
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("expr", ["5*x - 2", "x**2 + 4*x", "3*x**2 - 5*x", "x**3", "2/x",
                                  "sqrt(x)", "1/(x + 1)"])
def test_derivative_from_definition_agrees_with_the_difference_quotient(expr):
    """The node is the difference quotient, so the test takes that limit itself."""
    e = dt.P(expr)
    from_definition = sp.simplify(sp.limit((e.subs(x, x + h) - e) / h, h, 0))
    assert sp.simplify(answer_of("derivative_from_definition", {"expr": expr})
                       - from_definition) == 0


def test_derivative_from_definition_stem_names_the_method_and_the_function():
    stem, _, _ = dt.build("derivative_from_definition", {"expr": "3*x**2 - 5*x"})
    assert stem == ("Use the limit definition of the derivative to find $f'(x)$ for "
                    "$f(x) = 3 x^{2} - 5 x$.")


@pytest.mark.parametrize("expr", ["(2*x + 1)**3", "(3*x**2 + 1)**5", "sin(2*x)", "exp(3*x)",
                                  "sqrt(x**2 + 1)", "cos(x**2)", "log(x**2 + 1)"])
def test_chain_rule_answer_is_the_derivative(expr):
    assert sp.simplify(answer_of("chain_rule", {"expr": expr}) - sp.diff(dt.P(expr), x)) == 0


@pytest.mark.parametrize("expr", ["x**5", "sin(x)", "3*x**2 - 5*x", "exp(x)", "x*sin(x)"])
def test_chain_rule_refuses_a_drill_that_does_not_need_the_chain_rule(expr):
    """A power rule or product rule drill filed under der.chain-rule teaches nothing about the
    chain rule and, worse, sends blame to the wrong node when the student gets it wrong."""
    with pytest.raises(ValueError):
        dt.build("chain_rule", {"expr": expr})


@pytest.mark.parametrize("lhs,rhs", [("x**2 + y**2", "25"), ("x*y", "1"),
                                     ("x**2 + x*y + y**2", "7"), ("x**2*y + y**3", "10"),
                                     ("x**3 + y**3 - 3*x*y", "0"), ("x + sin(y)", "4")])
def test_implicit_dydx_agrees_with_sympy_idiff(lhs, rhs):
    """`sp.idiff` differentiates the equation treating y as y(x) and solves. Different route,
    same answer, so the -F_x/F_y shortcut is checked rather than assumed."""
    expected = sp.idiff(dt.P(lhs) - dt.P(rhs), y, x)
    got = answer_of("implicit_dydx", {"expr": lhs, "expr2": rhs})
    assert sp.simplify(sp.together(got - expected)) == 0


@pytest.mark.parametrize("lhs,rhs,why", [
    ("x**2 + y", "5", "linear in y, so you would just solve for y"),
    ("x**2 + 3*x", "5", "no y at all"),
    ("y**2 + y", "5", "no x at all"),
])
def test_implicit_dydx_refuses_an_equation_that_is_not_implicit(lhs, rhs, why):
    with pytest.raises(ValueError):
        dt.build("implicit_dydx", {"expr": lhs, "expr2": rhs})


@pytest.mark.parametrize("expr,inner", [
    ("x + y**2", "3*t, 2*t"), ("x**2*y", "t**2, 3*t"), ("x*y", "t**2 + 1, 2*t"),
    ("x**2 + y**2", "2*t, 3*t"), ("x*y**2", "t + 1, t**2"), ("x*y", "sin(t), cos(t)"),
    ("log(x + y)", "t**2, 3*t"),
])
def test_chain_rule_multivar_agrees_with_substituting_first(expr, inner):
    """Substitute then differentiate is what the multivariable chain rule claims to equal. If the
    two ever disagreed, the drill would be teaching a false identity."""
    gx, gy = [dt.P(s) for s in inner.split(",")]
    direct = sp.diff(dt.P(expr).subs({x: gx, y: gy}, simultaneous=True), t)
    got = answer_of("chain_rule_multivar", {"expr": expr, "expr2": inner})
    assert sp.simplify(got - direct) == 0


@pytest.mark.parametrize("expr,inner", [
    ("x**2", "t**2, 3*t"),          # only one path, so no sum to get wrong
    ("x*y", "t**2, 5"),             # y does not move with t
    ("x*y", "t**2"),                # only one inner function given
])
def test_chain_rule_multivar_refuses_a_spec_with_nothing_multivariable_about_it(expr, inner):
    with pytest.raises(ValueError):
        dt.build("chain_rule_multivar", {"expr": expr, "expr2": inner})


@pytest.mark.parametrize("expr,at,direction", [
    ("x*y", "2, 3", "4, -3"), ("x**2*y", "1, 2", "3, 4"), ("x**2 + y**2", "1, 2", "1, 1"),
    ("x**2 - y**2", "3, 1", "1, -1"), ("x*y + y**2", "1, 3", "5, 12"),
    ("sin(x)*y", "0, 2", "5, -12"), ("x*y*z", "1, 2, 3", "2, 1, 2"),
])
def test_directional_derivative_uses_a_unit_direction(expr, at, direction):
    e = dt.P(expr)
    point = [dt.P(c) for c in at.split(",")]
    u = sp.Matrix([dt.P(c) for c in direction.split(",")])
    names = [x, y, z][:len(point)]
    where = dict(zip(names, point))
    grad = sp.Matrix([sp.diff(e, n).subs(where, simultaneous=True) for n in names])
    expected = grad.dot(u) / sp.sqrt(sum(c ** 2 for c in u))
    got = answer_of("directional_derivative", {"expr": expr, "at": at, "expr2": direction})
    assert sp.simplify(got - expected) == 0


def test_directional_derivative_normalises_a_direction_that_is_not_unit_length():
    """(3, 4) has length 5. The drill must divide by it: the same drill without the division
    would be off by a factor of five and would look plausible."""
    got = answer_of("directional_derivative",
                    {"expr": "x**2*y", "at": "1, 2", "expr2": "3, 4"})
    assert got == sp.Rational(16, 5)


@pytest.mark.parametrize("direction,why", [
    ("3/5, 4/5", "already a unit vector, so forgetting to normalise cannot be detected"),
    ("0, 1", "already a unit vector"),
    ("0, 0", "zero vector"),
])
def test_directional_derivative_refuses_a_direction_that_does_not_need_normalising(direction, why):
    with pytest.raises(ValueError):
        dt.build("directional_derivative",
                 {"expr": "x**2*y", "at": "1, 2", "expr2": direction})


def test_directional_derivative_refuses_a_point_where_the_gradient_vanishes():
    with pytest.raises(ValueError):
        dt.build("directional_derivative", {"expr": "x**2*y", "at": "0, 0", "expr2": "3, 4"})


def test_a_vector_never_reaches_a_stem_as_a_matrix():
    """`_row`, not sympy's latex. `\\begin{matrix}` printed as the words "beginmatrix" on a card."""
    stem, answer, _ = dt.build("directional_derivative",
                               {"expr": "x*y*z", "at": "1, 2, 3", "expr2": "2, 1, 2"})
    assert "(1, 2, 3)" in stem and "(2, 1, 2)" in stem
    assert "matrix" not in stem and "matrix" not in answer


# --------------------------------------------------------------------------------------
# The three limit nodes are told apart by what substitution does, not by wording
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("expr,at", [("(3*x + 5)", "2"), ("(x**2 - 4*x + 7)", "3"),
                                     ("(x + 4)/(x - 1)", "3"), ("sqrt(x + 7)", "2"),
                                     ("x*cos(x)", "pi")])
def test_limit_by_substitution_is_the_substituted_value(expr, at):
    e, a = dt.P(expr), dt.P(at)
    got = answer_of("limit_substitution", {"expr": expr, "at": at})
    assert sp.simplify(got - e.subs(x, a)) == 0
    assert sp.simplify(got - sp.limit(e, x, a)) == 0


def test_limit_by_substitution_refuses_an_expression_that_is_undefined_there():
    """0/0 belongs to lim.indeterminate-factoring. Filing it here would blame the wrong node."""
    with pytest.raises(ValueError):
        dt.build("limit_substitution", {"expr": "(x**2 - 4)/(x - 2)", "at": "2"})


@pytest.mark.parametrize("expr,at", [("(x**2 - 25)/(x - 5)", "5"), ("(2*x**2 - 6*x)/(x - 3)", "3"),
                                     ("(x**2 - 4)/(x**2 - 2*x)", "2"), ("sin(2*x)/x", "0")])
def test_limit_informal_needs_a_point_where_the_function_has_no_value(expr, at):
    e, a = dt.P(expr), dt.P(at)
    assert not e.subs(x, a).is_finite, "the drill is only about the concept if f(a) is missing"
    got = answer_of("limit_informal", {"expr": expr, "at": at})
    assert sp.simplify(got - sp.limit(e, x, a)) == 0


def test_limit_informal_refuses_a_function_that_is_defined_at_the_point():
    with pytest.raises(ValueError):
        dt.build("limit_informal", {"expr": "x**2 + 1", "at": "2"})


@pytest.mark.parametrize("expr,at", [("(x**2 - 7*x + 12)/(x - 3)", "3"),
                                     ("(x**3 - 1)/(x - 1)", "1"),
                                     ("(x**2 + 2*x - 8)/(x**2 - 4)", "2"),
                                     ("(sqrt(x) - 1)/(x - 1)", "1")])
def test_limit_indeterminate_is_really_zero_over_zero(expr, at):
    a = dt.P(at)
    num, den = sp.fraction(sp.together(dt.P(expr)))
    assert sp.simplify(num.subs(x, a)) == 0 and sp.simplify(den.subs(x, a)) == 0
    assert sp.simplify(answer_of("limit_indeterminate", {"expr": expr, "at": at})
                       - sp.limit(dt.P(expr), x, a)) == 0


@pytest.mark.parametrize("expr,at", [("(x**2 - 4)/(x - 3)", "3"), ("x**2 + 1", "2"),
                                     ("(x + 1)/(x - 2)", "5")])
def test_limit_indeterminate_refuses_anything_substitution_already_answers(expr, at):
    with pytest.raises(ValueError):
        dt.build("limit_indeterminate", {"expr": expr, "at": at})


# --------------------------------------------------------------------------------------
# Rendering: the whole bank, every stem and every answer
# --------------------------------------------------------------------------------------

def test_the_allow_list_is_the_one_the_renderer_implements():
    """A cross-check against app/static/app.js, so this list cannot quietly drift from the code
    it is a model of. Anything the renderer does not name is printed as a bare word."""
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    for symbol in ("cdot", "nabla", "partial", "pi", "alpha", "to", "infty", "times", "div"):
        assert re.search(rf"\b{symbol}:", js), f"{symbol} is not in the renderer's symbol table"
    for macro in ("frac", "sqrt", "text", "left", "right"):
        assert macro in js, f"the renderer does not handle \\{macro}"
    # `\sin`, `\cos`, `\lim` and friends are not special-cased: they fall through to the rule that
    # drops the backslash and prints the word, which reads correctly. That fallback is also what
    # turns `\mathbf` into "mathbf", which is why the allow-list exists at all.
    assert "return word;" in js


@pytest.mark.parametrize("item", GENERATED, ids=[i["item_id"] for i in GENERATED])
def test_no_generated_stem_or_answer_uses_latex_the_app_cannot_draw(item):
    bad = sorted(set(dt.unsupported_commands(item["stem_latex"]))
                 | set(dt.unsupported_commands(item.get("answer_latex") or "")))
    assert not bad, f"{item['item_id']} would print {bad} as literal words"


def test_the_generated_source_file_agrees_with_the_shipped_bank():
    """items.json is built from generated_items.json, so a drill can only be checked in one of
    them if they cannot disagree."""
    shipped = {i["item_id"]: i for i in GENERATED}
    for row in GENERATED_SOURCE:
        assert row["item_id"] in shipped, row["item_id"]
        for field in ("stem_latex", "answer_latex", "answer_sympy", "answer_kind", "check"):
            assert shipped[row["item_id"]][field] == row[field], (row["item_id"], field)
    assert len(shipped) == len(GENERATED_SOURCE)


def test_build_refuses_to_emit_latex_the_app_cannot_draw():
    """sec(x) is a perfectly good derivative and an unrenderable one: `\\sec` is not in the twenty
    commands the app knows, so the drill is refused at build time rather than found on a card."""
    with pytest.raises(ValueError, match="cannot render"):
        dt.build("differentiate", {"expr": "sec(x)", "var": "x"})


def test_unsupported_commands_finds_the_two_that_actually_shipped():
    assert dt.unsupported_commands(r"\left[\begin{matrix}2 x\\2 y\end{matrix}\right]") == \
        ["begin", "end"]
    assert dt.unsupported_commands(r"\tan{\left(x \right)} \sec{\left(x \right)}") == ["sec"]
    assert dt.unsupported_commands(r"\frac{\partial f}{\partial x} \cdot \nabla f") == []


# --------------------------------------------------------------------------------------
# The bank as a whole
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("item", GENERATED, ids=[i["item_id"] for i in GENERATED])
def test_every_generated_item_still_rebuilds_from_its_own_spec(item):
    """The stored question and the stored answer must both still follow from the stored spec.
    Without this, "the answer cannot drift from the question" is a claim about the generator at
    the time it ran, not about the bank that ships."""
    check = item["check"]
    stem, answer_latex, answer = dt.build(check["task"], check["params"])
    assert stem == item["stem_latex"]
    assert answer_latex == item["answer_latex"]
    kind, serialised = dt.serialize_answer(answer)
    assert (kind, serialised) == (item["answer_kind"], item["answer_sympy"])


@pytest.mark.parametrize("item", GENERATED, ids=[i["item_id"] for i in GENERATED])
def test_no_generated_answer_is_a_float(item):
    """Exact rationals only. 0.30000000000000004 as an answer key marks a correct student wrong,
    and a stem that says 0.1 while its spec says 1/10 is the same bug one step earlier."""
    answer = dt.load_answer(item["answer_kind"], item["answer_sympy"])
    parts = list(answer) if isinstance(answer, sp.MatrixBase) else [answer]
    for part in parts:
        assert not part.atoms(sp.Float), f"{item['item_id']} answer carries a float: {part}"
    for field in ("expr", "expr2", "at", "lr"):
        src = str(item["check"]["params"].get(field) or "")
        assert not re.search(r"\d\.\d", src), f"{item['item_id']} spec carries a float: {src}"


@pytest.mark.parametrize("item", GENERATED, ids=[i["item_id"] for i in GENERATED])
def test_the_production_grader_accepts_every_generated_answer(item):
    result = grade(as_item(item), item["answer_sympy"])
    assert result.correct and not result.error, (item["item_id"], result.error)


def test_every_node_in_the_graph_has_generated_drills():
    """A node served only from OpenStax is a node whose stems are untranslated and whose items
    cannot be twinned. Nine nodes were in that state; none are now."""
    graph = json.loads((ROOT / "data" / "graph" / "nodes.json").read_text())
    per_node: dict[str, int] = {}
    for item in GENERATED:
        if item.get("gradeable"):
            per_node[item["node_id"]] = per_node.get(item["node_id"], 0) + 1
    thin = {n["id"]: per_node.get(n["id"], 0) for n in graph["nodes"]
            if per_node.get(n["id"], 0) < 6}
    assert not thin, f"nodes with fewer than six gradeable generated drills: {thin}"


@pytest.mark.parametrize("item", [i for i in GENERATED
                                  if i["node_id"] in REPLACED_OPENSTAX_ONLY_NODES],
                         ids=[i["item_id"] for i in GENERATED
                              if i["node_id"] in REPLACED_OPENSTAX_ONLY_NODES])
def test_the_drills_replacing_openstax_stems_fit_a_card(item):
    assert len(item["stem_latex"]) <= CARD_STEM_LIMIT, item["stem_latex"]


@pytest.mark.parametrize("item", [i for i in GENERATED
                                  if i["node_id"] in REPLACED_OPENSTAX_ONLY_NODES],
                         ids=[i["item_id"] for i in GENERATED
                              if i["node_id"] in REPLACED_OPENSTAX_ONLY_NODES])
def test_the_drills_replacing_openstax_stems_are_translated(item):
    """The reason these nodes needed drills at all: an OpenStax stem is English on a Hindi card."""
    entries = json.loads((ROOT / "data" / "i18n" / "hi.json").read_text(encoding="utf-8"))
    hindi = entries["entries"].get("item_stem:" + item["item_id"])
    assert hindi, f"{item['item_id']} has no Hindi stem"
    assert re.search(r"[ऀ-ॿ]", hindi), item["item_id"]


def test_a_stem_is_rendered_from_the_source_not_from_sympy():
    """The bug this file fixed once: sympy normalises, so rendering the parsed expression turns
    `-( (2x + 3) - (x - 5) )` into its own answer."""
    stem, _, _ = dt.build("expand", {"expr": "-( (2*x + 3) - (x - 5) )"})
    assert "-( (2 x + 3) - (x - 5) )" in stem
