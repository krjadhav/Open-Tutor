"""Tests for the hint ladder and the misconception name table. No network.

Two things are being protected here, and both are things a student sees directly.

  1. **Every node has a complete ladder.** The Solve screen renders four accordion rows from
     `hints.json`. A node with three rungs is a dead row; a node with none is a Solve screen with
     no help at all on the one problem the student is stuck on.
  2. **A raw kebab-case tag never reaches a student.** `misconception_tag` is a free string in the
     diagnosis tool schema, so the model can invent tags we have not authored. The Blockers tab and
     the daily set chips must show a sentence either way.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.build_hints import (  # noqa: E402
    HAND_RUNGS_12,
    HAND_RUNGS_34,
    HINTS_PATH,
    MAX_WORDS,
    RUNGS_34_BY_TASK,
    RUNGS_PER_NODE,
    TWIN_OFFER,
    build,
    humanise_tag,
    load_misconceptions,
    load_nodes,
    misconception_name,
    misconception_node,
    misconception_short,
    node_tasks,
    validate,
)

DEMO_DIR = ROOT / "data" / "demo"


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_ids() -> list[str]:
    return load_nodes()


@pytest.fixture(scope="module")
def hints() -> dict:
    """The committed file, not a freshly built one. The app reads the file."""
    assert HINTS_PATH.exists(), f"{HINTS_PATH} is missing; run python3 pipeline/build_hints.py"
    return json.loads(HINTS_PATH.read_text())


@pytest.fixture(scope="module")
def table() -> dict:
    return load_misconceptions()


# --------------------------------------------------------------------------------------
# The ladder: shape
# --------------------------------------------------------------------------------------

def test_every_graph_node_has_a_ladder(hints, node_ids):
    missing = [n for n in node_ids if n not in hints]
    assert not missing, f"{len(missing)} of {len(node_ids)} nodes have no hint ladder: {missing}"


def test_no_ladder_for_a_node_that_does_not_exist(hints, node_ids):
    assert not [n for n in hints if n not in node_ids]


def test_every_node_has_exactly_four_rungs(hints, node_ids):
    wrong = {n: len(hints[n]["rungs"]) for n in node_ids if len(hints[n]["rungs"]) != RUNGS_PER_NODE}
    assert not wrong, f"nodes without exactly {RUNGS_PER_NODE} rungs: {wrong}"


def test_rung_two_is_marked_as_maths(hints, node_ids):
    """Rung 2 states the rule itself, and the UI renders is_math rungs in the serif face."""
    not_math = [n for n in node_ids if not hints[n]["rungs"][1]["is_math"]]
    assert not not_math, f"rung 2 is not marked is_math on: {not_math}"


def test_rung_one_is_not_marked_as_maths(hints, node_ids):
    """Rung 1 names the idea. If it is maths, it has already given away rung 2."""
    assert not [n for n in node_ids if hints[n]["rungs"][0]["is_math"]]


def test_every_rung_has_text_and_a_boolean_flag(hints):
    for node_id, entry in hints.items():
        for i, rung in enumerate(entry["rungs"], start=1):
            assert rung["text"].strip(), f"{node_id} rung {i} is empty"
            assert isinstance(rung["is_math"], bool), f"{node_id} rung {i} has no is_math flag"


def test_rungs_stay_short(hints):
    """A hint that has to be read twice is not a hint."""
    long = {f"{n} rung {i}": len(r["text"].split())
            for n, e in hints.items()
            for i, r in enumerate(e["rungs"], start=1)
            if len(r["text"].split()) > MAX_WORDS}
    assert not long, f"rungs over {MAX_WORDS} words: {long}"


def test_validate_rejects_a_short_ladder(node_ids):
    broken = {n: {"rungs": [{"text": "x", "is_math": False}]} for n in node_ids}
    with pytest.raises(AssertionError):
        validate(broken, node_ids)


def test_validate_rejects_a_ladder_whose_rule_rung_is_not_maths(node_ids):
    ok, _ = build()
    broken = json.loads(json.dumps(ok))
    broken[node_ids[0]]["rungs"][1]["is_math"] = False
    with pytest.raises(AssertionError):
        validate(broken, node_ids)


# --------------------------------------------------------------------------------------
# The ladder: content
# --------------------------------------------------------------------------------------

def test_committed_file_matches_the_builder(hints):
    """`hints.json` is generated. If they drift, the app ships copy nobody reviewed."""
    built, _ = build()
    assert hints == built, "run python3 pipeline/build_hints.py"


def test_rungs_one_and_two_are_hand_authored_for_every_node(node_ids):
    assert set(HAND_RUNGS_12) == set(node_ids)


def test_no_rung_reads_as_a_penalty(hints):
    """Taking a hint already costs evidence weight. It must not also cost dignity."""
    scolding = re.compile(
        r"\b(should have|obviously|clearly you|as (?:you were|we) told|you failed|"
        r"you got this wrong|simply remember|you forgot|as expected)\b", re.I)
    offenders = [(n, r["text"]) for n, e in hints.items() for r in e["rungs"]
                 if scolding.search(r["text"])]
    assert not offenders, f"hint copy reads as a penalty: {offenders}"


def test_rung_one_never_states_the_rule_rung_two_states(hints, node_ids):
    """Rung 1 names the idea and hands the decision back, so it carries no equals sign."""
    leaks = [n for n in node_ids if "=" in hints[n]["rungs"][0]["text"]]
    assert not leaks, f"rung 1 gives the rule away on: {leaks}"


def test_rung_four_offers_a_twin_exactly_where_a_twin_is_possible(hints, node_ids):
    """Rung 4 promises a twin. `twin_of` can only deliver one for items with a `check` spec."""
    tasks = node_tasks()
    for node_id in node_ids:
        offers = TWIN_OFFER in hints[node_id]["rungs"][3]["text"]
        twinnable = bool(tasks.get(node_id))
        assert offers == twinnable, (
            f"{node_id}: rung 4 offers a twin={offers} but the node has drills with a check "
            f"spec={twinnable}")


def test_openstax_only_nodes_have_hand_authored_rungs_three_and_four(node_ids):
    tasks = node_tasks()
    no_drills = {n for n in node_ids if not tasks.get(n)}
    assert set(HAND_RUNGS_34) == no_drills, (
        "the hand-authored rung 3 and 4 set must be exactly the nodes with no drill task to "
        "template from")


def test_every_drill_task_in_the_bank_has_a_rung_three_and_four_template():
    tasks = {t for counter in node_tasks().values() for t in counter}
    assert tasks <= set(RUNGS_34_BY_TASK), f"no template for: {tasks - set(RUNGS_34_BY_TASK)}"


def test_ladders_are_not_copy_pasted_between_nodes(hints):
    """Rungs 1 and 2 are node-level facts, so no two nodes may share them."""
    seen: dict[str, str] = {}
    for node_id, entry in hints.items():
        key = entry["rungs"][0]["text"] + "\x00" + entry["rungs"][1]["text"]
        assert key not in seen, f"{node_id} has the same rungs 1 and 2 as {seen[key]}"
        seen[key] = node_id


# --------------------------------------------------------------------------------------
# Misconception names
# --------------------------------------------------------------------------------------

def test_the_ten_tags_the_ui_spec_names_all_resolve(table):
    required = [
        "sign-distribution", "fraction-subtraction", "exponent-arithmetic",
        "missing-inner-derivative", "product-of-derivatives", "quotient-numerator-order",
        "indeterminate-treated-as-dne", "unit-circle-values",
        "partial-other-variable-not-held-constant", "gradient-ascent-instead-of-descent",
    ]
    missing = [t for t in required if t not in table]
    assert not missing, f"misconceptions.json is missing: {missing}"


def test_every_entry_is_complete_and_points_at_a_real_node(table, node_ids):
    for tag, entry in table.items():
        assert entry.get("name"), f"{tag} has no name"
        assert entry.get("short"), f"{tag} has no short form"
        assert entry.get("node_id") in node_ids, f"{tag} points at unknown node {entry.get('node_id')}"


def test_no_name_is_just_the_tag_wearing_a_hat(table):
    """The point of the table is that the tag is not shown. A name that is the de-kebabbed tag
    would pass every other test here while shipping exactly what we are trying to avoid."""
    for tag, entry in table.items():
        assert entry["name"].lower() != tag.replace("-", " ").lower(), (
            f"{tag} has no real name, only its own tag respelled")


def test_short_forms_fit_a_chip(table):
    """The chip reads "Blocker · signs", so the short form is lower case and a couple of words."""
    for tag, entry in table.items():
        short = entry["short"]
        assert short == short.lower(), f"{tag} short form is not lower case: {short!r}"
        assert len(short.split()) <= 3, f"{tag} short form is too long for a chip: {short!r}"


def test_every_graph_node_can_name_a_blocker(table, node_ids):
    """A blocker is always actionable (ui-spec section 3, Blockers), so every node needs at least
    one tag that can name it."""
    covered = {e["node_id"] for e in table.values()}
    assert covered == set(node_ids), f"nodes with no misconception tag: {set(node_ids) - covered}"


def test_known_tags_resolve_through_the_table(table):
    assert misconception_name("sign-distribution", table) == "Dropping negatives across brackets"
    assert misconception_short("sign-distribution", table) == "signs"
    assert misconception_node("sign-distribution", table) == "alg.sign-distribution"


@pytest.mark.parametrize("tag,expected", [
    ("sign-distribution", "Sign distribution"),
    ("missing_inner_derivative", "Missing inner derivative"),
    ("gradient-ascent", "Gradient ascent"),
    ("limit-dne", "Limit does not exist"),
])
def test_unknown_tags_get_a_readable_fallback(tag, expected):
    assert humanise_tag(tag) == expected


def test_an_unknown_tag_never_reaches_a_student_as_a_raw_string(table):
    name = misconception_name("some-tag-we-never-authored", table)
    assert name == "Some tag we never authored"
    assert "-" not in name and "_" not in name
    assert name[0].isupper()
    assert misconception_short("some-tag-we-never-authored", table) == "some tag we never authored"


def test_empty_and_missing_tags_still_produce_something_printable(table):
    for tag in (None, "", "   ", "---"):
        assert misconception_name(tag, table) == "Unclassified"


# --------------------------------------------------------------------------------------
# The tags actually in play
# --------------------------------------------------------------------------------------

def _walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _demo_tags() -> set[str]:
    tags: set[str] = set()
    for path in sorted(DEMO_DIR.glob("**/*.json")):
        for key, value in _walk(json.loads(path.read_text())):
            if key in ("misconception_tag", "misconception") and isinstance(value, str) and value:
                tags.add(value)
            if key == "misconceptions" and isinstance(value, list):
                tags |= {v for v in value if isinstance(v, str) and v}
    return tags


def test_every_misconception_tag_in_the_demo_data_resolves(table):
    """Anything shown on stage is served from data/demo/. A tag in there that is not in the table
    is a Blockers card that falls back at exactly the wrong moment."""
    unknown = sorted(t for t in _demo_tags() if t not in table)
    assert not unknown, f"demo data uses tags with no authored name: {unknown}"


def test_every_node_blamed_in_the_demo_data_has_a_tag_pointing_at_it(table):
    blamed = set()
    for path in sorted(DEMO_DIR.glob("**/*.json")):
        for key, value in _walk(json.loads(path.read_text())):
            if key in ("blamed_node", "expect_node") and isinstance(value, str) and value:
                blamed.add(value)
    covered = {e["node_id"] for e in table.values()}
    assert blamed, "no blamed nodes found in data/demo/, this test has stopped testing anything"
    assert blamed <= covered, f"blamed in the demo but unnameable: {sorted(blamed - covered)}"


def test_the_diagnosis_prompt_does_not_constrain_the_tag(table):
    """The reason the fallback exists.

    `misconception_tag` is a free string in the tool schema, unlike `blamed_node`, which is pinned
    to an enum of real node ids. So the model can and will invent tags. If this ever gains an
    enum, every value in it must resolve through the table instead.
    """
    from engine.types import Graph
    from services.diagnose import build_tool

    graph = Graph(nodes={n: {} for n in load_nodes()})
    field = build_tool(graph)["function"]["parameters"]["properties"]["misconception_tag"]
    enum = field.get("enum")
    if enum is None:
        assert humanise_tag("anything-at-all") == "Anything at all"
        return
    unknown = [v for v in enum if v and v not in table]
    assert not unknown, f"the diagnosis prompt offers tags with no authored name: {unknown}"
