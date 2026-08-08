"""Tests for engine.mastery.

Run from the repo root: python3 -m pytest engine/tests/test_mastery.py -q

The blame-discounting test (`test_blame_on_prereq_spares_the_attempted_topic`) is the load-bearing
one. If it ever goes red the product claim is broken, not just the code.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.mastery import (  # noqa: E402
    BLAME_DISCOUNT_KEEP,
    apply_attempt,
    apply_blame,
    apply_consolidation,
    apply_direct,
    apply_implicit,
    clear_misconception,
    is_due,
    p_eff,
    retrievability,
    status,
)
from engine.types import (  # noqa: E402
    BLAME_MAX_B_DELTA,
    DUE_RETRIEVABILITY,
    LEARNING_FLOOR,
    MASTERED_MIN_SUCCESSES,
    MASTERED_P,
    PREREQ_READY,
    QUALITY_PHOTO,
    STABILITY_GROWTH,
    STABILITY_MAX_DAYS,
    STABILITY_MIN_DAYS,
    Attempt,
    Graph,
    NodeState,
    add_days,
    utc,
)

T0 = utc(2026, 8, 1)


# --------------------------------------------------------------------------- fixtures

def make_attempt(node_id: str, correct: bool, ts=T0, **kw) -> Attempt:
    """Attempt factory with sane defaults so each test states only what it cares about."""
    defaults = dict(
        attempt_id=f"att-{node_id}-{ts.isoformat()}-{int(correct)}",
        student_id="stu-1",
        item_id=f"item-{node_id}",
        node_id=node_id,
        ts=ts,
        correct=correct,
    )
    defaults.update(kw)
    return Attempt(**defaults)


@pytest.fixture(scope="module")
def real_graph() -> Graph:
    """The actual 36-node demo slice, not a toy fixture."""
    path = REPO_ROOT / "data" / "graph" / "nodes.json"
    data = json.loads(path.read_text())
    graph = Graph(
        nodes={n["id"]: n for n in data["nodes"]},
        target_node=data.get("target_node"),
    )
    assert len(graph.nodes) >= 36  # graph grows; coverage is asserted where it matters, "the demo slice is expected to have 36 nodes"
    return graph


@pytest.fixture
def toy_graph() -> Graph:
    """root -> child -> grandchild, with an encompasses edge for implicit credit."""
    return Graph(nodes={
        "root": {"id": "root", "title": "Root", "prereqs": [], "encompasses": []},
        "other": {"id": "other", "title": "Other", "prereqs": [], "encompasses": []},
        "child": {
            "id": "child", "title": "Child",
            "prereqs": [{"id": "root", "weight": 1.0}],
            "encompasses": [{"id": "root", "credit": 0.3}],
        },
        "grandchild": {
            "id": "grandchild", "title": "Grandchild",
            "prereqs": [{"id": "child", "weight": 1.0}],
            "encompasses": [],
        },
    })


def replay(graph: Graph, attempts, states: dict[str, NodeState] | None = None):
    """Local fold of an attempt log, so these tests exercise only engine.mastery.

    `engine/replay.py` owns the production version with timestamp ordering and frame emission;
    this is deliberately the dumbest possible loop over `apply_attempt`.
    """
    current: dict[str, NodeState] = dict(states or {})
    for attempt in attempts:
        current = apply_attempt(current, graph, attempt)
    return current


def known(node_id: str, p: float = 0.9, evidence: float = 10.0, **kw) -> NodeState:
    """A NodeState with the given p and total evidence weight, for setting up scenarios."""
    return NodeState(node_id=node_id, a=p * evidence, b=(1.0 - p) * evidence, **kw)


# --------------------------------------------------------------------------- retrievability

def test_retrievability_is_one_when_never_seen():
    assert retrievability(NodeState("x"), T0) == 1.0
    assert p_eff(NodeState("x"), T0) == pytest.approx(0.5)


def test_retrievability_decays_exponentially_with_stability():
    state = NodeState("x", stability=4.0, last_seen=T0)
    assert retrievability(state, T0) == pytest.approx(1.0)
    assert retrievability(state, add_days(T0, 4)) == pytest.approx(math.exp(-1.0))
    assert retrievability(state, add_days(T0, 8)) == pytest.approx(math.exp(-2.0))
    # A longer stability means slower decay at the same elapsed time.
    slow = NodeState("x", stability=40.0, last_seen=T0)
    assert retrievability(slow, add_days(T0, 4)) > retrievability(state, add_days(T0, 4))


def test_retrievability_never_exceeds_one_if_time_runs_backwards():
    state = NodeState("x", stability=4.0, last_seen=T0)
    assert retrievability(state, add_days(T0, -3)) == 1.0


def test_due_boundary():
    """R crosses DUE_RETRIEVABILITY at elapsed = -S * ln(0.85). Check both sides of it."""
    stability = 10.0
    boundary_days = -stability * math.log(DUE_RETRIEVABILITY)
    state = NodeState("x", stability=stability, last_seen=T0)

    just_before = add_days(T0, boundary_days * 0.99)
    just_after = add_days(T0, boundary_days * 1.01)

    assert retrievability(state, just_before) > DUE_RETRIEVABILITY
    assert not is_due(state, just_before)
    assert retrievability(state, just_after) < DUE_RETRIEVABILITY
    assert is_due(state, just_after)


def test_never_seen_node_is_not_due():
    """Unseen is not forgotten. It belongs to the new/frontier pipeline, not to review."""
    assert not is_due(NodeState("x"), T0)


def test_p_eff_is_p_times_retrievability():
    state = known("x", p=0.8, stability=2.0, last_seen=T0)
    later = add_days(T0, 2)
    assert p_eff(state, later) == pytest.approx(0.8 * math.exp(-1.0))


# --------------------------------------------------------------------------- status

def test_status_frontier_when_prereqs_ready_and_own_p_low(toy_graph):
    states = {"root": known("root", p=0.9)}
    assert status("child", toy_graph, states, T0) == "frontier"


def test_status_locked_when_a_prereq_is_weak(toy_graph):
    states = {"root": known("root", p=0.5)}
    assert status("child", toy_graph, states, T0) == "locked"


def test_status_locked_when_a_prereq_has_decayed(toy_graph):
    """A prereq that was strong but has not been seen in a long time locks its dependants."""
    states = {"root": known("root", p=0.95, stability=2.0, last_seen=T0)}
    assert status("child", toy_graph, states, T0) == "frontier"
    far_future = add_days(T0, 30)
    assert p_eff(states["root"], far_future) < 0.7
    assert status("child", toy_graph, states, far_future) == "locked"


def test_status_learning_and_mastered(toy_graph):
    states = {"root": known("root", p=0.9)}
    ready = {**states, "child": known("child", p=LEARNING_FLOOR + 0.05)}
    assert status("child", toy_graph, ready, T0) == "learning"

    # p high enough but not enough spaced successes yet: still learning, not mastered.
    almost = {**states, "child": known("child", p=MASTERED_P + 0.02,
                                       successes=MASTERED_MIN_SUCCESSES - 1)}
    assert status("child", toy_graph, almost, T0) == "learning"

    done = {**states, "child": known("child", p=MASTERED_P + 0.02,
                                     successes=MASTERED_MIN_SUCCESSES)}
    assert status("child", toy_graph, done, T0) == "mastered"


def test_node_with_no_prereqs_is_never_locked(toy_graph, real_graph):
    """The root layer must always be reachable, including for a brand new student."""
    empty: dict[str, NodeState] = {}
    assert status("root", toy_graph, empty, T0) == "frontier"

    roots = [nid for nid in real_graph.nodes if not real_graph.prereqs(nid)]
    assert roots, "the real graph must have at least one root node"
    for node_id in roots:
        assert status(node_id, real_graph, empty, T0) != "locked"


def test_locked_takes_precedence_over_own_mastery(toy_graph):
    """A node whose foundations have rotted is not safe to serve, whatever its own p says."""
    states = {
        "root": known("root", p=0.2),
        "child": known("child", p=0.99, successes=10),
    }
    assert status("child", toy_graph, states, T0) == "locked"


def test_real_graph_cold_start_locks_everything_downstream(real_graph):
    """With no evidence, p=0.5 on every prereq, so only the roots are open."""
    empty: dict[str, NodeState] = {}
    assert status("der.quotient-rule", real_graph, empty, T0) == "locked"
    assert status("ai.gradient-descent-step", real_graph, empty, T0) == "locked"
    assert status("alg.sign-distribution", real_graph, empty, T0) == "frontier"


def test_real_graph_unlocks_a_node_once_its_prereqs_are_ready(real_graph):
    prereq_ids = [p for p, _ in real_graph.prereqs("der.quotient-rule")]
    # Derived, not hardcoded. The exact prereq set is a curriculum decision that the graph audit
    # may revise; what must stay true is that the quotient rule depends on the two algebra skills
    # its failures actually route to, since that is what the demo's diagnosis relies on.
    assert {"alg.fraction-arithmetic", "alg.sign-distribution"} <= set(prereq_ids)
    states = {pid: known(pid, p=0.95) for pid in prereq_ids}
    assert status("der.quotient-rule", real_graph, states, T0) == "frontier"


# --------------------------------------------------------------------------- rule 1: direct

def test_correct_attempt_raises_p_and_incorrect_lowers_it(toy_graph):
    start = {"root": NodeState("root")}
    p0 = start["root"].p

    up = apply_attempt(start, toy_graph, make_attempt("root", True, channel="photo"))
    assert up["root"].p > p0

    down = apply_attempt(start, toy_graph, make_attempt("root", False, channel="photo"))
    assert down["root"].p < p0


def test_direct_evidence_weight_scales_the_update(toy_graph):
    """An MCQ must move mastery less than a photo of complete working."""
    start = {"root": NodeState("root")}
    mcq = apply_attempt(start, toy_graph, make_attempt("root", True, channel="mcq"))
    photo = apply_attempt(start, toy_graph, make_attempt("root", True, channel="photo"))
    assert start["root"].p < mcq["root"].p < photo["root"].p


def test_hints_weaken_the_evidence(toy_graph):
    start = {"root": NodeState("root")}
    clean = apply_attempt(start, toy_graph, make_attempt("root", True, channel="typed"))
    hinted = apply_attempt(
        start, toy_graph, make_attempt("root", True, channel="typed", hint_level=2)
    )
    assert hinted["root"].p < clean["root"].p


def test_apply_direct_stamps_last_seen_and_does_not_mutate_input():
    start = {"root": NodeState("root")}
    out = apply_direct(start, "root", True, 1.0, T0)
    assert out["root"].last_seen == T0
    assert out["root"].a == pytest.approx(2.0)
    assert out["root"].b == pytest.approx(1.0)
    # The caller's dict and state object are untouched.
    assert start["root"].last_seen is None
    assert start["root"].a == 1.0
    assert out is not start


def test_apply_direct_creates_state_for_an_unseen_node():
    out = apply_direct({}, "brand-new", True, 1.0, T0)
    assert out["brand-new"].a == pytest.approx(2.0)


# --------------------------------------------------------------------------- rule 2: implicit

def test_implicit_credit_needs_both_encompasses_and_used_nodes(toy_graph):
    """Credit reaches a node only if the graph says it could be used AND the diagnosis says it
    was. Either alone must do nothing."""
    start = {"root": NodeState("root"), "other": NodeState("other")}

    # In encompasses(child) and in used_nodes -> credited.
    hit = apply_implicit(start, toy_graph, "child", 1.0, ("root",), T0)
    assert hit["root"].a > start["root"].a

    # In encompasses(child) but not reported as used -> untouched.
    unused = apply_implicit(start, toy_graph, "child", 1.0, ("other",), T0)
    assert unused["root"].a == pytest.approx(start["root"].a)

    # Reported as used but not in encompasses(child) -> untouched.
    assert unused["other"].a == pytest.approx(start["other"].a)

    # No used_nodes at all -> nothing moves.
    assert apply_implicit(start, toy_graph, "child", 1.0, (), T0) == start


def test_implicit_credit_uses_the_graph_credit_weight(toy_graph):
    out = apply_implicit({}, toy_graph, "child", 1.0, ("root",), T0)
    assert out["root"].a == pytest.approx(1.0 + 0.3)


def test_implicit_credit_refreshes_the_schedule_but_not_the_mastery_gate(toy_graph):
    """An implicit rep keeps a node warm without carrying it toward mastered.

    last_seen advances, because the skill really was exercised. successes and stability do not,
    because it was never tested in isolation.
    """
    start = {"root": NodeState("root", successes=1, stability=4.0, last_seen=T0)}
    later = add_days(T0, 10)
    out = apply_implicit(start, toy_graph, "child", 1.0, ("root",), later)

    assert out["root"].last_seen == later
    assert out["root"].successes == 1
    assert out["root"].stability == pytest.approx(4.0)
    assert retrievability(out["root"], later) == pytest.approx(1.0)


def test_implicit_credit_through_apply_attempt_on_the_real_graph(real_graph):
    """A correct quotient-rule solution that used sign distribution credits it, and only it."""
    attempt = make_attempt(
        "der.quotient-rule", True, channel="photo",
        used_nodes=("alg.sign-distribution", "alg.factoring"),
    )
    out = apply_attempt({}, real_graph, attempt)

    # alg.sign-distribution is encompassed by der.quotient-rule (credit 0.35) and was used.
    assert out["alg.sign-distribution"].a == pytest.approx(1.0 + 0.35 * QUALITY_PHOTO)
    # der.power-rule is encompassed but was not reported as used.
    assert "der.power-rule" not in out
    # alg.factoring was used but is not encompassed by der.quotient-rule.
    assert "alg.factoring" not in out


def test_incorrect_attempt_gives_no_implicit_credit(real_graph):
    attempt = make_attempt(
        "der.quotient-rule", False, channel="photo",
        used_nodes=("alg.sign-distribution",),
    )
    out = apply_attempt({}, real_graph, attempt)
    assert "alg.sign-distribution" not in out


def test_practising_a_topic_never_locks_it_behind_a_prereq_it_exercises(real_graph):
    """Regression test for the silent lockout.

    A student does chain-rule problems every other day for six weeks. Every one of those
    solutions decomposes f(g(x)), so alg.function-composition is genuinely being practised the
    whole time. If implicit credit did not refresh last_seen, that prereq would decay as though
    untouched, its p_eff would fall under PREREQ_READY, and der.chain-rule would LOCK, shutting
    the student out of the exact topic they are working on. Nothing would error; the frontier
    would just quietly close.

    THIS TEST FAILS IF THE `last_seen=now` LINE IN `apply_implicit` IS REVERTED. The counterfactual
    assertion below spells out why: with the original last_seen, p_eff is far under the gate.
    """
    assert "alg.function-composition" in [p for p, _ in real_graph.prereqs("der.chain-rule")]
    assert "alg.function-composition" in [c for c, _ in real_graph.encompasses("der.chain-rule")]

    states = {
        # The prereq is mastered and on a healthy 6-day interval.
        "alg.function-composition": known("alg.function-composition", p=0.95, evidence=20.0,
                                          stability=6.0, last_seen=T0, successes=3),
        # Every OTHER prereq is held constant (never seen, so R stays 1) to isolate the variable.
        # Derived from the graph so that adding or removing a sibling prereq cannot silently
        # turn this regression test green for the wrong reason.
        **{pid: known(pid, p=0.95, evidence=20.0)
           for pid, _ in real_graph.prereqs("der.chain-rule")
           if pid != "alg.function-composition"},
        "der.chain-rule": NodeState("der.chain-rule"),
    }
    assert status("der.chain-rule", real_graph, states, T0) == "frontier"

    log = [
        make_attempt("der.chain-rule", True, ts=add_days(T0, i * 2), channel="photo",
                     used_nodes=("alg.function-composition",))
        for i in range(1, 22)
    ]
    out = replay(real_graph, log, states)
    now = add_days(T0, 21 * 2 + 1)

    assert status("der.chain-rule", real_graph, out, now) != "locked"
    assert p_eff(out["alg.function-composition"], now) >= PREREQ_READY

    # Counterfactual: the same state with last_seen never refreshed is deep under the gate, so
    # this test is passing because of the refresh and nothing else.
    stale = out["alg.function-composition"].with_(last_seen=T0)
    assert p_eff(stale, now) < PREREQ_READY
    assert status("der.chain-rule", real_graph, {**out, "alg.function-composition": stale},
                  now) == "locked"


def test_implicit_credit_alone_can_never_reach_mastered(real_graph):
    """Keeping a node warm is not the same as proving you own it.

    Implicit credit can push p as high as it likes; without a spaced retrieval of the skill
    ITSELF, successes stays 0 and the mastered gate stays shut.
    """
    log = [
        make_attempt("der.chain-rule", True, ts=add_days(T0, i * 2), channel="photo",
                     used_nodes=("alg.function-composition",))
        for i in range(1, 61)
    ]
    out = replay(real_graph, log)
    now = add_days(T0, 61 * 2)

    credited = out["alg.function-composition"]
    assert credited.p >= MASTERED_P            # mastery-level confidence from credit alone
    assert credited.successes == 0             # but not one demonstration of the skill itself
    # alg.function-composition has no prereqs, so "locked" is off the table; it must read as
    # learning, never mastered.
    assert status("alg.function-composition", real_graph, out, now) == "learning"

    # One explicit spaced success is still not enough; the gate needs MASTERED_MIN_SUCCESSES.
    explicit = apply_attempt(out, real_graph,
                             make_attempt("alg.function-composition", True,
                                          ts=now, channel="photo"))
    assert explicit["alg.function-composition"].successes == 1
    assert status("alg.function-composition", real_graph, explicit, now) == "learning"


# --------------------------------------------------------------------------- rule 3: blame

def test_blame_on_prereq_spares_the_attempted_topic(real_graph):
    """THE test. A student fails a quotient-rule problem because they botched -(3x - 2).

    The quotient rule is not what they got wrong. Marking them down on it would send them to
    re-practise a rule they already understand while the real gap stays invisible. So
    der.quotient-rule must come out nearly unchanged and alg.sign-distribution must take the hit.
    """
    states = {
        "der.quotient-rule": known("der.quotient-rule", p=0.80, evidence=10.0),
        "alg.sign-distribution": known("alg.sign-distribution", p=0.85, evidence=8.0,
                                       stability=12.0, last_seen=T0, successes=3),
    }
    p_topic_before = states["der.quotient-rule"].p
    p_blamed_before = states["alg.sign-distribution"].p

    attempt = make_attempt(
        "der.quotient-rule", False, ts=add_days(T0, 5), channel="photo",
        blamed_node="alg.sign-distribution", blame_confidence=0.9,
        misconception_tag="drops-sign-on-second-term",
    )
    out = apply_attempt(states, real_graph, attempt)

    topic_drop = p_topic_before - out["der.quotient-rule"].p
    blamed_drop = p_blamed_before - out["alg.sign-distribution"].p

    # The topic the student actually understood is barely touched.
    assert topic_drop < 0.03, f"quotient rule lost {topic_drop:.3f} of p, expected nearly nothing"
    # The true cause takes a real hit.
    assert blamed_drop > 0.10, f"sign distribution only lost {blamed_drop:.3f} of p"
    assert blamed_drop > 5 * topic_drop

    # Only BLAME_DISCOUNT_KEEP of the direct negative evidence survives on the topic.
    expected_b = states["der.quotient-rule"].b + BLAME_DISCOUNT_KEEP * QUALITY_PHOTO
    assert out["der.quotient-rule"].b == pytest.approx(expected_b)

    # The blamed node gets the misconception, a collapsed interval, and a reset success counter.
    assert out["alg.sign-distribution"].misconceptions == ("drops-sign-on-second-term",)
    assert out["alg.sign-distribution"].stability == pytest.approx(6.0)
    assert out["alg.sign-distribution"].successes == 0

    # The topic's own review interval is NOT collapsed: it was retrieved fine.
    assert out["der.quotient-rule"].stability == states["der.quotient-rule"].stability


def test_blame_on_the_attempted_node_itself_is_not_discounted(real_graph):
    """When the diagnosis blames the topic itself, the direct evidence stands at full strength."""
    states = {"der.quotient-rule": known("der.quotient-rule", p=0.80, evidence=10.0,
                                         stability=8.0, last_seen=T0)}
    attempt = make_attempt(
        "der.quotient-rule", False, ts=add_days(T0, 3), channel="photo",
        blamed_node="der.quotient-rule", blame_confidence=0.9,
        misconception_tag="swapped-numerator-order",
    )
    out = apply_attempt(states, real_graph, attempt)

    # Full direct b (1.0) plus the blame delta (1.35), no give-back.
    expected_b = states["der.quotient-rule"].b + QUALITY_PHOTO + min(1.5 * 0.9, BLAME_MAX_B_DELTA)
    assert out["der.quotient-rule"].b == pytest.approx(expected_b)
    assert out["der.quotient-rule"].p < states["der.quotient-rule"].p - 0.1
    # Halved exactly once, not twice, for one failure.
    assert out["der.quotient-rule"].stability == pytest.approx(4.0)


def test_blame_is_capped_however_confident_the_model_claims_to_be():
    """Experiment 2 finding 3: confidence is not separable, so it must not be able to run away."""
    start = {"x": NodeState("x")}
    wild = apply_blame(start, Graph(), "x", 10.0, None, T0)
    assert wild["x"].b - start["x"].b == pytest.approx(BLAME_MAX_B_DELTA)

    # And a confidence just past the cap point gives exactly the cap too.
    at_cap = apply_blame(start, Graph(), "x", BLAME_MAX_B_DELTA / 1.5, None, T0)
    assert at_cap["x"].b - start["x"].b == pytest.approx(BLAME_MAX_B_DELTA)

    # Below the cap it scales with confidence.
    low = apply_blame(start, Graph(), "x", 0.5, None, T0)
    assert low["x"].b - start["x"].b == pytest.approx(0.75)


def test_a_single_capped_blame_cannot_wipe_out_a_well_evidenced_node():
    strong = {"x": known("x", p=0.95, evidence=40.0)}
    out = apply_blame(strong, Graph(), "x", 10.0, "tag", T0)
    assert out["x"].p > 0.85


def test_blame_stability_collapse_respects_the_floor():
    start = {"x": NodeState("x", stability=STABILITY_MIN_DAYS)}
    out = apply_blame(start, Graph(), "x", 1.0, None, T0)
    assert out["x"].stability == pytest.approx(STABILITY_MIN_DAYS)


def test_blame_tags_are_deduplicated_and_ordered():
    states = {"x": NodeState("x")}
    states = apply_blame(states, Graph(), "x", 0.5, "sign-flip", T0)
    states = apply_blame(states, Graph(), "x", 0.5, "off-by-one", T0)
    states = apply_blame(states, Graph(), "x", 0.5, "sign-flip", T0)
    assert states["x"].misconceptions == ("sign-flip", "off-by-one")


def test_blame_without_a_tag_records_no_misconception():
    out = apply_blame({}, Graph(), "x", 0.5, None, T0)
    assert out["x"].misconceptions == ()


def test_undiagnosed_failure_falls_back_to_the_attempted_node(toy_graph):
    """No blamed_node means we cannot route the failure, so the topic takes it."""
    states = {"root": known("root", p=0.9, stability=8.0, last_seen=T0)}
    out = apply_attempt(states, toy_graph, make_attempt("root", False, ts=add_days(T0, 4)))
    assert out["root"].p < states["root"].p
    assert out["root"].stability == pytest.approx(4.0)


# ------------------------------------------------------------------ rule 4: consolidation

def test_stability_grows_on_a_spaced_success_and_halves_on_failure(toy_graph):
    first = apply_attempt({}, toy_graph, make_attempt("root", True, ts=T0))
    assert first["root"].stability == pytest.approx(1.0 * (1.0 + STABILITY_GROWTH))
    assert first["root"].successes == 1

    # Same day again: not a spaced retrieval, so no interval growth and no success counted.
    again = apply_attempt(first, toy_graph, make_attempt("root", True, ts=T0))
    assert again["root"].stability == pytest.approx(first["root"].stability)
    assert again["root"].successes == 1

    # Days later, the node is due, so this one counts.
    due_ts = add_days(T0, 3)
    assert is_due(again["root"], due_ts)
    spaced = apply_attempt(again, toy_graph, make_attempt("root", True, ts=due_ts))
    assert spaced["root"].stability == pytest.approx(
        first["root"].stability * (1.0 + STABILITY_GROWTH)
    )
    assert spaced["root"].successes == 2

    # A failure collapses it.
    failed = apply_attempt(spaced, toy_graph, make_attempt("root", False, ts=add_days(T0, 40)))
    assert failed["root"].stability == pytest.approx(spaced["root"].stability / 2.0)


def test_stability_never_leaves_its_bounds(toy_graph):
    # Ceiling: hammer it with spaced successes far into the future.
    states: dict[str, NodeState] = {}
    ts = T0
    for _ in range(40):
        ts = add_days(ts, 400)
        states = apply_attempt(states, toy_graph, make_attempt("root", True, ts=ts))
        assert STABILITY_MIN_DAYS <= states["root"].stability <= STABILITY_MAX_DAYS
    assert states["root"].stability == pytest.approx(STABILITY_MAX_DAYS)

    # Floor: hammer it with failures.
    for _ in range(20):
        ts = add_days(ts, 1)
        states = apply_attempt(states, toy_graph, make_attempt("root", False, ts=ts))
        assert states["root"].stability >= STABILITY_MIN_DAYS
    assert states["root"].stability == pytest.approx(STABILITY_MIN_DAYS)


def test_consolidation_success_requires_a_genuine_spaced_retrieval():
    fresh = {"x": NodeState("x", stability=4.0, last_seen=T0)}
    # Not due yet and was_due not forced: nothing grows.
    same_day = apply_consolidation(fresh, "x", True, T0)
    assert same_day["x"].stability == pytest.approx(4.0)
    assert same_day["x"].successes == 0

    # was_due passed explicitly (what apply_attempt does, since apply_direct already stamped
    # last_seen and destroyed the evidence).
    forced = apply_consolidation(fresh, "x", True, T0, was_due=True)
    assert forced["x"].stability == pytest.approx(4.0 * (1.0 + STABILITY_GROWTH))
    assert forced["x"].successes == 1


# --------------------------------------------------------------------------- misconceptions

def test_clean_correct_answer_clears_a_misconception(toy_graph):
    states = {"root": NodeState("root", misconceptions=("sign-flip", "off-by-one"))}
    out = apply_attempt(states, toy_graph, make_attempt("root", True, hint_level=0))
    assert out["root"].misconceptions == ("off-by-one",)


def test_a_hinted_correct_answer_does_not_clear_a_misconception(toy_graph):
    states = {"root": NodeState("root", misconceptions=("sign-flip",))}
    out = apply_attempt(states, toy_graph, make_attempt("root", True, hint_level=1))
    assert out["root"].misconceptions == ("sign-flip",)


def test_clearing_targets_the_named_tag_when_one_is_given(toy_graph):
    states = {"root": NodeState("root", misconceptions=("sign-flip", "off-by-one"))}
    out = apply_attempt(
        states, toy_graph,
        make_attempt("root", True, hint_level=0, misconception_tag="off-by-one"),
    )
    assert out["root"].misconceptions == ("sign-flip",)


def test_clear_misconception_on_a_clean_node_is_a_noop():
    states = {"root": NodeState("root")}
    assert clear_misconception(states, "root") == states
    assert clear_misconception(states, "unknown-node") == states


# --------------------------------------------------------------------------- purity, replay

def test_apply_attempt_never_mutates_its_input(real_graph):
    states = {
        "der.quotient-rule": known("der.quotient-rule", p=0.8),
        "alg.sign-distribution": known("alg.sign-distribution", p=0.85, last_seen=T0),
    }
    snapshot = {k: v for k, v in states.items()}
    attempt = make_attempt(
        "der.quotient-rule", False, channel="photo",
        blamed_node="alg.sign-distribution", blame_confidence=0.9,
        misconception_tag="sign-flip",
    )
    apply_attempt(states, real_graph, attempt)
    assert states == snapshot
    assert states["alg.sign-distribution"].misconceptions == ()


def test_apply_attempt_uses_attempt_ts_when_now_is_omitted(toy_graph):
    ts = add_days(T0, 9)
    out = apply_attempt({}, toy_graph, make_attempt("root", True, ts=ts))
    assert out["root"].last_seen == ts


def test_replay_is_deterministic(real_graph):
    """Same log twice from a fresh start must give byte-identical state. This is what makes
    retuning the constants measurable."""
    log = [
        make_attempt("alg.sign-distribution", True, ts=T0, channel="typed"),
        make_attempt("alg.fraction-arithmetic", True, ts=add_days(T0, 1), channel="photo"),
        make_attempt("der.power-rule", False, ts=add_days(T0, 2), channel="typed",
                     blamed_node="alg.exponent-rules", blame_confidence=0.8,
                     misconception_tag="negative-exponent"),
        make_attempt("der.quotient-rule", False, ts=add_days(T0, 3), channel="photo",
                     used_nodes=("alg.sign-distribution",),
                     blamed_node="alg.sign-distribution", blame_confidence=0.95,
                     misconception_tag="drops-sign"),
        make_attempt("alg.sign-distribution", True, ts=add_days(T0, 6), channel="photo",
                     hint_level=0),
        make_attempt("der.quotient-rule", True, ts=add_days(T0, 9), channel="photo",
                     used_nodes=("alg.sign-distribution", "der.power-rule")),
        make_attempt("der.quotient-rule", True, ts=add_days(T0, 20), channel="typed",
                     used_nodes=("alg.fraction-arithmetic",)),
    ]

    first = replay(real_graph, log)
    second = replay(real_graph, log)
    assert first == second
    assert first is not second

    # And the run is not trivially empty.
    assert set(first) >= {"alg.sign-distribution", "der.quotient-rule", "alg.exponent-rules"}
    # The clean unhinted success on day 6 retired the misconception blame put there on day 3.
    assert first["alg.sign-distribution"].misconceptions == ()
    # Reversing the order changes the outcome, so determinism is not a vacuous claim.
    assert replay(real_graph, list(reversed(log))) != first


def test_replay_can_carry_a_node_from_locked_to_mastered(real_graph):
    """End to end on the real graph: teach the prereqs, then the topic itself."""
    prereqs = [p for p, _ in real_graph.prereqs("alg.radicals")]
    assert prereqs == ["alg.exponent-rules"]

    assert status("alg.radicals", real_graph, {}, T0) == "locked"

    log = []
    ts = T0
    for i in range(8):
        ts = add_days(T0, i * 4)
        log.append(make_attempt("alg.exponent-rules", True, ts=ts, channel="photo"))
    states = replay(real_graph, log)
    now = add_days(ts, 1)
    assert status("alg.radicals", real_graph, states, now) == "frontier"

    # Practise the topic, interleaving prereq reviews. Without them the prereq decays and the
    # topic locks again, which is the system working as intended.
    log2 = []
    for i in range(1, 12):
        log2.append(make_attempt("alg.radicals", True, ts=add_days(ts, i * 3), channel="photo"))
        if i % 3 == 0:
            log2.append(
                make_attempt("alg.exponent-rules", True, ts=add_days(ts, i * 3 + 1),
                             channel="photo")
            )
    states = replay(real_graph, log2, states)
    final_now = add_days(ts, 11 * 3 + 2)
    assert states["alg.radicals"].successes >= MASTERED_MIN_SUCCESSES
    assert states["alg.radicals"].p >= MASTERED_P
    assert status("alg.radicals", real_graph, states, final_now) == "mastered"
