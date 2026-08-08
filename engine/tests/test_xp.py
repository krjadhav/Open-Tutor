"""Tests for engine.xp.

The load-bearing tests in this file are `test_grinding_is_worthless_versus_learning` and
`test_new_work_rate_is_paid_once_per_node_not_once_per_attempt`. Everything else is scaffolding
for them. If either anti-farming property regresses, the leaderboard becomes a cheating target and
the XP number stops meaning "you learned something".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import xp                                          # noqa: E402
from engine.types import (                                     # noqa: E402
    NOVELTY_NEW,
    NOVELTY_OVERPRACTICE,
    NOVELTY_REVIEW,
    QUALITY_XP_CLEAN,
    QUALITY_XP_HINTED,
    QUALITY_XP_REVEALED,
    XP_BASE,
    Attempt,
    Graph,
    utc,
)

NOW = utc(2026, 3, 1)

# alg.sign-distribution is a root; ai.gradient-descent-step is the course target and the deepest
# node in the slice. The exact depth is NOT hard-coded: the graph is still being edited, and a
# test that has to be renumbered every time an edge moves gets renumbered without being read.
# `expected_depth` below is an independent oracle, so the real number is still pinned exactly.
ROOT_NODE = "alg.sign-distribution"
DEEP_NODE = "ai.gradient-descent-step"


def real_graph() -> Graph:
    data = json.loads((ROOT / "data" / "graph" / "nodes.json").read_text(encoding="utf-8"))
    return Graph(nodes={n["id"]: n for n in data["nodes"]},
                 target_node=data.get("target_node"))


def expected_depth(graph: Graph, node_id: str) -> int:
    """Longest prereq chain, computed independently of engine.xp: no memo, no cycle guard, plain
    recursion. Only valid on a DAG, which the real graph is required to be."""
    prereqs = [p for p, _w in graph.prereqs(node_id) if p in graph.nodes]
    return 1 + max((expected_depth(graph, p) for p in prereqs), default=0)


DEEP_NODE_DEPTH = expected_depth(real_graph(), DEEP_NODE)


@pytest.fixture
def graph() -> Graph:
    xp.clear_depth_cache()
    return real_graph()


def attempt(node_id: str, correct: bool = True, hint_level: int = 0,
            channel: str = "typed") -> Attempt:
    return Attempt(attempt_id="a1", student_id="s1", item_id="i1", node_id=node_id,
                   ts=NOW, correct=correct, hint_level=hint_level, channel=channel)


def earn(graph: Graph, node_id: str, status_before: str, *, was_due: bool = False,
         hint_level: int = 0, correct: bool = True, priors: int = 0) -> int:
    """Readable wrapper. `priors` is prior_attempts_on_node, which defaults to a first attempt."""
    return xp.xp_for(attempt(node_id, correct=correct, hint_level=hint_level),
                     graph, status_before, was_due, prior_attempts_on_node=priors)


# --------------------------------------------------------------------------- node_depth

def test_root_nodes_are_depth_one(graph):
    assert xp.node_depth(graph, ROOT_NODE) == 1
    for node_id in graph.nodes:
        if not graph.prereqs(node_id):
            assert xp.node_depth(graph, node_id) == 1, node_id


def test_deep_node_depth_is_the_longest_chain_not_the_shortest(graph):
    assert xp.node_depth(graph, DEEP_NODE) == expected_depth(graph, DEEP_NODE)
    for node_id in graph.nodes:
        assert xp.node_depth(graph, node_id) == expected_depth(graph, node_id), node_id

    # The qualitative claims the XP design rests on, which no graph edit may quietly break:
    # the target is the deepest node in the slice, and it is far above the roots.
    depths = {n: xp.node_depth(graph, n) for n in graph.nodes}
    assert depths[DEEP_NODE] == max(depths.values())
    assert depths[DEEP_NODE] >= 5, "the target must stay several levels above the roots"
    assert depths[ROOT_NODE] == 1


def test_depth_is_one_more_than_its_deepest_prereq(graph):
    for node_id in graph.nodes:
        prereqs = [p for p, _w in graph.prereqs(node_id)]
        expected = 1 + max((xp.node_depth(graph, p) for p in prereqs), default=0)
        assert xp.node_depth(graph, node_id) == expected, node_id


def test_depth_is_memoised(graph):
    calls = {"n": 0}
    original = Graph.prereqs

    def counting_prereqs(self, node_id):
        calls["n"] += 1
        return original(self, node_id)

    Graph.prereqs = counting_prereqs
    try:
        xp.node_depth(graph, DEEP_NODE)
        first = calls["n"]
        assert first > 0, "the counter must actually be wired up"
        for _ in range(50):
            xp.node_depth(graph, DEEP_NODE)
        assert calls["n"] == first, "repeat lookups must hit the cache, not walk the graph again"
    finally:
        Graph.prereqs = original


def test_depth_terminates_on_a_malformed_cyclic_graph():
    """The graph is contractually a DAG. A bad import must still not hang the process."""
    cyclic = Graph(nodes={
        "a": {"id": "a", "prereqs": [{"id": "b"}]},
        "b": {"id": "b", "prereqs": [{"id": "a"}]},
        "c": {"id": "c", "prereqs": [{"id": "c"}]},
    })
    assert xp.node_depth(cyclic, "a") >= 1
    assert xp.node_depth(cyclic, "b") >= 1
    assert xp.node_depth(cyclic, "c") == 1


def test_depth_ignores_dangling_prereq_ids():
    g = Graph(nodes={"a": {"id": "a", "prereqs": [{"id": "does-not-exist"}]}})
    assert xp.node_depth(g, "a") == 1


# --------------------------------------------------------------------------- xp_for

def test_wrong_answers_earn_nothing(graph):
    for hint_level in (0, 2, 4):
        assert earn(graph, DEEP_NODE, "frontier", was_due=True,
                    hint_level=hint_level, correct=False) == 0


def test_xp_is_base_times_depth_times_novelty_times_quality(graph):
    got = xp.xp_for(attempt(DEEP_NODE), graph, status_before="frontier", was_due=False,
                    prior_attempts_on_node=0)
    assert got == round(XP_BASE * DEEP_NODE_DEPTH * NOVELTY_NEW * QUALITY_XP_CLEAN)
    assert got == 10 * DEEP_NODE_DEPTH * 3


def test_prior_attempts_on_node_is_required_and_keyword_only(graph):
    """A caller that forgets the count must fail loudly, not silently reopen the new-work farm."""
    with pytest.raises(TypeError):
        xp.xp_for(attempt(DEEP_NODE), graph, "frontier", False)
    with pytest.raises(TypeError):
        xp.xp_for(attempt(DEEP_NODE), graph, "frontier", False, 0)


def test_deeper_nodes_pay_more(graph):
    shallow = earn(graph, ROOT_NODE, "frontier")
    deep = earn(graph, DEEP_NODE, "frontier")
    assert deep == shallow * DEEP_NODE_DEPTH


@pytest.mark.parametrize("status_before,was_due,priors,novelty", [
    ("frontier", False, 0, NOVELTY_NEW),
    ("frontier", True, 0, NOVELTY_NEW),      # frontier wins even if the node also looks due
    ("frontier", False, 1, NOVELTY_REVIEW),  # second attempt on the same frontier node
    ("frontier", False, 40, NOVELTY_REVIEW),
    ("learning", True, 3, NOVELTY_REVIEW),
    ("mastered", True, 9, NOVELTY_REVIEW),
    ("learning", False, 3, NOVELTY_OVERPRACTICE),
    ("mastered", False, 9, NOVELTY_OVERPRACTICE),
])
def test_novelty_tiers(graph, status_before, was_due, priors, novelty):
    got = earn(graph, DEEP_NODE, status_before, was_due=was_due, priors=priors)
    assert got == round(XP_BASE * DEEP_NODE_DEPTH * novelty * QUALITY_XP_CLEAN)


@pytest.mark.parametrize("hint_level,quality", [
    (0, QUALITY_XP_CLEAN),
    (1, QUALITY_XP_HINTED),
    (2, QUALITY_XP_HINTED),
    (3, QUALITY_XP_HINTED),
    (4, QUALITY_XP_REVEALED),
])
def test_quality_tiers_follow_the_hint_ladder(graph, hint_level, quality):
    got = earn(graph, DEEP_NODE, "frontier", hint_level=hint_level)
    assert got == round(XP_BASE * DEEP_NODE_DEPTH * NOVELTY_NEW * quality)


def test_hints_cost_xp_but_never_zero_it(graph):
    """Hints are never blocked, so taking one must still be clearly better than not answering."""
    clean = earn(graph, DEEP_NODE, "frontier", hint_level=0)
    hinted = earn(graph, DEEP_NODE, "frontier", hint_level=2)
    revealed = earn(graph, DEEP_NODE, "frontier", hint_level=4)
    assert clean > hinted > revealed > 0


def test_xp_is_never_negative_and_survives_odd_inputs(graph):
    assert earn(graph, ROOT_NODE, "frontier", hint_level=-3) > 0
    assert earn(graph, ROOT_NODE, "mastered", hint_level=99, priors=9) >= 0
    assert earn(graph, ROOT_NODE, "frontier", priors=-5) == earn(graph, ROOT_NODE, "frontier")
    for status_before in ("locked", "frontier", "learning", "mastered", "nonsense"):
        for hint_level in range(0, 8):
            for priors in (0, 1, 100):
                for correct in (True, False):
                    got = earn(graph, ROOT_NODE, status_before, hint_level=hint_level,
                               priors=priors, correct=correct)
                    assert isinstance(got, int) and got >= 0


def test_unknown_node_still_scores_as_a_root(graph):
    """An item tagged to a node that is not in the loaded slice must not crash the XP call."""
    assert earn(graph, "not.in.graph", "frontier") == round(
        XP_BASE * 1 * NOVELTY_NEW * QUALITY_XP_CLEAN)


# --------------------------------------------------------------------------- anti-farming

def test_grinding_is_worthless_versus_learning(graph):
    """The property the whole XP design exists for.

    Same node, same clean answer: learning it the first time pays exactly 20x what re-grinding it
    pays once it is mastered and not due. That 20x is NOVELTY_NEW / NOVELTY_OVERPRACTICE and it is
    what stops a leaderboard from being a stamina contest.
    """
    learn = earn(graph, DEEP_NODE, "frontier")
    grind = earn(graph, DEEP_NODE, "mastered", priors=7)

    assert learn == round(XP_BASE * DEEP_NODE_DEPTH * NOVELTY_NEW)
    assert grind == round(XP_BASE * DEEP_NODE_DEPTH * NOVELTY_OVERPRACTICE)
    assert learn / grind == pytest.approx(20.0)
    assert learn / grind == pytest.approx(NOVELTY_NEW / NOVELTY_OVERPRACTICE)


def test_new_work_rate_is_paid_once_per_node_not_once_per_attempt(graph):
    """The farm that "status_before == frontier" alone would leave open.

    A node stays on the frontier until mastery crosses LEARNING_FLOOR, so a student could sit on
    one node answering item after item and collect the 3.0x new-work rate every time. The prior
    attempt count closes it: 3.0x lands once, then the rate drops to review for good.
    """
    n_attempts = 20
    earned = [earn(graph, DEEP_NODE, "frontier", priors=i) for i in range(n_attempts)]

    first, second = earned[0], earned[1]
    assert first / second == pytest.approx(NOVELTY_NEW / NOVELTY_REVIEW)
    assert first / second == pytest.approx(3.0)
    assert len(set(earned[1:])) == 1, "the rate must not keep changing after the first attempt"
    assert all(x == second for x in earned[1:])

    if_every_attempt_were_new = n_attempts * first
    assert sum(earned) < 0.4 * if_every_attempt_were_new
    assert sum(earned) == first + (n_attempts - 1) * second


def test_grinding_easy_roots_is_worth_almost_nothing(graph):
    """The realistic cheat: hammer the easiest node in the course over and over."""
    new_deep = earn(graph, DEEP_NODE, "frontier")
    grind_root = earn(graph, ROOT_NODE, "mastered", priors=7)

    assert grind_root <= 2, "a mastered root must round to a rounding error"
    # XP_BASE * depth * 3.0 against round(XP_BASE * 1 * 0.15), i.e. 30 * depth against 2.
    assert new_deep / grind_root == pytest.approx(15 * DEEP_NODE_DEPTH)
    assert new_deep / grind_root >= 100.0, "the deep/root gap must stay at least two orders wide"

    # Reps needed to match one genuinely new deep node.
    assert new_deep // max(grind_root, 1) >= 100

    # And a full day of grinding (say 100 reps) must not beat a single day of real learning.
    a_days_learning = 6 * earn(graph, "der.chain-rule", "frontier")
    assert 100 * grind_root < a_days_learning


def test_honest_review_beats_grinding_but_loses_to_new_work(graph):
    """The ratio is checked on a deep node because XP is rounded to an int: on a shallow node the
    over-practice term lands near 1 and rounding, not the formula, decides the ratio. The ordering
    still has to hold everywhere, so that is asserted on a shallow node too."""
    new = earn(graph, DEEP_NODE, "frontier")
    review = earn(graph, DEEP_NODE, "mastered", was_due=True, priors=7)
    grind = earn(graph, DEEP_NODE, "mastered", priors=7)
    assert new > review > grind > 0
    assert new / review == pytest.approx(NOVELTY_NEW / NOVELTY_REVIEW)
    assert review / grind == pytest.approx(NOVELTY_REVIEW / NOVELTY_OVERPRACTICE)

    shallow = "der.chain-rule"
    assert (earn(graph, shallow, "frontier")
            > earn(graph, shallow, "mastered", was_due=True, priors=7)
            > earn(graph, shallow, "mastered", priors=7)
            > 0)


def test_hint_mining_a_new_node_still_beats_grinding_a_mastered_one(graph):
    """Hint mining must not be the dominant strategy, but honest hinted learning must still beat
    dishonest grinding, otherwise students stop taking hints and start guessing."""
    revealed_new = earn(graph, ROOT_NODE, "frontier", hint_level=4)
    clean_grind = earn(graph, ROOT_NODE, "mastered", priors=9)
    assert revealed_new > clean_grind
