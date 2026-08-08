"""Tests for the seeded demo history.

These are not tests of `scripts/seed_demo.py`'s plumbing. They are tests of the STATE the shipped
`data/demo/history.json` produces when it is replayed, because that state is what every screen in
the demo renders and there is no second chance to notice it drifted.

Two properties are worth naming, since both are easy to lose and neither fails loudly:

  - The state must be EARNED. Every assertion below runs against `replay(...)` output, never
    against a stored snapshot. If someone ever "fixes" a failing test by writing a state file,
    the event-log design (learning-design.md section 12.1) has been thrown away and the first
    retune will silently produce a different demo.
  - The history must be STABLE. Regenerating has to give a byte-identical file, or the demo the
    team rehearsed is not the demo that runs.

The tolerances are deliberately loose where the exact number does not matter (mastered count,
accuracy band) and exact where the demo's script depends on it (which blockers are open, how many
times each fired, quotient rule at the frontier).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.mastery import is_due, p_eff, status                            # noqa: E402
from engine.replay import load_graph, replay                                # noqa: E402
from engine.selection import compose_daily_set, load_items                  # noqa: E402
from engine.types import DAILY_SET_SIZE, PREREQ_READY                       # noqa: E402
from scripts import seed_demo                                               # noqa: E402

TAG_SIGNS = seed_demo.TAG_SIGNS
TAG_FRACTIONS = seed_demo.TAG_FRACTIONS
NODE_SIGNS = seed_demo.NODE_SIGNS
NODE_FRACTIONS = seed_demo.NODE_FRACTIONS
NODE_QUOTIENT = seed_demo.NODE_QUOTIENT


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def graph():
    return load_graph(seed_demo.GRAPH_PATH)


@pytest.fixture(scope="module")
def items():
    return load_items(seed_demo.ITEMS_PATH)


@pytest.fixture(scope="module")
def bank():
    return seed_demo._ItemBank(seed_demo.ITEMS_PATH)


@pytest.fixture(scope="module")
def history():
    return seed_demo.load_history()


@pytest.fixture(scope="module")
def rows(history):
    return history["attempts"]


@pytest.fixture(scope="module")
def now(history):
    return datetime.fromisoformat(history["anchor_today"])


@pytest.fixture(scope="module")
def states(rows, graph):
    return replay([seed_demo.attempt_from_row(r) for r in rows], graph)


# --------------------------------------------------------------------------- the file itself

def test_the_history_ships_with_its_provenance_and_anchor(history):
    """The API layer offsets this log onto the real today, so it has to know where it starts."""
    assert history["generated_by"] == "scripts/seed_demo.py"
    assert history["note"]
    assert history["student_id"] == seed_demo.STUDENT_ID
    assert datetime.fromisoformat(history["anchor_today"]) == seed_demo.ANCHOR_TODAY
    assert history["history_days"] == seed_demo.HISTORY_DAYS
    assert history["attempt_count"] == len(history["attempts"])


def test_the_history_is_deterministic(tmp_path):
    """Same script, same file, byte for byte. Two runs and the shipped copy must all agree.

    A history that drifted between runs would mean the rehearsed demo and the demo that runs are
    different demos, and the difference would only show up on stage.
    """
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    seed_demo.write_history(first)
    seed_demo.write_history(second)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == Path(seed_demo.HISTORY_PATH).read_bytes(), (
        "data/demo/history.json is stale; re-run `python3 scripts/seed_demo.py`")


def test_the_history_covers_five_days_and_stops_before_the_demo(rows, now):
    stamps = [datetime.fromisoformat(r["ts"]) for r in rows]
    assert stamps == sorted(stamps), "attempts must be stored in chronological order"
    assert stamps[-1] < now
    days = {s.date() for s in stamps}
    assert len(days) == seed_demo.HISTORY_DAYS
    assert max(days) == (now - timedelta(days=1)).date(), "the last study day is yesterday"


# --------------------------------------------------------------------------- references are real

def test_every_attempt_references_a_real_gradeable_item_and_a_real_node(rows, graph, bank):
    """An attempt against a missing or ungradeable item seeds the worst bug the system has: a
    student types the right answer and is told they are wrong. See the note in items.json."""
    for row in rows:
        item_id, node_id = row["item_id"], row["node_id"]
        assert item_id in bank.node_of, f"{row['attempt_id']}: unknown item {item_id}"
        assert item_id in bank.gradeable, f"{row['attempt_id']}: {item_id} is not gradeable"
        assert bank.node_of[item_id] == node_id, (
            f"{row['attempt_id']}: {item_id} is tagged to {bank.node_of[item_id]}, not {node_id}")
        assert node_id in graph.nodes, f"{row['attempt_id']}: unknown node {node_id}"
        for used in row["used_nodes"]:
            assert used in graph.nodes
        if row["blamed_node"] is not None:
            assert row["blamed_node"] in graph.nodes


def test_attempt_ids_are_unique_and_ordered(rows):
    ids = [r["attempt_id"] for r in rows]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids), "ids must sort the same way replay's (ts, attempt_id) tie-break does"


def test_the_demo_problem_is_not_spent_in_the_history(rows):
    """The student meets the stage problem for the first time on stage."""
    used = {r["item_id"] for r in rows}
    assert not (used & seed_demo.RESERVED_FOR_DEMO)


# --------------------------------------------------------------------------- the earned state

def test_the_student_has_roughly_nine_skills_mastered(states, graph, now):
    mastered = [n for n in graph.nodes if status(n, graph, states, now) == "mastered"]
    assert 8 <= len(mastered) <= 12, f"expected about 9 mastered, got {len(mastered)}: {mastered}"
    # The shape matters as much as the count: the roots and the limits stage are behind them, and
    # they have started on derivative rules. That is the story the Path tab tells.
    for node_id in ("alg.exponent-rules", "alg.factoring", "trig.unit-circle",
                    "lim.concept", "lim.direct-substitution", "lim.continuity",
                    "der.power-rule"):
        assert node_id in mastered, f"{node_id} should be mastered"


def test_the_quotient_rule_is_at_the_frontier(states, graph, now):
    """Frontier, not locked and not learning: the demo's live problem has to be legitimately
    reachable, and reachable means every prereq is genuinely above PREREQ_READY."""
    assert status(NODE_QUOTIENT, graph, states, now) == "frontier"
    for prereq_id, _weight in graph.prereqs(NODE_QUOTIENT):
        assert p_eff(states[prereq_id], now) >= PREREQ_READY, (
            f"{prereq_id} is below PREREQ_READY, so the quotient rule is locked, not frontier")


def test_exactly_two_blockers_are_open_and_they_are_the_named_ones(states, graph):
    open_blockers = {n: states[n].misconceptions for n in graph.nodes if states[n].misconceptions}
    assert set(open_blockers) == {NODE_SIGNS, NODE_FRACTIONS}, (
        f"the Blockers tab must show exactly two cards, got {sorted(open_blockers)}")
    assert open_blockers[NODE_SIGNS] == (TAG_SIGNS,)
    assert open_blockers[NODE_FRACTIONS] == (TAG_FRACTIONS,)


def test_the_blockers_carry_the_frequencies_the_card_claims(rows, now):
    counts = seed_demo.misconception_counts(rows)
    assert counts[(NODE_SIGNS, TAG_SIGNS)]["count"] == 4, "the card says 4 times this week"
    assert counts[(NODE_FRACTIONS, TAG_FRACTIONS)]["count"] == 2

    last_sign = datetime.fromisoformat(counts[(NODE_SIGNS, TAG_SIGNS)]["last"]["ts"])
    assert last_sign.date() == (now - timedelta(days=1)).date(), (
        "the demo says 'same mistake as yesterday', so the most recent slip is yesterday's")


def test_every_blamed_attempt_carries_the_students_own_line_and_its_repair(rows, bank):
    """The Blockers card strikes `failed_step` through and puts `corrected_step` under it, so a
    blamed attempt without them renders an empty card. The two lines must also differ, and the
    item must be the problem that working belongs to."""
    blamed = [r for r in rows if r["blamed_node"] is not None]
    assert blamed, "the demo needs diagnosed failures"
    for row in blamed:
        assert row["failed_step"], f"{row['attempt_id']} has no failed_step"
        assert row["corrected_step"], f"{row['attempt_id']} has no corrected_step"
        assert row["failed_step"] != row["corrected_step"]
        assert row["student_message"]
        assert not row["correct"]
        assert 0.0 < row["blame_confidence"] <= 1.0
        assert row["item_id"] in bank.gradeable


def test_accuracy_lands_in_a_believable_band(rows):
    """The You tab prints this number, so it has to be a student's number, not a demo's."""
    correct = sum(1 for r in rows if r["correct"])
    accuracy = correct / len(rows)
    assert 0.70 <= accuracy <= 0.85, f"accuracy {accuracy:.3f} is outside the believable band"


def test_there_are_real_due_reviews_waiting(states, graph, now):
    """The daily set has two review slots. If nothing is due they get redistributed and the demo
    shows a set with no reviews in it, which is a different product."""
    due = [n for n in graph.nodes if is_due(states[n], now)]
    assert len(due) >= 4, f"only {len(due)} nodes due; the review slots have nothing real to hold"


# --------------------------------------------------------------------------- today's set

def test_the_daily_set_is_full_and_leads_with_a_blocker(states, graph, items, now):
    entries = compose_daily_set(graph, states, items, now)
    assert len(entries) == DAILY_SET_SIZE

    blockers = [e for e in entries if e.slot == "blocker"]
    assert blockers, "today's set must contain at least one blocker item"
    assert entries[0].slot == "blocker", "blockers lead the set"

    blocked_nodes = {e.item.node_id for e in blockers}
    assert blocked_nodes <= {NODE_SIGNS, NODE_FRACTIONS}
    assert NODE_SIGNS in blocked_nodes, "sign distribution is the blocker the demo is about"

    for entry in entries:
        assert entry.reason, "every slot carries the reason the UI prints on its chip"
    assert len({e.item.item_id for e in entries}) == DAILY_SET_SIZE, "no repeats within a set"


def test_the_set_reaches_the_quotient_rule_on_the_demo_day(states, graph, items, now):
    """Not a hard requirement of the engine, but it is the demo: step 2 of the 90-second script
    taps a problem in Today's set and step 4 diagnoses it. If this ever fails, the set is still
    valid, but the demo has to launch the climax problem from another screen.
    """
    entries = compose_daily_set(graph, states, items, now)
    assert NODE_QUOTIENT in {e.item.node_id for e in entries}
