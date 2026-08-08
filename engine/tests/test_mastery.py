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
    STATUS_ORDER,
    STATUS_RANK,
    apply_attempt,
    apply_blame,
    apply_consolidation,
    apply_direct,
    apply_implicit,
    blame_delta,
    clear_misconception,
    credited_nodes,
    is_due,
    p_eff,
    retrievability,
    status,
    using,
)
from engine.types import (  # noqa: E402
    BLAME_MAX_B_DELTA,
    DUE_RETRIEVABILITY,
    LAMBDA_BLAME,
    LEARNING_FLOOR,
    LEGACY_CONFIG,
    MASTERED_MIN_SUCCESSES,
    MASTERED_P,
    PREREQ_READY,
    QUALITY_PHOTO,
    STABILITY_GROWTH,
    STABILITY_MAX_DAYS,
    STABILITY_MIN_DAYS,
    Attempt,
    Graph,
    MasteryConfig,
    NodeState,
    add_days,
    clamp_credit,
    utc,
)

T0 = utc(2026, 8, 1)

SINGLE_HOP = MasteryConfig(transitive_credit=False)
TRANSITIVE = MasteryConfig(transitive_credit=True)


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


@pytest.fixture
def deep_graph() -> Graph:
    """Four encompassing levels with two competing paths to the same node.

        top --0.5--> mid  --0.4--> low --0.5--> floor
        top --0.9--> side --0.2--> low

    Chosen so the arithmetic distinguishes the rules that could plausibly be used:
      max of products  -> low = max(0.5*0.4, 0.9*0.2) = 0.20   (what we implement)
      sum of products  -> low = 0.38                           (would amplify, forbidden)
      min along path   -> low = 0.4                            (would not attenuate)
    and so the LONGER path through the LARGER first weight (0.9) still loses, which is the point
    of multiplying rather than taking the strongest single edge.
    """
    return Graph(nodes={
        "top": {"id": "top", "prereqs": [],
                "encompasses": [{"id": "mid", "credit": 0.5}, {"id": "side", "credit": 0.9}]},
        "mid": {"id": "mid", "prereqs": [], "encompasses": [{"id": "low", "credit": 0.4}]},
        "side": {"id": "side", "prereqs": [], "encompasses": [{"id": "low", "credit": 0.2}]},
        "low": {"id": "low", "prereqs": [], "encompasses": [{"id": "floor", "credit": 0.5}]},
        "floor": {"id": "floor", "prereqs": [], "encompasses": []},
    })


def all_path_credits(graph: Graph, start: str) -> dict[str, list[tuple[float, float]]]:
    """Brute-force every simple encompassing path out of `start`.

    Returns `{node: [(product_along_path, smallest_weight_on_path), ...]}`. Deliberately a
    different algorithm from the one under test (exhaustive enumeration, no memo, no max), so a
    bug in `implied_credit` cannot hide behind a matching bug here.
    """
    found: dict[str, list[tuple[float, float]]] = {}

    def walk(node: str, product: float, smallest: float, path: tuple[str, ...]) -> None:
        for child, raw in graph.encompasses(node):
            if child in path:
                continue
            weight = clamp_credit(raw)
            if weight <= 0.0:
                continue
            found.setdefault(child, []).append((product * weight, min(smallest, weight)))
            walk(child, product * weight, min(smallest, weight), path + (child,))

    walk(start, 1.0, 1.0, (start,))
    return found


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


# ------------------------------------------------------ rule 2: transitive implicit credit

def test_implied_credit_reaches_a_grandchild_and_attenuates(deep_graph):
    """Credit multiplies along a path and takes the max across paths, per core_engine.md 2.2."""
    implied = deep_graph.implied_credit("top")
    assert implied == pytest.approx({
        "mid": 0.5,
        "side": 0.9,
        "low": 0.2,        # max(0.5*0.4, 0.9*0.2), NOT the sum 0.38 and NOT the min-weight 0.4
        "floor": 0.1,      # 0.2 * 0.5, four levels down
    })
    # Attenuation is the whole property: every hop can only shrink what it passes on.
    assert implied["floor"] < implied["low"] < implied["mid"]


def test_implied_credit_never_exceeds_the_smallest_weight_on_the_path(deep_graph, real_graph):
    """Composition attenuates and never amplifies.

    Checked against an independent exhaustive path enumeration, on the toy graph and on the real
    one, so this is a property of the graph we ship and not just of a fixture.
    """
    for graph in (deep_graph, real_graph):
        for node_id in graph.nodes:
            implied = graph.implied_credit(node_id)
            paths = all_path_credits(graph, node_id)
            paths.pop(node_id, None)
            assert set(implied) == set(paths), f"{node_id}: reached a different set of nodes"
            for target, options in paths.items():
                for product, smallest in options:
                    assert product <= smallest + 1e-12, (
                        f"{node_id} -> {target}: {product} exceeds the path's smallest weight "
                        f"{smallest}, so composition amplified")
                assert implied[target] == pytest.approx(max(p for p, _ in options))


def test_implied_credit_stays_in_the_open_unit_interval(deep_graph, real_graph):
    """Every credit is in (0, 1]: an edge that pays nothing is not an edge, and one that pays
    more than a full repetition would make a component skill worth more than the skill itself."""
    for graph in (deep_graph, real_graph):
        for node_id in graph.nodes:
            for target, value in graph.implied_credit(node_id).items():
                assert 0.0 < value <= 1.0, f"{node_id} -> {target} credited {value}"


def test_implied_credit_never_credits_the_node_itself(real_graph):
    for node_id in real_graph.nodes:
        assert node_id not in real_graph.implied_credit(node_id)


def test_the_walk_terminates_on_the_real_cycle_free_graph(real_graph):
    """The shipped encompassing graph is a DAG, and the walk over it halts and stays finite."""
    seen_edges = sum(len(real_graph.encompasses(n)) for n in real_graph.nodes)
    assert seen_edges > 0
    for node_id in real_graph.nodes:
        implied = real_graph.implied_credit(node_id)
        assert len(implied) < len(real_graph.nodes)


def test_the_walk_terminates_even_if_someone_authors_a_cycle():
    """Mutation guard. Acyclicity is an authoring axiom, not something this walk may ASSUME:
    a cycle introduced by a bad edit must produce a wrong-ish number, never an infinite loop."""
    cyclic = Graph(nodes={
        "a": {"id": "a", "encompasses": [{"id": "b", "credit": 1.0}]},
        "b": {"id": "b", "encompasses": [{"id": "a", "credit": 1.0},
                                         {"id": "c", "credit": 0.5}]},
        "c": {"id": "c", "encompasses": [{"id": "c", "credit": 1.0}]},   # self loop
    })
    implied = cyclic.implied_credit("a")
    assert implied == pytest.approx({"b": 1.0, "c": 0.5})
    assert "a" not in implied
    for value in implied.values():
        assert 0.0 < value <= 1.0


def test_weights_outside_the_axiom_are_clamped_so_composition_cannot_amplify():
    """Mutation guard for the (0, 1] axiom. A weight above 1 would make a two-hop path pay more
    than its first hop, which is the one thing transitive credit must never do."""
    assert clamp_credit(2.0) == 1.0
    assert clamp_credit(0.0) == 0.0
    assert clamp_credit(-1.0) == 0.0

    bad = Graph(nodes={
        "v": {"id": "v", "encompasses": [{"id": "u", "credit": 3.0}]},
        "u": {"id": "u", "encompasses": [{"id": "t", "credit": 5.0}]},
        "t": {"id": "t", "encompasses": []},
    })
    implied = bad.implied_credit("v")
    assert implied == pytest.approx({"u": 1.0, "t": 1.0})
    assert implied["t"] <= implied["u"], "a grandchild was paid more than its parent"


def test_the_credit_map_is_cached_but_callers_cannot_poison_it(deep_graph):
    first = deep_graph.implied_credit("top")
    first["low"] = 99.0
    assert deep_graph.implied_credit("top")["low"] == pytest.approx(0.2)
    assert deep_graph.implied_credit("top") is not deep_graph.implied_credit("top")


def test_credit_is_gated_on_used_nodes_at_the_first_hop_only(deep_graph):
    """The decision this feature turns on.

    `used_nodes` comes from reading the student's working, so it can only ever name the skills the
    working makes visible: the first hop. Filtering every hop on it would credit nothing
    transitively, because no grandchild is ever in the list. So the gate applies once, and below a
    confirmed first hop credit flows freely.
    """
    # "mid" was reported used; "side" was not. Only mid's subtree is credited.
    through_mid = credited_nodes(deep_graph, "top", ("mid",), TRANSITIVE)
    assert through_mid == pytest.approx({"mid": 0.5, "low": 0.2, "floor": 0.1})
    assert "side" not in through_mid

    # The other branch alone gives the other path's product, which is strictly worse for "low".
    through_side = credited_nodes(deep_graph, "top", ("side",), TRANSITIVE)
    assert through_side == pytest.approx({"side": 0.9, "low": 0.18, "floor": 0.09})

    # Both reported: max across paths, not sum.
    both = credited_nodes(deep_graph, "top", ("mid", "side"), TRANSITIVE)
    assert both["low"] == pytest.approx(0.2)

    # A node not in encompasses(top) at all is never a first hop, however loudly it is reported.
    assert credited_nodes(deep_graph, "top", ("low",), TRANSITIVE) == {}
    assert credited_nodes(deep_graph, "top", (), TRANSITIVE) == {}


def test_transitive_credit_is_a_superset_of_single_hop_and_only_below_used_nodes(real_graph):
    """Every existing credit is unchanged; the new ones all hang under a reported first hop."""
    for node_id in real_graph.nodes:
        used = tuple(c for c, _ in real_graph.encompasses(node_id))
        if not used:
            continue
        old = credited_nodes(real_graph, node_id, used, SINGLE_HOP)
        new = credited_nodes(real_graph, node_id, used, TRANSITIVE)
        assert set(old) <= set(new)
        for child, credit in old.items():
            assert new[child] == pytest.approx(credit), "a first-hop credit changed"
        reachable = set()
        for child in used:
            reachable |= {child} | set(real_graph.implied_credit(child))
        assert set(new) <= reachable, "credit reached a node no reported skill can reach"


def test_transitive_credit_on_the_real_graph_reaches_the_exponent_rules(real_graph):
    """The concrete case engine.md section 8 names: a quotient-rule solution exercises the power
    rule, and the power rule is exponent rules wearing a calculus hat."""
    assert credited_nodes(real_graph, "der.quotient-rule", ("der.power-rule",), TRANSITIVE) == (
        pytest.approx({"der.power-rule": 0.3, "alg.exponent-rules": 0.3 * 0.35}))
    # Single-hop stops dead one level down, which is the gap being closed.
    assert credited_nodes(real_graph, "der.quotient-rule", ("der.power-rule",), SINGLE_HOP) == (
        pytest.approx({"der.power-rule": 0.3}))


def test_transitive_credit_keeps_every_property_of_the_single_hop_rule(real_graph):
    """last_seen refreshed on credited nodes; successes and stability untouched; nothing else."""
    now = add_days(T0, 10)
    start = {
        "der.power-rule": known("der.power-rule", p=0.9, stability=8.0,
                                last_seen=T0, successes=3),
        "alg.exponent-rules": known("alg.exponent-rules", p=0.9, stability=8.0,
                                    last_seen=T0, successes=3),
    }
    out = apply_implicit(start, real_graph, "der.quotient-rule", QUALITY_PHOTO,
                         ("der.power-rule",), now, config=TRANSITIVE)

    for node_id, credit in (("der.power-rule", 0.3), ("alg.exponent-rules", 0.105)):
        after = out[node_id]
        assert after.a == pytest.approx(start[node_id].a + credit * QUALITY_PHOTO)
        assert after.b == pytest.approx(start[node_id].b)          # credit is never negative
        assert after.last_seen == now                              # schedule: kept warm
        assert after.successes == start[node_id].successes         # mastery gate: untouched
        assert after.stability == pytest.approx(start[node_id].stability)
    assert start["alg.exponent-rules"].last_seen == T0, "the caller's state was mutated"


def test_transitive_credit_alone_still_cannot_reach_mastered(real_graph):
    """The gate the whole rule is fenced by, restated for the transitive case: a grandchild that
    is only ever credited implicitly can climb p as high as it likes and stays out of mastered."""
    log = [
        make_attempt("der.quotient-rule", True, ts=add_days(T0, i * 2), channel="photo",
                     used_nodes=("der.power-rule",))
        for i in range(1, 121)
    ]
    with using(TRANSITIVE):
        out = replay(real_graph, log)
    grandchild = out["alg.exponent-rules"]
    assert grandchild.p >= MASTERED_P
    assert grandchild.successes == 0
    assert status("alg.exponent-rules", real_graph, out, add_days(T0, 241)) != "mastered"


def test_transitive_credit_is_deterministic_and_order_independent(real_graph):
    """Replay determinism is what makes any of this measurable, so it is asserted, not assumed."""
    attempt = make_attempt("der.definition", True, channel="photo",
                           used_nodes=("lim.indeterminate-factoring", "alg.sign-distribution"))
    reordered = make_attempt(
        "der.definition", True, channel="photo",
        used_nodes=("alg.sign-distribution", "lim.indeterminate-factoring"))
    with using(TRANSITIVE):
        first = apply_attempt({}, real_graph, attempt)
        assert apply_attempt({}, real_graph, attempt) == first
        assert apply_attempt({}, real_graph, reordered) == first
    assert len(first) > 4, "the transitive walk reached nothing, so this proves nothing"


def test_both_behaviours_stay_runnable_side_by_side(real_graph):
    """The old rule has to stay RUNNABLE, or "is the new one better" is not a question we can
    answer from the log. This is the guarantee scripts/tune/replay_compare.py rests on, and it is
    also why `transitive_credit` is a flag that currently ships OFF rather than deleted code."""
    attempt = make_attempt("der.quotient-rule", True, channel="photo",
                           used_nodes=("der.power-rule", "alg.sign-distribution"))
    new = apply_attempt({}, real_graph, attempt, config=TRANSITIVE)
    old = apply_attempt({}, real_graph, attempt, config=SINGLE_HOP)

    assert "alg.exponent-rules" in new
    assert "alg.exponent-rules" not in old
    assert old["der.power-rule"] == new["der.power-rule"]

    # The shipped default is single-hop, per the measurement recorded on MasteryConfig.
    assert not MasteryConfig().transitive_credit
    assert apply_attempt({}, real_graph, attempt) == old

    # And the context manager, which is how the config reaches code that does not take one.
    with using(TRANSITIVE):
        assert apply_attempt({}, real_graph, attempt) == new
    assert apply_attempt({}, real_graph, attempt) == old      # default restored


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


# ------------------------------------------------- rule 3: the one-boundary blame cap

def solo_graph() -> Graph:
    """One node, no prereqs, so `status` is decided purely by the node's own p and successes.
    That isolates the blame cap from the locked cascade, which it does not claim to control."""
    return Graph(nodes={"x": {"id": "x", "prereqs": [], "encompasses": []}})


def blame_sweep():
    """Reachable-ish (a, b, successes, confidence) combinations.

    `b` starts at 1 from the prior and only ever grows (`apply_attempt` even floors the
    blame-discount give-back at 1.0), so b >= 1 is the reachable region and the sweep says so.
    """
    for a in (1.0, 2.0, 3.0, 4.5, 9.0, 12.0, 20.0, 40.0):
        for b in (1.0, 1.5, 2.0, 3.5, 6.0):
            for successes in (0, MASTERED_MIN_SUCCESSES):
                for confidence in (0.0, 0.5, 0.93, 0.97, 1.0, 10.0):
                    yield a, b, successes, confidence


def test_one_blame_never_crosses_two_status_boundaries():
    """THE new property. `mastered` may fall to `learning` and `learning` to `frontier`; nothing
    may skip a rung. One diagnosis, at most one boundary, whatever the model claims to believe.

    This is the rule that replaces BLAME_MAX_B_DELTA = 2.0. A fixed ceiling cannot express it,
    because the damage one unit of b does depends entirely on how much evidence the node already
    carries: 1.4 is a scratch at 40 reps and a demolition at 3.
    """
    graph = solo_graph()
    for a, b, successes, confidence in blame_sweep():
        states = {"x": NodeState("x", a=a, b=b, successes=successes)}
        pre = status("x", graph, states, T0)
        out = apply_blame(states, graph, "x", confidence, None, T0)
        post = status("x", graph, out, T0)
        assert STATUS_RANK[post] >= STATUS_RANK[pre] - 1, (
            f"a={a} b={b} succ={successes} conf={confidence}: {pre} -> {post} skipped a rung")
        assert out["x"].b >= b, "blame must never reduce b"


def test_the_boundary_clamp_is_live_code_and_the_old_cap_would_violate_the_property():
    """Mutation test for the clamp.

    A node with b below the prior is not reachable through `apply_attempt`, which is exactly why
    it is the right probe: it isolates the clamp from the BLAME_MAX_B_DELTA backstop that happens
    to cover the reachable region. Under the old fixed cap this node falls mastered -> frontier,
    two rungs, on one diagnosis. Under the property it stops at learning.
    """
    graph = solo_graph()
    states = {"x": NodeState("x", a=2.76, b=0.24, successes=MASTERED_MIN_SUCCESSES)}
    assert status("x", graph, states, T0) == "mastered"

    old = apply_blame(states, graph, "x", 0.97, None, T0, config=LEGACY_CONFIG)
    assert status("x", graph, old, T0) == "frontier", "the old cap used to skip a rung here"

    new = apply_blame(states, graph, "x", 0.97, None, T0)
    assert status("x", graph, new, T0) == "learning"
    assert new["x"].p == pytest.approx(LEARNING_FLOOR)      # clamped to the boundary, not past it
    assert new["x"].b < old["x"].b

    detail = blame_delta(states, graph, "x", 0.97, T0)
    assert detail.bound_by == "boundary"
    assert detail.applied < detail.requested
    assert detail.pre_status == "mastered" and detail.floor_status == "learning"


def test_on_reachable_states_the_absolute_backstop_is_the_tighter_of_the_two():
    """Honest accounting, so nobody claims a win the numbers do not support.

    For any node the engine can actually produce, b >= 1, and `mastered` needs p >= 0.90, so
    a >= 9b. The one-boundary limit is then a/0.70 - a - b >= (27/7)b - b = 2.857b >= 2.857,
    which is always above BLAME_MAX_B_DELTA = 2.0. So on real histories the backstop still binds
    first and the property changes no number. What it changes is that the cap is now a rule with
    a reason, and it will keep holding if LAMBDA_BLAME is ever retuned upward.
    """
    graph = solo_graph()
    boundary_ever_bound = False
    for a, b, successes, confidence in blame_sweep():
        states = {"x": NodeState("x", a=a, b=b, successes=successes)}
        detail = blame_delta(states, graph, "x", confidence, T0)
        assert detail.applied == pytest.approx(min(detail.requested, BLAME_MAX_B_DELTA))
        if detail.bound_by == "boundary":
            boundary_ever_bound = True
    assert not boundary_ever_bound, (
        "the boundary now binds on a reachable state; re-read the arithmetic above")

    # And the arithmetic itself, stated directly rather than inferred from the sweep.
    mastered = {"x": NodeState("x", a=9.0, b=1.0, successes=MASTERED_MIN_SUCCESSES)}
    assert status("x", graph, mastered, T0) == "mastered"
    assert blame_delta(mastered, graph, "x", 1.0, T0).boundary == pytest.approx(9.0 / 0.7 - 10.0)
    assert blame_delta(mastered, graph, "x", 1.0, T0).boundary > BLAME_MAX_B_DELTA


def test_the_cap_only_starts_to_matter_if_lambda_blame_is_retuned_upward():
    """At LAMBDA_BLAME = 1.5 and the model's measured confidence (0.93 to 0.97), the requested
    increment is about 1.4 and NO cap engages. Both caps are guards on a future retune, not on
    today's behaviour, and this test pins the threshold at which that stops being true."""
    graph = solo_graph()
    states = {"x": NodeState("x", a=9.0, b=1.0, successes=MASTERED_MIN_SUCCESSES)}
    for confidence in (0.93, 0.97):
        detail = blame_delta(states, graph, "x", confidence, T0)
        assert detail.bound_by == "none"
        assert detail.applied == pytest.approx(LAMBDA_BLAME * confidence)
        assert detail.applied < BLAME_MAX_B_DELTA


def test_frontier_and_locked_nodes_have_no_rung_below_them_so_the_backstop_is_all_they_get():
    """The boundary rule cannot bound a node that is already at the bottom of the ladder, which
    is precisely why BLAME_MAX_B_DELTA survives as an absolute backstop."""
    graph = solo_graph()
    frontier = {"x": NodeState("x")}                      # p = 0.5
    assert status("x", graph, frontier, T0) == "frontier"
    detail = blame_delta(frontier, graph, "x", 10.0, T0)
    assert detail.boundary == math.inf
    assert detail.applied == pytest.approx(BLAME_MAX_B_DELTA)
    assert detail.bound_by == "ceiling"


def test_the_blame_cap_change_does_not_touch_the_blame_discount_guarantee(real_graph):
    """The load-bearing property, re-checked under BOTH behaviours.

    Whatever the cap does, a student who failed a quotient-rule problem on a sign error must not
    be marked down on the quotient rule: exactly BLAME_DISCOUNT_KEEP of the direct negative
    evidence survives on the attempted node, and its review interval is not collapsed.
    """
    states = {
        "der.quotient-rule": known("der.quotient-rule", p=0.80, evidence=10.0,
                                   stability=9.0, last_seen=T0),
        "alg.sign-distribution": known("alg.sign-distribution", p=0.85, evidence=8.0,
                                       stability=12.0, last_seen=T0, successes=3),
    }
    attempt = make_attempt(
        "der.quotient-rule", False, ts=add_days(T0, 5), channel="photo",
        blamed_node="alg.sign-distribution", blame_confidence=0.95,
        misconception_tag="drops-sign-on-second-term",
    )
    expected_b = states["der.quotient-rule"].b + BLAME_DISCOUNT_KEEP * QUALITY_PHOTO

    for config in (LEGACY_CONFIG, MasteryConfig(), None):
        out = apply_attempt(states, real_graph, attempt, config=config)
        assert out["der.quotient-rule"].b == pytest.approx(expected_b)
        assert out["der.quotient-rule"].stability == pytest.approx(9.0)
        assert states["der.quotient-rule"].p - out["der.quotient-rule"].p < 0.03


def test_the_status_ladder_is_the_one_the_engine_actually_derives():
    """STATUS_ORDER is the only place the four statuses are ranked, and `status` must only ever
    return one of them, or "one boundary" would be measured against a ladder with a missing rung.
    """
    assert STATUS_ORDER == ("locked", "frontier", "learning", "mastered")
    graph = solo_graph()
    produced = set()
    for a, b, successes, _confidence in blame_sweep():
        produced.add(status("x", graph, {"x": NodeState("x", a=a, b=b, successes=successes)}, T0))
    assert produced <= set(STATUS_ORDER)
    assert {"frontier", "learning", "mastered"} <= produced


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
