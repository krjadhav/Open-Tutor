"""Grader tests. No network, no API key, no model. This file must stay that way.

The bar these tests defend, from learning-design.md section 16: **marking a correct student wrong
is the most damaging error the system can make.** Most of what follows is therefore not "does it
catch wrong answers" but "does it accept the many shapes of a right one".
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.types import Item                                             # noqa: E402
from services.grading import (NOT_CHECKABLE, grade, latex_to_sympy_src,   # noqa: E402
                              load_item_bank, normalise_typed)


def expr_item(answer_sympy, **kw):
    return Item(item_id=kw.pop("item_id", "t1"), node_id="alg.sign-distribution",
                stem_latex="stem", answer_sympy=answer_sympy, answer_kind="expr", **kw)


# --------------------------------------------------------------------------- the headline case

@pytest.mark.parametrize("typed", [
    "2x - 3",
    "2*x - 3",
    "x - 5 + x + 2",          # section 14 finding 2: the exact answer the model called wrong
    "-3 + 2x",
    "(4x - 6)/2",
    "2(x - 1) - 1",
    "  2X - 3  ".replace("X", "x"),
    "2x-3+0*x",
    "2 x - 3",
    "\\frac{4x-6}{2}",        # a student typing LaTeX
    "y = 2x - 3",             # restating the variable they were asked for
])
def test_equivalent_forms_are_accepted(typed):
    r = grade(expr_item("2*x - 3"), typed)
    assert r.error is None
    assert r.correct is True, f"{typed!r} is 2x - 3 and must not be marked wrong"


@pytest.mark.parametrize("typed", ["2x + 3", "x - 3", "3 - 2x", "2", "0", "2x"])
def test_genuinely_wrong_answers_are_wrong(typed):
    r = grade(expr_item("2*x - 3"), typed)
    assert r.correct is False
    assert r.error is None                 # graded, and the verdict is wrong: not an error


def test_unsimplified_rational_expression():
    """The demo item's own answer, in the form a student would actually leave it."""
    item = expr_item("-7/(x - 3)**2")
    for typed in ["-7/(x-3)^2", "-7/(x**2 - 6x + 9)", "7/(-(x-3)^2)",
                  "(2*(x-3) - (2x+1))/(x-3)^2"]:
        r = grade(item, typed)
        assert r.correct is True, typed


def test_trig_and_surd_equivalence():
    assert grade(expr_item("cos(x)**2"), "1 - sin(x)^2").correct is True
    assert grade(expr_item("sqrt(6)/3"), "2/sqrt(6)").correct is True


def test_exponent_forms():
    assert grade(expr_item("-7/x**8"), "-7x^-8").correct is True
    assert grade(expr_item("-7/x**8"), "-7*x**(-8)").correct is True


# --------------------------------------------------------------------------- answer kinds

def test_set_answers_ignore_order_and_labels():
    item = Item("s1", "alg.solving-equations", "stem",
                answer_sympy="2, 3", answer_kind="set")
    for typed in ["2, 3", "3, 2", "3,2", "x = 2, x = 3", "x=3, x=2"]:
        assert grade(item, typed).correct is True, typed
    assert grade(item, "2").correct is False
    assert grade(item, "2, 3, 4").correct is False


def test_vector_answers():
    item = Item("v1", "mv.gradient", "stem", answer_sympy="2*x, 3*y", answer_kind="vector")
    assert grade(item, "2x, 3y").correct is True
    assert grade(item, "2*x, 3*y").correct is True
    assert grade(item, "3y, 2x").correct is False        # a gradient is ordered
    assert grade(item, "2x").correct is False            # wrong shape, but no exception


# --------------------------------------------------------------------------- hostile input

@pytest.mark.parametrize("typed", [
    ")(", "2 +* 3", "((((", "2x -", "*", "\\frac{1}{", "^^^", "1/", "= = =",
])
def test_malformed_input_returns_an_error_and_never_raises(typed):
    r = grade(expr_item("2*x - 3"), typed)
    assert r.correct is False
    assert r.error is not None
    assert r.normalised is None


@pytest.mark.parametrize("typed", ["", "   ", "\n", None])
def test_empty_answer(typed):
    r = grade(expr_item("2*x - 3"), typed)
    assert r.correct is False and r.error


def test_nothing_in_the_bank_raises_on_hostile_input():
    """Every item, every nasty string. The grader sits under a text box; it cannot throw."""
    bank = load_item_bank()
    nasty = [")(", "", "DNE", "I don't know", "0", "x", "1/0", "sqrt(-1)", "999" * 40]
    for item in bank.values():
        for typed in nasty:
            r = grade(item, typed)
            assert isinstance(r.correct, bool)


def test_broken_stored_answer_is_reported_not_raised():
    r = grade(expr_item(")("), "2x - 3")
    assert r.correct is False
    assert r.error and "stored answer" in r.error


# --------------------------------------------------------------------------- the item bank

def test_item_bank_loads():
    bank = load_item_bank()
    import json as _json
    from pathlib import Path as _Path
    raw = _json.loads((_Path(__file__).resolve().parents[2] / "data/items/items.json").read_text())["items"]
    # the loader must drop nothing; a hardcoded size would fail for the wrong reason
    assert len(bank) == len(raw)
    assert all(isinstance(k, str) and v.item_id == k for k, v in bank.items())
    assert bank["gen-ai.backprop-chain-1"].answer_kind == "expr"


def test_every_generated_item_accepts_its_own_answer():
    """376 items are only useful if the grader agrees with the generator. Section 17 claims all
    132 generated drills reload and verify; this is that claim, asserted."""
    bank = load_item_bank()
    checked = 0
    for item in bank.values():
        if not item.answer_sympy:
            continue
        assert grade(item, item.answer_sympy).correct is True, item.item_id
        checked += 1
    assert checked == sum(1 for i in bank.values() if i.source == 'generated')


def test_openstax_prose_answers_are_declared_uncheckable():
    """Honest failure, not a fabricated verdict. `error` is a routing signal for the caller."""
    item = Item("os1", "lim.concept", "stem", answer_latex="Discontinuous at 1; removable")
    r = grade(item, "anything")
    assert r.correct is False and r.error == NOT_CHECKABLE

    item2 = Item("os2", "lim.concept", "stem", answer_latex="DNE")
    assert grade(item2, "DNE").error == NOT_CHECKABLE


def test_openstax_simple_latex_answers_are_gradeable():
    item = Item("os3", "der.quotient-rule", "stem",
                answer_latex="$h^{'}(x)=\\frac{3-4x}{{(2x+3)}^{4}}$")
    assert grade(item, "(3 - 4x)/(2x+3)^4").correct is True
    assert grade(item, "(3 - 4x)/(2x+3)^3").correct is False


def test_openstax_rounded_numeric_answer_accepts_the_exact_value():
    """The key says -2.67 because a textbook rounded it. A student who is exact is not wrong."""
    item = Item("os4", "der.definition", "stem", answer_latex="$-2.67$")
    assert grade(item, "-2.67").correct is True
    assert grade(item, "-8/3").correct is True
    assert grade(item, "-2.7").correct is False


def test_uncheckable_fraction_of_the_bank_is_what_we_think_it_is():
    """A regression guard on coverage. If a change to the latex subset silently drops items,
    or silently starts accepting prose, this test moves."""
    bank = load_item_bank()
    uncheckable = [i for i in bank.values() if grade(i, "0").error == NOT_CHECKABLE]
    assert len(uncheckable) == 165
    assert all(i.answer_sympy is None for i in uncheckable)


# --------------------------------------------------------------------------- the latex subset

def test_latex_converter_refuses_rather_than_guesses():
    for raw in [
        "Answers may vary.",                      # prose parses as a product of symbols
        "DNE",
        "Nowhere",
        "$10\\frac{3}{4}$",                       # mixed number: 43/4, not 10*3/4
        "a. $f(u)=-6u^{-3},$ b. $18u$",           # multi-part
        "Discontinuous at 1; removable",
        "$\\text{lim}_{x \\to 0} f(x)$",          # unhandled macro
        "Infinite discontinuities at $x=\\frac{(2k+1)\\p i}{4}$",
    ]:
        assert latex_to_sympy_src(raw) is None, raw


def test_unit_vector_answers_are_not_graded():
    """`\\frac{2}{3}i+3j` converts to something that looks like an expression and is not one:
    i and j are basis vectors. Refused at the answer-key layer."""
    item = Item("os6", "mv.gradient", "stem", answer_latex="$\\frac{2}{3}i+3j$")
    assert grade(item, "2/3 i + 3 j").error == NOT_CHECKABLE


def test_latex_converter_handles_nested_braces():
    """Regression: an earlier \\frac regex could not see through `{{(4x-3)}^{2}}`, left the
    literal `frac` behind, and sympy parsed `frac(13)(...)` as a function call equal to 0. The
    answer key silently became zero."""
    src = latex_to_sympy_src("$k^{'}(x)=-\\frac{13}{{(4x-3)}^{2}}.$")
    assert src is not None and "frac" not in src
    item = Item("os5", "der.quotient-rule", "stem",
                answer_latex="$k^{'}(x)=-\\frac{13}{{(4x-3)}^{2}}.$")
    assert grade(item, "-13/(4x-3)^2").correct is True
    assert grade(item, "0").correct is False


def test_normalise_typed():
    assert normalise_typed("x^2") == "x**2"
    assert normalise_typed("y = 2x - 3") == "2x - 3"
    assert normalise_typed("x = 2, x = 3") == "2, 3"
    assert normalise_typed("2 − x") == "2 - x"
    assert normalise_typed("2x - 3 = 0") == "2x - 3 = 0"   # not a label: left alone
