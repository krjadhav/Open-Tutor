"""Tests for isomorphic twin generation. No network: sympy and the item bank, nothing else.

A twin is what turns hint rung 4 from a giveaway back into evidence. That only works if three
things hold, and each of them is a section below:

  - the twin is a **different problem** (otherwise the student just retypes the revealed answer)
  - it is the **same problem** in every way that matters, so the attempt is still evidence about
    the same skill (a sign-distribution twin still has a minus in front of a bracket)
  - its answer is **right**, verified through the same grader that will mark the student

Plus the boring but load-bearing one: `twin_of` returns None rather than raising, for OpenStax
items, for specs with nothing to move, and for anything the CAS rejects.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from engine.types import Item                              # noqa: E402
from pipeline import drill_tasks as dt                     # noqa: E402
from pipeline.twin import (                                # noqa: E402
    INT_RE,
    ITEMS_PATH,
    NUMERIC_FIELDS,
    skeleton,
    twin_of,
)
from services.grading import grade                         # noqa: E402

#: A minus sign in front of a bracket. This is the whole of alg.sign-distribution, and a twin that
#: loses it is a twin of a different skill.
NEGATIVE_BEFORE_BRACKET = re.compile(r"-\s*\(")


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bank() -> list[dict]:
    return json.loads(ITEMS_PATH.read_text())["items"]


@pytest.fixture(scope="module")
def generated(bank) -> list[dict]:
    """Items with a `check` spec. These are the only ones that can be twinned."""
    return [i for i in bank if i.get("check")]


@pytest.fixture(scope="module")
def openstax(bank) -> list[dict]:
    return [i for i in bank if i.get("source") == "openstax"]


@pytest.fixture(scope="module")
def twinned(generated) -> list[tuple[dict, dict]]:
    """(original, twin) for every generated item that twins at seed 0.

    Built once: the bank sweep is the expensive thing in this file and half the tests below want
    exactly the same pass over it.
    """
    pairs = [(i, twin_of(i, 0)) for i in generated]
    return [(i, t) for i, t in pairs if t is not None]


@pytest.fixture(scope="module")
def sign_items(generated) -> list[dict]:
    items = [i for i in generated if i["node_id"] == "alg.sign-distribution"]
    assert items, "no alg.sign-distribution drills in the bank"
    return items


def as_item(d: dict) -> Item:
    return Item(item_id=d["item_id"], node_id=d["node_id"], stem_latex=d["stem_latex"],
                answer_latex=d.get("answer_latex"), answer_sympy=d.get("answer_sympy"),
                answer_kind=d.get("answer_kind"), source=d.get("source", "generated"))


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------

def test_twin_is_deterministic_for_a_given_item_and_seed(generated):
    for item in generated[:40]:
        a, b = twin_of(item, 11), twin_of(item, 11)
        assert a == b, f"{item['item_id']} twinned differently on two identical calls"


def test_failure_is_deterministic_too(generated):
    """None is an answer, not an accident, and it must be the same answer every time."""
    failures = [i for i in generated if twin_of(i, 3) is None]
    assert failures, "no item fails to twin, so this test has stopped testing anything"
    for item in failures:
        assert twin_of(item, 3) is None


def test_different_seeds_generally_give_different_twins(sign_items):
    stems = {twin_of(sign_items[0], s)["stem_latex"] for s in range(12)}
    assert len(stems) > 1, "the seed does nothing"


# --------------------------------------------------------------------------------------
# A twin is a different problem
# --------------------------------------------------------------------------------------

def test_twin_stem_always_differs_from_the_original(twinned):
    for item, twin in twinned:
        assert twin["stem_latex"] != item["stem_latex"], f"{item['item_id']} twinned into itself"
        assert twin["check"]["params"] != item["check"]["params"]
    assert len(twinned) > 100, f"only {len(twinned)} twins produced; the sweep is not meaningful"


def test_twin_id_is_suffixed_and_points_back_at_the_original(sign_items):
    item = sign_items[0]
    twin = twin_of(item, 5)
    assert twin["item_id"] == f"{item['item_id']}-twin5"
    assert twin["twin_of"] == item["item_id"]


def test_twin_keeps_the_shape_of_an_item_dict(sign_items):
    item = sign_items[0]
    twin = twin_of(item, 0)
    assert set(item).issubset(set(twin))
    for key in ("node_id", "source", "difficulty_b", "answer_is_checkable"):
        if key in item:
            assert twin[key] == item[key]


def test_twin_answer_is_recomputed_not_copied(twinned):
    """The answer must come from the CAS applied to the new spec. If a twin ever carried the
    original's answer, hint rung 4 would be handing out a wrong answer key."""
    same = []
    for item, twin in twinned:
        stem, answer_latex, _ = dt.build(twin["check"]["task"], twin["check"]["params"])
        assert twin["stem_latex"] == stem
        assert twin["answer_latex"] == answer_latex
        if twin["answer_sympy"] == item["answer_sympy"]:
            same.append(item["item_id"])
    # A twin may legitimately land on the same answer (2x + 3 = 7 and 3x + 4 = 10 both give 2),
    # but it must be rare. If most twins share an answer, the perturbation is not perturbing.
    assert len(same) < 20, f"{len(same)} twins share the original's answer: {same[:10]}"


# --------------------------------------------------------------------------------------
# A twin is the same problem
# --------------------------------------------------------------------------------------

def test_only_digits_move(twinned):
    """The formal statement of isomorphic: every non-digit character survives byte for byte."""
    for item, twin in twinned:
        before, after = item["check"]["params"], twin["check"]["params"]
        assert before.keys() == after.keys()
        for field in NUMERIC_FIELDS:
            assert skeleton(before.get(field) or "") == skeleton(after.get(field) or ""), (
                f"{item['item_id']} changed the structure of {field}")
        assert before.get("var") == after.get("var"), f"{item['item_id']} renamed the variable"


def test_task_is_never_changed(twinned):
    for item, twin in twinned:
        assert twin["check"]["task"] == item["check"]["task"]
        assert twin["node_id"] == item["node_id"]


def test_sign_distribution_twins_keep_the_negative_before_the_bracket(sign_items):
    """The drilled property, over 25 seeds. This is the node the demo's headline diagnosis lands
    on, so a twin that quietly drops the bracket would be the worst possible failure here."""
    produced = 0
    for item in sign_items:
        assert NEGATIVE_BEFORE_BRACKET.search(item["check"]["params"]["expr"]), (
            f"{item['item_id']} is not a sign-distribution drill to begin with")
        for seed in range(25):
            twin = twin_of(item, seed)
            if twin is None:
                continue
            produced += 1
            expr = twin["check"]["params"]["expr"]
            assert NEGATIVE_BEFORE_BRACKET.search(expr), (
                f"{item['item_id']} seed {seed} lost the negative before the bracket: {expr}")
            assert NEGATIVE_BEFORE_BRACKET.search(twin["stem_latex"]), (
                f"{item['item_id']} seed {seed} rendered without it: {twin['stem_latex']}")
    assert produced >= 25 * len(sign_items) * 0.8, (
        f"only {produced} sign-distribution twins across 25 seeds; too few to trust the property")


def test_gradient_descent_twins_still_descend(generated):
    """A gd_step twin that climbs would be a drill teaching the opposite of its own node."""
    items = [i for i in generated if i["check"]["task"] == "gd_step"]
    assert items, "no gd_step drills in the bank"
    checked = 0
    for item in items:
        for seed in range(6):
            twin = twin_of(item, seed)
            if twin is None:
                continue
            checked += 1
            params = twin["check"]["params"]
            loss, w = dt.P(params["expr"]), dt.S(params.get("var", "w"))
            before = dt.P(params["at"])
            after = dt.load_answer(twin["answer_kind"], twin["answer_sympy"])
            assert float(loss.subs(w, after)) < float(loss.subs(w, before)), (
                f"{twin['item_id']} steps uphill")
    assert checked > 10


def test_learning_rates_stay_positive(generated):
    for item in generated:
        if not item["check"]["params"].get("lr"):
            continue
        for seed in range(8):
            twin = twin_of(item, seed)
            if twin is None:
                continue
            assert float(dt.P(twin["check"]["params"]["lr"])) > 0


def test_multi_character_variable_names_are_never_touched(generated):
    """`w1` and `w2` are names, not `w` times a number. A perturbation that reads their digits as
    literals differentiates a variable that no longer exists and every answer comes out zero."""
    items = [i for i in generated if "w1" in json.dumps(i["check"]["params"])]
    assert items, "no backprop drills with w1 in the bank"
    for item in items:
        for seed in range(5):
            twin = twin_of(item, seed)
            if twin is None:
                continue
            src = json.dumps(twin["check"]["params"])
            assert "w1" in src and "w2" in src, f"{twin['item_id']} mangled a variable name: {src}"


def test_the_literal_scanner_leaves_names_and_decimals_alone():
    assert INT_RE.findall("(w2*(w1*3))**2") == ["3", "2"]
    assert INT_RE.findall("0.05") == []
    assert INT_RE.findall("1/10") == ["1", "10"]
    assert INT_RE.findall("the target is 3.") == ["3"]
    assert skeleton("5*x - (3*x - 2)") == skeleton("7*x - (4*x - 11)")
    assert skeleton("5*x - (3*x - 2)") != skeleton("5*x + (3*x - 2)")


def test_context_prose_stays_true_to_the_expression(twinned):
    """The stem is prose plus mathematics. A twin that moves the numbers in one and not the other
    ships a question that contradicts itself."""
    pairs = [(i, t) for i, t in twinned if i["check"]["params"].get("context")]
    assert pairs
    for item, twin in pairs:
        params = twin["check"]["params"]
        context, expr = params["context"], params["expr"]
        if expr in context or expr.replace("**", "^") in context:
            continue                                   # the prose quotes the expression verbatim
        in_prose = {int(m.group()) for m in INT_RE.finditer(context)}
        in_maths = {int(m.group()) for f in NUMERIC_FIELDS
                    for m in INT_RE.finditer(str(params.get(f) or ""))}
        assert in_prose <= in_maths, (
            f"{twin['item_id']} prose mentions {sorted(in_prose - in_maths)}, which is nowhere in "
            f"its mathematics: {context!r}")


# --------------------------------------------------------------------------------------
# A twin's answer is right
# --------------------------------------------------------------------------------------

def test_every_twin_verifies_through_the_production_grader(twinned):
    """The one that matters. `services.grading.grade` is what will mark the student's attempt on
    the twin, so it is what has to agree with the twin's stored answer."""
    bad = []
    for _, twin in twinned:
        result = grade(as_item(twin), twin["answer_sympy"])
        if not result.correct:
            bad.append((twin["item_id"], twin["answer_kind"], twin["answer_sympy"], result.error))
    assert len(twinned) > 100
    assert not bad, f"{len(bad)} twins do not verify: {bad[:5]}"


def test_a_twin_answer_in_a_different_but_equivalent_form_still_grades(sign_items):
    twin = twin_of(sign_items[0], 0)
    expected = dt.load_answer(twin["answer_kind"], twin["answer_sympy"])
    assert grade(as_item(twin), str(dt.sp.expand(expected))).correct


def test_the_original_answer_does_not_grade_against_the_twin(twinned):
    """If the revealed answer still worked on the twin, the twin would prove nothing."""
    survived = 0
    for item, twin in twinned:
        if twin["answer_sympy"] == item["answer_sympy"]:
            continue
        if grade(as_item(twin), item["answer_sympy"]).correct:
            survived += 1
    assert survived == 0, f"{survived} twins accept the original item's answer"


# --------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------

def test_openstax_items_return_none(openstax):
    assert openstax, "no openstax items in the bank"
    for item in openstax[:60]:
        assert item.get("check") is None
        assert twin_of(item, 0) is None, f"{item['item_id']} is scraped; there is nothing to twin"


def test_openstax_item_returns_none_rather_than_raising():
    item = {"item_id": "os-3_3_e42", "node_id": "der.power-rule", "source": "openstax",
            "stem_latex": "Find the derivative of $f(x)=x^{5}$.", "answer_latex": "5x^{4}",
            "answer_sympy": None, "answer_kind": None, "check": None}
    assert twin_of(item, 0) is None


@pytest.mark.parametrize("item", [
    {},
    {"item_id": "x", "check": None},
    {"item_id": "x", "check": {}},
    {"item_id": "x", "check": {"task": "differentiate"}},
    {"item_id": "x", "check": {"task": "differentiate", "params": {}}},
    {"item_id": "x", "check": {"task": "no_such_task", "params": {"expr": "2*x"}}},
    {"item_id": "x", "check": {"task": "differentiate", "params": {"expr": "!!!"}}},
    {"item_id": "x", "check": {"task": "differentiate", "params": {"expr": "sin(x)"}}},
])
def test_unusable_specs_return_none_rather_than_raising(item):
    assert twin_of(item, 0) is None


def test_a_spec_with_no_movable_literal_returns_none():
    """`sin(x)**2 + cos(x)**2` is a Pythagorean identity drill whose only digits are exponents,
    and moving an exponent would stop it being that identity."""
    item = {"item_id": "gen-trig.identities-1", "node_id": "trig.identities", "source": "generated",
            "stem_latex": "", "check": {"task": "simplify",
                                        "params": {"expr": "sin(x)**2 + cos(x)**2",
                                                   "context": "Pythagorean identity."}}}
    assert twin_of(item, 0) is None


def test_the_whole_bank_can_be_swept_without_raising(bank):
    for item in bank:
        for seed in (0, 1):
            twin_of(item, seed)


def test_twin_rate_across_the_bank_stays_useful(generated, twinned):
    """Not a correctness property, a health check. Most refusals are specs with no number in them
    at all (`sin(x)`, `exp(x)`, `cos(pi)`), and those can never be twinned by any amount of
    cleverness. If this drops sharply, a guard has started rejecting good twins."""
    assert len(twinned) / len(generated) > 0.70, (
        f"only {len(twinned)}/{len(generated)} items twinned")
