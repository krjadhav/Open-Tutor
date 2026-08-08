"""Tests for engine/replay.py.

The properties tested here are the ones the event-log architecture is actually bought with. If
replay is not deterministic, not timestamp-ordered and not stoppable at a past instant, then
storing attempts instead of state buys nothing and we may as well mutate in place. So these are
less "unit tests for a helper" than "the guarantees learning-design.md section 12.1 promises".

Run: python3 -m pytest engine/tests/test_replay.py -q
"""

from __future__ import annotations

import dataclasses
import random
import sys
from pathlib import Path

import pytest

# Make `import engine...` work whether this is run as `python3 -m pytest` (cwd on the path) or as
# a bare `pytest` from somewhere else.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from engine.mastery import p_eff  # noqa: E402
from engine.replay import load_graph, replay, replay_to_frames  # noqa: E402
from engine.types import (  # noqa: E402
    LEARNING_FLOOR,
    PREREQ_READY,
    Attempt,
    Graph,
    add_days,
    utc,
)


# --------------------------------------------------------------------------- fixtures

def toy_graph() -> Graph:
    """A three-node slice: prereq -> node -> downstream, plus one island.

    `encompasses` is deliberately left empty. Implicit credit would push the prereq's `a` up on
    every correct attempt at `calc.power-rule`, which is realistic but would blur the one thing
    the blame test is measuring. Implicit credit is mastery.py's business and is tested there.
    """
    return Graph(
        nodes={
            "alg.sign-distribution": {
                "id": "alg.sign-distribution",
                "title": "Distributing a negative across a sum or difference",
                "kind": "skill",
                "difficulty_b": -1.2,
                "prereqs": [],
                "encompasses": [],
            },
            "calc.power-rule": {
                "id": "calc.power-rule",
                "title": "The power rule",
                "kind": "skill",
                "difficulty_b": -0.3,
                "prereqs": [{"id": "alg.sign-distribution", "weight": 0.9}],
                "encompasses": [],
            },
            "calc.chain-rule": {
                "id": "calc.chain-rule",
                "title": "The chain rule",
                "kind": "skill",
                "difficulty_b": 0.4,
                "prereqs": [{"id": "calc.power-rule", "weight": 1.0}],
                "encompasses": [],
            },
            "alg.exponent-rules": {
                "id": "alg.exponent-rules",
                "title": "Laws of exponents",
                "kind": "skill",
                "difficulty_b": -1.0,
                "prereqs": [],
                "encompasses": [],
            },
        },
        target_node="calc.chain-rule",
    )


def attempt(
    aid: str,
    node: str,
    correct: bool,
    ts,
    *,
    channel: str = "photo",
    hint_level: int = 0,
    blamed: str | None = None,
    confidence: float = 0.0,
    tag: str | None = None,
) -> Attempt:
    return Attempt(
        attempt_id=aid,
        student_id="stu-1",
        item_id=f"item-{aid}",
        node_id=node,
        ts=ts,
        correct=correct,
        hint_level=hint_level,
        channel=channel,
        blamed_node=blamed,
        blame_confidence=confidence,
        misconception_tag=tag,
    )


def sample_log() -> list[Attempt]:
    """A week of mixed activity across three nodes, spaced so reviews count as spaced."""
    day0 = utc(2026, 8, 1)
    return [
        attempt("a1", "alg.sign-distribution", True, day0),
        attempt("a2", "calc.power-rule", True, add_days(day0, 1)),
        attempt("a3", "calc.power-rule", False, add_days(day0, 2),
                blamed="alg.sign-distribution", confidence=0.9, tag="sign-flip"),
        attempt("a4", "alg.sign-distribution", True, add_days(day0, 3)),
        attempt("a5", "calc.power-rule", True, add_days(day0, 4)),
        attempt("a6", "calc.chain-rule", False, add_days(day0, 5), channel="typed"),
        attempt("a7", "calc.chain-rule", True, add_days(day0, 6), hint_level=2),
    ]


# --------------------------------------------------------------------------- determinism

def test_replay_is_deterministic():
    """Same log twice, same state. Without this the log is not a rebuildable source of truth."""
    graph = toy_graph()
    log = sample_log()

    first = replay(log, graph)
    second = replay(log, graph)

    assert first == second
    # And not vacuously: something actually moved off the prior.
    assert any(state.evidence > 0 for state in first.values())


def test_replay_does_not_mutate_its_inputs():
    """Replay is a fold, not an update. Callers keep passing the same log to it."""
    graph = toy_graph()
    log = sample_log()
    snapshot = list(log)

    replay(log, graph)

    assert log == snapshot
    assert list(graph.nodes) == list(toy_graph().nodes)


# --------------------------------- order independence, timestamp dependence

def test_replay_is_independent_of_input_list_order():
    """Rows come back from Postgres in whatever order the planner chose. That must not matter."""
    graph = toy_graph()
    log = sample_log()
    expected = replay(log, graph)

    rng = random.Random(20260807)
    for _ in range(10):
        shuffled = list(log)
        rng.shuffle(shuffled)
        assert replay(shuffled, graph) == expected


def test_replay_is_order_independent_even_on_tied_timestamps():
    """Two attempts at the same instant must still fold in a single, defined order."""
    graph = toy_graph()
    same_ts = utc(2026, 8, 1)
    log = [
        attempt("t1", "calc.power-rule", True, same_ts),
        attempt("t2", "calc.power-rule", False, same_ts),
        attempt("t3", "alg.sign-distribution", True, same_ts),
    ]

    expected = replay(log, graph)
    for permutation in ([log[2], log[0], log[1]], [log[1], log[2], log[0]], list(reversed(log))):
        assert replay(permutation, graph) == expected


def test_replay_does_depend_on_timestamps():
    """Timestamps are not decoration: spacing drives consolidation and decay.

    Same attempts, same order, different spacing (one day apart versus all inside one hour) must
    give a different history, otherwise the spaced-repetition half of the model is not wired up.
    """
    graph = toy_graph()
    day0 = utc(2026, 8, 1)

    spaced = [
        attempt("s1", "calc.power-rule", True, day0),
        attempt("s2", "calc.power-rule", True, add_days(day0, 3)),
        attempt("s3", "calc.power-rule", True, add_days(day0, 9)),
    ]
    massed = [
        attempt("s1", "calc.power-rule", True, day0),
        attempt("s2", "calc.power-rule", True, add_days(day0, 1.0 / 48)),
        attempt("s3", "calc.power-rule", True, add_days(day0, 2.0 / 48)),
    ]

    spaced_states = replay(spaced, graph)
    massed_states = replay(massed, graph)

    assert spaced_states != massed_states
    node = "calc.power-rule"
    # Same evidence either way, so p is unchanged; what moved is the memory model.
    assert spaced_states[node].p == pytest.approx(massed_states[node].p)
    assert spaced_states[node].stability > massed_states[node].stability
    assert spaced_states[node].last_seen != massed_states[node].last_seen


def test_swapping_two_timestamps_changes_the_result():
    """Reordering events in TIME is a different history, even with the same multiset of events.

    A failure followed by a clean success is a student who recovered; a clean success followed by
    a failure is a student who regressed. The Beta counts alone cannot tell those apart, which is
    exactly why `successes` and the open-misconception list exist.
    """
    graph = toy_graph()
    node = "calc.power-rule"
    day0 = utc(2026, 8, 1)

    regressed = [
        attempt("x1", node, True, day0),
        attempt("x2", node, True, add_days(day0, 5)),
        attempt("x3", node, False, add_days(day0, 10), blamed=node, confidence=1.0,
                tag="dropped-exponent"),
    ]
    # Same three events; the failure and the second success swap timestamps.
    recovered = [
        regressed[0],
        dataclasses.replace(regressed[2], ts=add_days(day0, 5)),
        dataclasses.replace(regressed[1], ts=add_days(day0, 10)),
    ]

    end_regressed = replay(regressed, graph)
    end_recovered = replay(recovered, graph)

    assert end_regressed != end_recovered
    assert end_regressed[node].p == pytest.approx(end_recovered[node].p)   # same evidence
    assert end_regressed[node].successes == 0                             # ended on a failure
    assert end_recovered[node].successes >= 1                             # ended on a success
    assert end_regressed[node].misconceptions == ("dropped-exponent",)
    assert end_recovered[node].misconceptions == ()                       # cleared by the success


# --------------------------------------------------------------------------- upto

def test_upto_excludes_later_attempts():
    graph = toy_graph()
    log = sample_log()
    cutoff = utc(2026, 8, 3, 23)          # covers a1, a2, a3 only

    truncated = replay(log, graph, upto=cutoff)
    equivalent = replay([a for a in log if a.ts <= cutoff], graph)

    assert truncated == equivalent
    assert truncated != replay(log, graph)
    # a4 (a correct attempt on the prereq, on day 3) has not happened yet at the cutoff.
    assert truncated["alg.sign-distribution"] != replay(log, graph)["alg.sign-distribution"]


def test_upto_is_inclusive_of_its_own_instant():
    graph = toy_graph()
    log = sample_log()
    at_a2 = log[1].ts

    states = replay(log, graph, upto=at_a2)

    assert states["calc.power-rule"].evidence > 0        # a2 counted
    assert states["calc.chain-rule"].evidence == 0       # nothing later did


def test_upto_before_everything_gives_fresh_priors():
    graph = toy_graph()
    states = replay(sample_log(), graph, upto=utc(2020, 1, 1))

    assert set(states) == set(graph.nodes)
    assert all(s.a == 1.0 and s.b == 1.0 and s.last_seen is None for s in states.values())


def test_upto_after_everything_is_the_full_replay():
    graph = toy_graph()
    log = sample_log()
    assert replay(log, graph, upto=utc(2030, 1, 1)) == replay(log, graph)


# --------------------------------------------------------------------------- coverage

def test_every_graph_node_appears_with_a_fresh_prior_if_untouched():
    graph = toy_graph()
    states = replay(sample_log(), graph)

    assert set(states) == set(graph.nodes)

    untouched = states["alg.exponent-rules"]             # no attempt in sample_log touches it
    assert untouched.a == 1.0 and untouched.b == 1.0
    assert untouched.p == pytest.approx(0.5)
    assert untouched.last_seen is None
    assert untouched.successes == 0
    # An unmeasured prereq must read as NOT ready, so it locks rather than unlocks dependants.
    assert untouched.p < PREREQ_READY


def test_empty_log_still_covers_the_graph():
    graph = toy_graph()
    states = replay([], graph)
    assert set(states) == set(graph.nodes)
    assert all(s.evidence == 0.0 for s in states.values())


def test_load_graph_reads_the_real_slice():
    graph = load_graph()
    assert len(graph.nodes) >= 30
    assert graph.target_node
    assert graph.target_node in graph.nodes
    # Every prereq and encompassing must point at a node that exists, or replay silently invents
    # state for an id nothing else knows about.
    for node_id in graph.nodes:
        for prereq_id, _w in graph.prereqs(node_id):
            assert prereq_id in graph.nodes, f"{node_id} requires unknown prereq {prereq_id}"
        for child_id, _c in graph.encompasses(node_id):
            assert child_id in graph.nodes, f"{node_id} encompasses unknown node {child_id}"


def test_replay_over_the_real_graph_covers_every_node():
    graph = load_graph()
    log = [attempt("r1", graph.target_node, True, utc(2026, 8, 1))]
    states = replay(log, graph)
    assert set(states) == set(graph.nodes)


# --------------------------------------------------------------------------- frames

def test_replay_to_frames_returns_one_frame_per_day():
    graph = toy_graph()
    frames = replay_to_frames(sample_log(), graph, days=7)
    assert len(frames) == 7


def test_frames_are_monotonic_in_time():
    graph = toy_graph()
    frames = replay_to_frames(sample_log(), graph, days=10)
    stamps = [ts for ts, _ in frames]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)              # strictly increasing, one per day
    for earlier, later in zip(stamps, stamps[1:]):
        assert (later - earlier).days == 1


def test_frames_progress_and_never_lose_evidence():
    """Evidence is cumulative: a later frame has seen everything an earlier one had.

    p can fall (that is what blame is for) but the total weight of evidence cannot.
    """
    graph = toy_graph()
    frames = replay_to_frames(sample_log(), graph, days=7)

    for (_, earlier), (_, later) in zip(frames, frames[1:]):
        for node_id in graph.nodes:
            assert later[node_id].evidence >= earlier[node_id].evidence - 1e-9


def test_final_frame_matches_replay_when_the_window_covers_the_log():
    graph = toy_graph()
    log = sample_log()
    frames = replay_to_frames(log, graph, days=14)

    assert frames[-1][1] == replay(log, graph)
    # And each frame equals a replay stopped at that day's boundary. This is the property that
    # lets offline retuning compare two parameter sets day by day.
    for boundary, states in frames:
        assert states == replay(log, graph, upto=boundary)


def test_frames_cover_every_graph_node_and_do_not_alias():
    graph = toy_graph()
    frames = replay_to_frames(sample_log(), graph, days=7)

    for _, states in frames:
        assert set(states) == set(graph.nodes)

    # Distinct dict objects: a frame must be a snapshot, not a live view of the running fold.
    assert frames[0][1] is not frames[-1][1]
    assert frames[0][1] != frames[-1][1]


def test_frames_are_empty_without_an_anchor():
    graph = toy_graph()
    assert replay_to_frames([], graph, days=7) == []
    assert replay_to_frames(sample_log(), graph, days=0) == []


def test_a_quiet_day_repeats_the_previous_frame():
    graph = toy_graph()
    day0 = utc(2026, 8, 1)
    log = [
        attempt("q1", "calc.power-rule", True, day0),
        attempt("q2", "calc.power-rule", True, add_days(day0, 3)),
    ]
    frames = replay_to_frames(log, graph, days=4)

    assert frames[0][1] == frames[1][1] == frames[2][1]   # days 2 and 3 were quiet
    assert frames[3][1] != frames[2][1]


# --------------------------------------------------------------------------- realistic history

def test_blamed_prereq_failure_reopens_the_prereq_without_destroying_the_node():
    """The end-to-end story the product is pitched on.

    The student learns sign distribution, then power rule, gets three clean spaced successes on
    the power rule, and then fails a power-rule problem because they botched -(3x - 2). The
    diagnosis blames sign distribution.

    What must happen:
      - sign distribution reopens: p_eff drops back below PREREQ_READY, its review interval
        collapses, its misconception is recorded, and its spaced-success count resets.
      - the power rule survives: the student did understand it, so it must NOT be knocked back
        below the learning floor. "A student is never marked down on a topic they actually
        understood" is the claim; this test is what makes it true rather than a slogan.
    """
    graph = toy_graph()
    prereq = "alg.sign-distribution"
    node = "calc.power-rule"
    day0 = utc(2026, 8, 1)

    # Build the prereq up first, spaced so each success counts as a real retrieval. The last
    # prereq review sits just before the failure on purpose: without it the prereq would have
    # decayed below PREREQ_READY on its own and the test would be measuring forgetting, not blame.
    warmup = [
        attempt("p1", prereq, True, day0),
        attempt("p2", prereq, True, add_days(day0, 2)),
        attempt("p3", prereq, True, add_days(day0, 6)),
        attempt("p4", prereq, True, add_days(day0, 15)),
    ]
    # Three clean spaced successes on the node itself.
    build = [
        attempt("n1", node, True, add_days(day0, 7)),
        attempt("n2", node, True, add_days(day0, 9)),
        attempt("n3", node, True, add_days(day0, 12)),
    ]
    failure_ts = add_days(day0, 16)
    blamed_failure = attempt(
        "n4", node, False, failure_ts,
        blamed=prereq, confidence=1.0, tag="sign-flip-on-distribute",
    )

    before = replay(warmup + build, graph, upto=add_days(day0, 15.5))
    after = replay(warmup + build + [blamed_failure], graph)

    # The node did climb toward mastery on the way up.
    climb = [
        replay(warmup + build, graph, upto=add_days(day0, d))[node].p
        for d in (7.5, 9.5, 12.5)
    ]
    assert climb[0] < climb[1] < climb[2]
    assert before[node].p > LEARNING_FLOOR
    assert before[node].successes >= 3
    assert p_eff(before[prereq], failure_ts) >= PREREQ_READY   # prereq was satisfied

    # The prereq reopens.
    assert after[prereq].p < before[prereq].p
    assert p_eff(after[prereq], failure_ts) < PREREQ_READY
    assert after[prereq].stability < before[prereq].stability
    assert "sign-flip-on-distribute" in after[prereq].misconceptions
    assert after[prereq].successes == 0

    # The node itself is dented, not destroyed.
    assert after[node].p < before[node].p                     # some residual honesty
    assert after[node].p > LEARNING_FLOOR                     # but still learned
    assert after[node].p > before[node].p * 0.9
    assert after[node].misconceptions == ()

    # And the whole thing is still just a fold over the log.
    assert after == replay(list(reversed(warmup + build + [blamed_failure])), graph)


def test_the_same_failure_undiagnosed_hits_the_attempted_node_instead():
    """Contrast case: with no blame, the failure has nowhere to go but the node attempted."""
    graph = toy_graph()
    prereq = "alg.sign-distribution"
    node = "calc.power-rule"
    day0 = utc(2026, 8, 1)

    base = [
        attempt("b1", node, True, day0),
        attempt("b2", node, True, add_days(day0, 3)),
    ]
    fail_ts = add_days(day0, 6)

    diagnosed = replay(
        base + [attempt("b3", node, False, fail_ts, blamed=prereq, confidence=1.0)], graph)
    undiagnosed = replay(base + [attempt("b3", node, False, fail_ts)], graph)

    assert undiagnosed[node].p < diagnosed[node].p
    assert undiagnosed[prereq].p > diagnosed[prereq].p
