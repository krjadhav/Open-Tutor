"""Tests for engine.selection.

Two kinds of test live here on purpose:

  - Unit tests on toy graphs, where the Rasch arithmetic and the slot budget are pinned exactly.
    Nothing is mocked: difficulty targeting reads `state.p` directly, so a NodeState is all the
    control these need.
  - Integration tests that run the real graph and the real 376-item bank through
    `compose_daily_set`, because a selector that only works on toy data is not a selector.

Two behaviours here are easy to "fix" back into bugs, so both have a test named after the
intent rather than the mechanism:
  - `test_difficulty_targeting_uses_mastery_not_decayed_mastery` and
    `test_a_rusty_node_is_served_a_reviewable_item_not_a_beginner_item` (p_eff schedules, p targets)
  - `test_a_maximally_busy_day_still_carries_the_goal_link` (goal_link is reserved, not last)
"""

from __future__ import annotations

import json
import pathlib
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import selection                                    # noqa: E402
from engine.mastery import is_due, p_eff, status                # noqa: E402
from engine.types import (                                      # noqa: E402
    DAILY_SET_SIZE,
    NEW_SLOT_MAX,
    SET_BUDGET,
    TARGET_SUCCESS_RATE,
    Graph,
    Item,
    NodeState,
    SetEntry,
    add_days,
    utc,
)

NOW = utc(2026, 3, 1)


# --------------------------------------------------------------------------- fixtures

def item(item_id: str, node_id: str, b: float) -> Item:
    return Item(item_id=item_id, node_id=node_id, stem_latex=f"stem {item_id}", difficulty_b=b)


def bank(*items: Item) -> dict[str, list[Item]]:
    out: dict[str, list[Item]] = {}
    for i in items:
        out.setdefault(i.node_id, []).append(i)
    return out


def toy_graph() -> Graph:
    """r1 -> m1 -\
                  > d       plus an unrelated island node `iso`.
       r2 -> m2 -/
    """
    nodes = {
        "r1": {"id": "r1", "title": "Root one", "prereqs": []},
        "r2": {"id": "r2", "title": "Root two", "prereqs": []},
        "m1": {"id": "m1", "title": "Middle one", "prereqs": [{"id": "r1", "weight": 1.0}]},
        "m2": {"id": "m2", "title": "Middle two", "prereqs": [{"id": "r2", "weight": 1.0}]},
        "d": {"id": "d", "title": "Deep target",
              "prereqs": [{"id": "m1", "weight": 1.0}, {"id": "m2", "weight": 1.0}]},
        "iso": {"id": "iso", "title": "Island", "prereqs": []},
    }
    return Graph(nodes=nodes, target_node="d")


def chain_graph(n: int) -> Graph:
    """n0 -> n1 -> ... -> n(n-1). A pure path, so hop distance is |i - j| and interleaving has an
    unambiguous right answer."""
    nodes = {}
    for i in range(n):
        nodes[f"n{i}"] = {
            "id": f"n{i}",
            "title": f"Node {i}",
            "prereqs": ([{"id": f"n{i - 1}", "weight": 1.0}] if i else []),
        }
    return Graph(nodes=nodes, target_node=f"n{n - 1}")


def strong(node_id: str, last_seen=NOW) -> NodeState:
    """A node the student clearly knows and has just practised."""
    return NodeState(node_id=node_id, a=39.0, b=1.0, stability=10.0,
                     last_seen=last_seen, successes=5)


def real_graph() -> Graph:
    data = json.loads((ROOT / "data" / "graph" / "nodes.json").read_text(encoding="utf-8"))
    return Graph(nodes={n["id"]: n for n in data["nodes"]},
                 target_node=data.get("target_node"))


def ancestors_of(graph: Graph, node_id: str) -> set[str]:
    """Everything `node_id` transitively depends on, computed independently of engine.selection."""
    out: set[str] = set()
    stack = [node_id]
    while stack:
        for prereq_id, _w in graph.prereqs(stack.pop()):
            if prereq_id not in out:
                out.add(prereq_id)
                stack.append(prereq_id)
    return out


def descendants_of(graph: Graph, node_id: str) -> set[str]:
    return {n for n in graph.nodes if node_id in ancestors_of(graph, n)}


@pytest.fixture(scope="module")
def real_items() -> dict[str, list[Item]]:
    return selection.load_items()


# --------------------------------------------------------------------------- item bank

def test_load_items_reads_the_real_bank(real_items):
    total = sum(len(v) for v in real_items.values())
    raw = json.loads((pathlib.Path(__file__).resolve().parents[2] / "data/items/items.json").read_text())["items"]
    assert total == len(raw)
    assert all(isinstance(i, Item) for v in real_items.values() for i in v)
    assert all(i.node_id == node_id for node_id, v in real_items.items() for i in v)
    assert set(real_items) <= set(real_graph().nodes), "every item must be tagged to a real node"


def test_load_items_keeps_encompasses_as_a_tuple(real_items):
    withs = [i for v in real_items.values() for i in v if i.encompasses]
    assert withs, "expected some items to carry encompasses links"
    assert all(isinstance(i.encompasses, tuple) for i in withs)


# --------------------------------------------------------------------------- predicted_success

def test_predicted_success_is_monotone_in_mastery():
    it = item("i", "n", b=0.0)
    ps = [selection.predicted_success(NodeState("n", a=a, b=100.0 - a), it, NOW)
          for a in (5.0, 20.0, 50.0, 80.0, 95.0)]
    assert ps == sorted(ps)
    assert all(x < y for x, y in zip(ps, ps[1:])), "must be strictly increasing in mastery"


def test_predicted_success_is_inverse_in_difficulty():
    state = NodeState("n", a=9.0, b=1.0)
    ps = [selection.predicted_success(state, item(f"i{b}", "n", b=b), NOW)
          for b in (-3.0, -1.0, 0.0, 1.0, 3.0)]
    assert all(x > y for x, y in zip(ps, ps[1:])), "must be strictly decreasing in difficulty"


def test_predicted_success_matches_the_rasch_formula_exactly():
    state = NodeState("n", a=9.0, b=1.0)                     # p = 0.9
    got = selection.predicted_success(state, item("i", "n", b=0.5), NOW)
    theta = math.log(0.9 / 0.1)
    assert got == pytest.approx(1 / (1 + math.exp(-(theta - 0.5))), abs=1e-12)


@pytest.mark.parametrize("a,b", [(0.0, 1.0), (1.0, 0.0), (1.0, 1e18), (1e18, 1.0), (1e-9, 1e-9)])
def test_predicted_success_clamps_saturated_mastery_instead_of_blowing_up(a, b):
    state = NodeState("n", a=a, b=b)
    got = selection.predicted_success(state, item("i", "n", b=0.0), NOW)
    assert math.isfinite(got)
    assert 0.0 < got < 1.0


def test_difficulty_targeting_uses_mastery_not_decayed_mastery():
    """The distinction FIX 1 exists for, at the smallest possible scale.

    Two students with identical skill (p = 0.9), one of whom last practised six weeks ago. p_eff
    separates them by two orders of magnitude, and predicted_success must not care: the rusty
    student has the same skill, so they get the same item.
    """
    fresh = NodeState("n", a=9.0, b=1.0, stability=10.0, last_seen=NOW)
    rusty = NodeState("n", a=9.0, b=1.0, stability=10.0, last_seen=add_days(NOW, -45))
    assert p_eff(rusty, NOW) < p_eff(fresh, NOW) / 50, "the two must differ sharply under p_eff"
    it = item("i", "n", b=0.5)
    assert (selection.predicted_success(rusty, it, NOW)
            == selection.predicted_success(fresh, it, NOW))


# --------------------------------------------------------------------------- pick_item

def test_pick_item_targets_85_percent_not_the_easiest_or_hardest():
    # theta = logit(0.9) = 2.197; the 85% item sits at b = 2.197 - logit(0.85) = 0.463.
    states = {"n": NodeState("n", a=9.0, b=1.0)}
    items = bank(item("easy", "n", -2.0), item("target", "n", 0.5), item("hard", "n", 3.0))
    got = selection.pick_item("n", states, items, NOW, exclude=set())
    assert got.item_id == "target"
    assert selection.predicted_success(states["n"], got, NOW) == pytest.approx(0.85, abs=0.02)


def test_pick_item_prefers_slightly_hard_over_wildly_easy():
    """A 0.99 freebie and a 0.71 stretch are not equally wrong. Nearest to 0.85 wins."""
    states = {"n": NodeState("n", a=9.0, b=1.0)}
    freebie = item("freebie", "n", -3.0)
    stretch = item("stretch", "n", 1.3)
    got = selection.pick_item("n", states, bank(freebie, stretch), NOW, exclude=set())
    assert selection.predicted_success(states["n"], freebie, NOW) > 0.98
    assert got.item_id == "stretch"


def test_a_rusty_node_is_served_a_reviewable_item_not_a_beginner_item(real_items):
    """A six-week-old 0.90 node must come back at roughly 85% success, not at 3%.

    This is the whole point of FIX 1. Scheduling still sees the node as badly overdue (that is
    what p_eff is for), but the item pitched at it has to match the skill the student built, or
    spaced repetition degenerates into handing an expert the easiest problem in the bank.
    """
    node = "alg.factoring"
    state = strong(node, last_seen=add_days(NOW, -45))       # p = 0.975, stability 10 days
    states = {node: state}

    assert is_due(state, NOW), "scheduling must still call this overdue"
    assert p_eff(state, NOW) < 0.02, "p_eff must still have collapsed; that is its job"

    picked = selection.pick_item(node, states, real_items, NOW, exclude=set())
    achieved = selection.predicted_success(state, picked, NOW)
    assert achieved == pytest.approx(TARGET_SUCCESS_RATE, abs=0.05)

    # And the easiest item in the bank, which is what p_eff-based targeting used to hand over,
    # must now be visibly the wrong choice.
    easiest = min(real_items[node], key=lambda i: i.difficulty_b)
    assert picked.item_id != easiest.item_id
    assert selection.predicted_success(state, easiest, NOW) > 0.98

    # The same must hold for the entry that actually lands in the daily set.
    entries = selection.compose_daily_set(real_graph(), states, real_items, NOW)
    review = [e for e in entries if e.item.node_id == node]
    assert review, "the overdue node should be in today's set"
    assert review[0].slot == "review"
    assert review[0].predicted_success > 0.6


def test_pick_item_honours_exclude_and_empty_banks():
    states = {"n": NodeState("n", a=9.0, b=1.0)}
    items = bank(item("easy", "n", -2.0), item("target", "n", 0.5), item("hard", "n", 3.0))
    second = selection.pick_item("n", states, items, NOW, exclude={"target"})
    assert second.item_id == "easy"                    # 0.985 is nearer 0.85 than 0.30 is
    assert selection.pick_item("n", states, items, NOW,
                               exclude={"easy", "target", "hard"}) is None
    assert selection.pick_item("missing", states, items, NOW, exclude=set()) is None


def test_pick_item_is_deterministic_under_ties():
    """Two items of identical difficulty must resolve the same way every run, whatever order the
    bank happens to be in."""
    states = {"n": NodeState("n", a=9.0, b=1.0)}
    b_star = math.log(9.0) - math.log(0.85 / 0.15)
    tied = {"n": [item("zz", "n", b_star), item("aa", "n", b_star)]}
    reversed_bank = {"n": list(reversed(tied["n"]))}
    assert selection.pick_item("n", states, tied, NOW, exclude=set()).item_id == "aa"
    assert selection.pick_item("n", states, reversed_bank, NOW, exclude=set()).item_id == "aa"


# --------------------------------------------------------------------------- graph geometry

def test_goal_path_is_a_real_prereq_chain_ending_at_the_target():
    g = real_graph()
    path = selection.goal_path(g)
    assert path[-1] == g.target_node
    assert not g.prereqs(path[0]), "the chain must start at a root"
    for earlier, later in zip(path, path[1:]):
        assert earlier in [p for p, _w in g.prereqs(later)]


def test_goal_ancestors_is_exactly_the_transitive_prereq_closure_of_the_target():
    """Checked against an independent closure rather than a list of node names: the graph is still
    being edited, and "is this node on the way to SGD" is a fact about the edges, not a fact to
    hard-code."""
    g = real_graph()
    ancestors = set(selection.goal_ancestors(g))
    assert ancestors == ancestors_of(g, g.target_node)
    assert set(selection.goal_path(g)) - {g.target_node} <= ancestors
    assert g.target_node not in ancestors, "the target is not its own prerequisite"

    excluded = set(g.nodes) - ancestors - {g.target_node}
    assert excluded, "goal slicing that keeps the whole graph is not slicing anything"
    for node_id in excluded:
        assert g.target_node not in descendants_of(g, node_id)


# --------------------------------------------------------------------------- compose_daily_set

def test_locked_nodes_with_no_prior_learning_stay_out_of_new_work():
    """The guard that keeps the lock rule from becoming "serve anything to anyone".

    `locked` still means exactly what it says for the new and goal_link slots: prereqs are not
    ready, so this is not something to start today.
    """
    g = toy_graph()
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    entries = selection.compose_daily_set(g, {}, items, NOW)

    assert entries, "roots are available, the set must not be empty"
    learning_slots = [e for e in entries if e.slot in ("new", "goal_link")]
    assert learning_slots
    for e in learning_slots:
        assert status(e.item.node_id, g, {}, NOW) != "locked", e.reason

    served = {e.item.node_id for e in entries}
    for node_id in ("m1", "m2", "d"):
        assert status(node_id, g, {}, NOW) == "locked"
        assert node_id not in served, f"{node_id} is locked with nothing learned behind it"


def test_new_learning_slots_refuse_a_locked_node_even_if_offered_one(monkeypatch):
    """Defence in depth, and the only test that can tell the two layers apart.

    `_new_candidates` already filters by status, so the slot-level check in `compose_daily_set` is
    unfalsifiable through the normal path: both layers say no. This forces a candidate list that
    offers locked nodes anyway, which is exactly what a future candidate rule would do if someone
    loosened it, and pins that the slot still refuses.
    """
    g = toy_graph()
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    monkeypatch.setattr(selection, "_CANDIDATE_FNS",
                        {**selection._CANDIDATE_FNS,
                         "new": lambda graph, states, now, statuses: ["d", "m1", "m2"]})

    entries = selection.compose_daily_set(g, {}, items, NOW)

    assert all(status(e.item.node_id, g, {}, NOW) != "locked" for e in entries)
    assert {"d", "m1", "m2"}.isdisjoint({e.item.node_id for e in entries})
    assert not [e for e in entries if e.slot == "new"], "every offered node was locked"


def test_a_locked_node_with_an_open_misconception_is_still_a_blocker():
    """Remediation is not gated on status. Clearing a blocker is the core loop, and a student
    cannot be told to come back later for a mistake they are making right now."""
    g = toy_graph()
    states = {"d": NodeState("d", misconceptions=("locked-but-still-wrong",))}
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    entries = selection.compose_daily_set(g, states, items, NOW)

    assert status("d", g, states, NOW) == "locked", "precondition: d's prereqs are not ready"
    blockers = [e for e in entries if e.slot == "blocker"]
    assert [e.item.node_id for e in blockers] == ["d"]
    assert entries[0].item.node_id == "d", "and it still leads the set"
    assert entries[0].reason == "Blocker: locked but still wrong"


def test_composed_set_has_no_duplicate_items_or_nodes(real_items):
    g = real_graph()
    states = {}
    entries = selection.compose_daily_set(g, states, real_items, NOW)
    item_ids = [e.item.item_id for e in entries]
    node_ids = [e.item.node_id for e in entries]
    assert len(item_ids) == len(set(item_ids))
    assert len(node_ids) == len(set(node_ids))


def test_blockers_come_first_and_are_ordered_by_misconception_count():
    g = toy_graph()
    states = {
        "r1": NodeState("r1", misconceptions=("incorrect-sign-distribution",)),
        "r2": NodeState("r2", misconceptions=("fraction-subtraction-error",
                                              "incorrect-fraction-combination")),
    }
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    entries = selection.compose_daily_set(g, states, items, NOW)

    slots = [e.slot for e in entries]
    assert slots[:2] == ["blocker", "blocker"]
    assert "blocker" not in slots[2:], "blockers must be contiguous at the front"
    assert entries[0].item.node_id == "r2", "two misconceptions outrank one"
    assert entries[0].reason == "Blocker: fraction subtraction error"
    assert entries[1].reason == "Blocker: incorrect sign distribution"


def test_blockers_picked_up_in_redistribution_are_still_moved_to_the_front():
    """Three open misconceptions but a budget of two. The third is only reached in redistribution,
    long after the new and goal_link entries were chosen, and it must still lead the set: the
    student works the set top to bottom, so "blockers first" is about the order they are shown,
    not the order they were selected."""
    # Six roots: a1..a3 clean, m1..m3 carrying misconceptions, and a target above a1 so the
    # goal_link slot has something to want too.
    nodes = {n: {"id": n, "title": n.upper(), "prereqs": []}
             for n in ("a1", "a2", "a3", "m1", "m2", "m3")}
    nodes["t"] = {"id": "t", "title": "Target", "prereqs": [{"id": "a1", "weight": 1.0}]}
    g = Graph(nodes=nodes, target_node="t")
    states = {
        "m1": NodeState("m1", misconceptions=("one",)),
        "m2": NodeState("m2", misconceptions=("two", "three")),
        "m3": NodeState("m3", misconceptions=("four",)),
    }
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    entries = selection.compose_daily_set(g, states, items, NOW)

    slots = [e.slot for e in entries]
    assert slots.count("blocker") == 3, "redistribution should reach the third misconception"
    assert slots[:3] == ["blocker"] * 3, "every blocker leads, wherever it was picked up"
    assert "blocker" not in slots[3:]

    ordered = selection.interleave(entries, g)
    assert [e.slot for e in ordered][:3] == ["blocker"] * 3, "interleaving must not bury them"


def test_a_decayed_prereq_does_not_erase_a_due_review(real_items):
    """The black hole this rule exists to close.

    A node the student learned weeks ago comes due. One of its prereqs has decayed far enough to
    put the node in `locked`. If locked suppressed the review, the node would never be served
    again: not served, so it decays further, so it stays locked, and it leaves the schedule
    permanently with nothing reporting it. Worse, the review is the repair, since a correct
    attempt refreshes the prereqs that are holding the lock shut.
    """
    g = real_graph()

    learned, prereq = None, None
    for node_id in sorted(g.nodes):
        prereqs = [p for p, _w in g.prereqs(node_id)]
        if len(prereqs) == 1 and real_items.get(node_id) and not g.prereqs(prereqs[0]):
            learned, prereq = node_id, prereqs[0]
            break
    assert learned, "the graph should have a node standing on a single root"

    states = {
        prereq: strong(prereq, last_seen=add_days(NOW, -45)),
        learned: strong(learned, last_seen=add_days(NOW, -60)),
    }

    assert status(learned, g, states, NOW) == "locked", "precondition: the prereq has decayed"
    assert is_due(states[learned], NOW), "precondition: the review is genuinely due"
    assert states[learned].successes >= 1, "precondition: this was really learned once"

    entries = selection.compose_daily_set(g, states, real_items, NOW)
    served = {e.item.node_id: e for e in entries}
    assert learned in served, "a locked node that was already learned is still reviewable"
    assert served[learned].slot == "review"
    assert served[learned].reason == "Review, due today"


def test_review_still_requires_evidence_that_something_was_learned():
    """The other half of the trade. Lifting the locked check must not turn the review slot into a
    dumping ground for nodes the student merely brushed against once."""
    g = toy_graph()
    states = {
        "r1": strong("r1", last_seen=add_days(NOW, -60)),                     # learned, due
        "r2": NodeState("r2", a=1.0, b=1.2, stability=1.0,                    # one stray miss
                        last_seen=add_days(NOW, -60), successes=0),
    }
    statuses = {n: status(n, g, states, NOW) for n in g.nodes}
    candidates = selection._review_candidates(g, states, NOW, statuses)

    assert is_due(states["r2"], NOW), "r2 is due; it just never demonstrated anything"
    assert states["r2"].evidence < selection.REVIEW_MIN_EVIDENCE
    assert candidates == ["r1"]


def test_review_slot_picks_the_most_overdue_first():
    g = toy_graph()
    states = {
        "r1": strong("r1", last_seen=add_days(NOW, -60)),     # stability 10 -> R = e^-6
        "r2": strong("r2", last_seen=add_days(NOW, -20)),     # R = e^-2
        "iso": strong("iso", last_seen=NOW),                  # not due
    }
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    entries = selection.compose_daily_set(g, states, items, NOW)

    reviews = [e for e in entries if e.slot == "review"]
    assert [e.item.node_id for e in reviews] == ["r1", "r2"]
    assert all(e.reason == "Review, due today" for e in reviews)
    assert "iso" not in {e.item.node_id for e in reviews}, "a fresh node is not due"


def test_new_candidates_are_ordered_goal_path_first():
    g = toy_graph()
    statuses = {n: status(n, g, {}, NOW) for n in g.nodes}
    ordered = selection._new_candidates(g, {}, NOW, statuses)

    assert set(ordered) == {"r1", "r2", "iso"}, "frontier nodes only, no locked ones"
    assert ordered[0] == "r1", "r1 is the frontier node on the path to the target"
    assert ordered.index("r1") < ordered.index("iso"), "the island comes last"


def test_new_slot_prefers_the_goal_path_and_reads_like_a_sentence():
    g = toy_graph()
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    entries = selection.compose_daily_set(g, states={}, items_by_node=items, now=NOW)

    new = [e for e in entries if e.slot == "new"]
    assert new, "with everything unseen, the set should be mostly new work"
    assert new[0].item.node_id in set(selection.goal_path(g))
    assert new[0].reason == f"New: {g.title(new[0].item.node_id)}"
    assert new[0].item.node_id != "iso", "the island is not on the goal path"


def test_goal_link_names_the_target():
    g = toy_graph()
    states = {"r1": strong("r1"), "r2": strong("r2")}          # unlocks m1 and m2
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    entries = selection.compose_daily_set(g, states, items, NOW)

    links = [e for e in entries if e.slot == "goal_link"]
    assert links, "with m1/m2 unlocked there is a goal-linked node to serve"
    assert links[0].reason == "On your path to Deep target"
    assert links[0].item.node_id in set(selection.goal_ancestors(g)) | {"d"}


def test_empty_slots_are_redistributed_so_the_set_stays_full(real_items):
    """No misconceptions and nothing due: blocker and review are empty and the other slots must
    absorb all six places rather than the set coming back short."""
    g = real_graph()
    entries = selection.compose_daily_set(g, {}, real_items, NOW)
    assert len(entries) == DAILY_SET_SIZE
    assert {e.slot for e in entries} <= {"new", "goal_link"}


def busy_day(g: Graph, items_by_node: dict[str, list[Item]]) -> tuple[dict, list[str], list[str]]:
    """The worst realistic day: two open misconceptions and two reviews gone stale.

    Built from the live graph rather than a hand-written list of node ids. A stale node drags its
    dependents into `locked`, so the two review nodes have to be roots that none of the blockers
    stand on, and a graph edit can quietly break that. Choosing them here, and asserting the
    preconditions in the tests, means a graph edit fails loudly instead of turning the worst day
    into a quiet one and taking the assertions with it.
    """
    # Shallowest first, so unlocking the blockers costs the fewest nodes and leaves the rest of
    # the graph free to supply reviews, new work and a goal link.
    candidates = [n for n in sorted(g.nodes) if g.prereqs(n) and items_by_node.get(n)]
    blockers = sorted(candidates, key=lambda n: (len(ancestors_of(g, n)), n))[:2]

    fresh: set[str] = set()
    for node_id in blockers:
        fresh |= {node_id} | ancestors_of(g, node_id)

    reviews = [n for n in sorted(g.nodes)
               if not g.prereqs(n) and n not in fresh and items_by_node.get(n)][:2]

    states = {n: strong(n) for n in fresh}
    # Blockers are mid-learning, not mastered: a misconception is something you hit on the way up.
    states[blockers[0]] = NodeState(blockers[0], a=8.0, b=2.0, stability=5.0, last_seen=NOW,
                                    successes=2,
                                    misconceptions=("dropped-a-negative-across-the-bracket",
                                                    "sign-slip-in-the-second-term"))
    states[blockers[1]] = NodeState(blockers[1], a=8.0, b=2.0, stability=5.0, last_seen=NOW,
                                    successes=2, misconceptions=("misapplied-the-rule",))
    for offset, node_id in enumerate(reviews):
        states[node_id] = strong(node_id, last_seen=add_days(NOW, -45 + 10 * offset))
    return states, blockers, reviews


def assert_busy_day_is_really_busy(g, states, blockers, reviews) -> None:
    """The fixture's preconditions. Without these a graph edit could make the scenario quiet and
    the tests below would still pass while checking nothing."""
    assert len(blockers) == 2 and len(reviews) == 2, "the scenario needs two of each"
    for node_id in blockers:
        assert status(node_id, g, states, NOW) != "locked", f"{node_id} must be workable"
        assert states[node_id].misconceptions
    for node_id in reviews:
        assert status(node_id, g, states, NOW) != "locked", f"{node_id} must be workable"
        assert is_due(states[node_id], NOW), f"{node_id} must actually be due"
    assert set(blockers).isdisjoint(reviews)


def test_a_maximally_busy_day_still_carries_the_goal_link(real_items):
    """Two blockers and two reviews consume four of six places. SET_BUDGET sums to exactly six, so
    without a reserved slot the goal link is the one that silently disappears, on precisely the day
    the student most needs to see why any of this is worth doing."""
    g = real_graph()
    states, blockers, reviews = busy_day(g, real_items)
    assert_busy_day_is_really_busy(g, states, blockers, reviews)
    entries = selection.compose_daily_set(g, states, real_items, NOW)

    slots = [e.slot for e in entries]
    assert len(entries) == DAILY_SET_SIZE, "a busy day must still be a full day"
    assert slots.count("blocker") == 2
    assert slots[:2] == ["blocker", "blocker"]
    assert slots.count("review") == 2
    assert slots.count("goal_link") == 1, "the reserved slot must survive"
    assert slots.count("new") == 1, "new gives way to blockers, it does not vanish"

    link = next(e for e in entries if e.slot == "goal_link")
    assert link.reason == f"On your path to {g.title(g.target_node)}"
    assert link.item.node_id in set(selection.goal_ancestors(g)) | {g.target_node}


def test_the_goal_link_slot_is_reserved_not_merely_last_in_a_shorter_set(real_items):
    """SET_BUDGET summing to exactly DAILY_SET_SIZE hides the bug at size 6. Ask for a shorter set
    and the blockers and reviews will eat every place unless the slot is genuinely held back."""
    g = real_graph()
    states, blockers, reviews = busy_day(g, real_items)
    assert_busy_day_is_really_busy(g, states, blockers, reviews)
    entries = selection.compose_daily_set(g, states, real_items, NOW, size=4)
    slots = [e.slot for e in entries]
    assert len(entries) == 4
    assert "goal_link" in slots, "the goal link must be reserved, not left to leftovers"
    assert slots.count("blocker") == 2, "blockers still outrank everything for the other places"


def test_the_goal_link_slot_survives_an_oversubscribed_budget(monkeypatch, real_items):
    """Regression guard for the original bug. If SET_BUDGET is ever retuned so the slots sum to
    more than the set size, the reserve is the only thing keeping the goal link in the set."""
    monkeypatch.setattr(selection, "SET_BUDGET",
                        {"blocker": 2, "review": 2, "new": 2, "goal_link": 1})
    g = real_graph()
    states, _blockers, _reviews = busy_day(g, real_items)
    entries = selection.compose_daily_set(g, states, real_items, NOW)
    slots = [e.slot for e in entries]
    assert len(entries) == DAILY_SET_SIZE
    assert slots.count("goal_link") == 1


def test_new_grows_into_unused_blocker_slots_up_to_its_maximum(real_items):
    """The other side of the trade. Day one: nothing is blocking and nothing is due, so four of
    the six places are unclaimed and goal_link is the only other slot competing for them. They go
    to new work up to NEW_SLOT_MAX before anything else picks up the slack."""
    entries = selection.compose_daily_set(real_graph(), {}, real_items, NOW)

    slots = [e.slot for e in entries]
    assert len(entries) == DAILY_SET_SIZE
    assert slots.count("blocker") == 0 and slots.count("review") == 0
    assert slots.count("new") >= NEW_SLOT_MAX, "unused blocker slots become new work"
    assert slots.count("goal_link") >= 1, "the reserved slot is still there"
    assert slots.count("new") > slots.count("goal_link"), (
        "a day with nothing blocking is a day you advance: the spare places go to new work, "
        "not to more copies of the same goal-link reason")


def test_short_bank_returns_a_short_set_rather_than_padding():
    g = toy_graph()
    items = bank(item("only-r1", "r1", 0.0), item("only-r2", "r2", 0.0))
    entries = selection.compose_daily_set(g, {}, items, NOW)
    assert len(entries) == 2, "two usable items means a set of two, never junk padding"
    assert {e.item.item_id for e in entries} == {"only-r1", "only-r2"}
    assert selection.compose_daily_set(g, {}, {}, NOW) == []


def test_set_size_is_respected_when_requested_smaller():
    g = toy_graph()
    items = bank(*[item(f"{n}-{k}", n, b) for n in g.nodes for k, b in enumerate((-1.0, 0.0, 1.0))])
    assert len(selection.compose_daily_set(g, {}, items, NOW, size=3)) == 3


# --------------------------------------------------------------------------- real data end to end

def test_compose_daily_set_on_the_real_graph_and_real_bank(real_items):
    g = real_graph()
    states, blockers, reviews = busy_day(g, real_items)
    assert_busy_day_is_really_busy(g, states, blockers, reviews)

    entries = selection.compose_daily_set(g, states, real_items, NOW)

    assert len(entries) == DAILY_SET_SIZE
    assert all(isinstance(e, SetEntry) for e in entries)
    assert len({e.item.item_id for e in entries}) == DAILY_SET_SIZE
    assert len({e.item.node_id for e in entries}) == DAILY_SET_SIZE
    assert all(status(e.item.node_id, g, states, NOW) != "locked" for e in entries)
    assert all(e.reason and not e.reason.endswith(":") for e in entries)
    assert all(0.0 < e.predicted_success < 1.0 for e in entries)
    assert all(e.slot in SET_BUDGET for e in entries)

    assert [e.item.node_id for e in entries[:2]] == blockers, "blockers first, worst first"
    assert entries[0].reason.startswith("Blocker: ")
    assert {e.item.node_id for e in entries} >= set(reviews), "both stale nodes are due"

    # Every served item must actually exist in the bank under its node.
    for e in entries:
        assert e.item in real_items[e.item.node_id]


def test_real_set_difficulty_is_targeted_not_random(real_items):
    """Across a cold-start set, chosen items should sit closer to 0.85 than the node's median item.
    This is the property that makes 'no problems we expect you to fail' true."""
    g = real_graph()
    entries = selection.compose_daily_set(g, {}, real_items, NOW)
    for e in entries:
        state = NodeState(e.item.node_id)
        chosen = abs(e.predicted_success - TARGET_SUCCESS_RATE)
        alternatives = [abs(selection.predicted_success(state, i, NOW) - TARGET_SUCCESS_RATE)
                        for i in real_items[e.item.node_id]]
        assert chosen == pytest.approx(min(alternatives)), e.item.item_id


# --------------------------------------------------------------------------- interleave

def entry(node_id: str, slot: str = "new", item_id: str | None = None) -> SetEntry:
    return SetEntry(item=item(item_id or f"{node_id}-i", node_id, 0.0), slot=slot, reason="x")


def test_interleave_separates_same_neighbourhood_items():
    g = chain_graph(6)
    before = [entry(f"n{i}") for i in range(6)]           # every neighbour adjacent: the worst case
    after = selection.interleave(before, g)

    assert [e.item.item_id for e in after] != [e.item.item_id for e in before]
    assert {e.item.item_id for e in after} == {e.item.item_id for e in before}

    dist = selection._Distances(g)
    gaps_before = [dist.between(before[i].item.node_id, before[i + 1].item.node_id)
                   for i in range(5)]
    gaps_after = [dist.between(after[i].item.node_id, after[i + 1].item.node_id)
                  for i in range(5)]
    assert gaps_before == [1, 1, 1, 1, 1]
    assert min(gaps_after) >= 2, "no two adjacent items may be graph neighbours"
    assert (selection.mean_adjacent_distance(after, g)
            > selection.mean_adjacent_distance(before, g))


def test_interleave_keeps_blockers_first():
    g = chain_graph(6)
    entries = ([entry("n0", slot="blocker"), entry("n1", slot="blocker")]
               + [entry(f"n{i}") for i in range(2, 6)])
    after = selection.interleave(entries, g)
    assert [e.slot for e in after[:2]] == ["blocker", "blocker"]
    assert [e.item.node_id for e in after[:2]] == ["n0", "n1"], "blocker order is preserved"
    assert {e.item.node_id for e in after} == {f"n{i}" for i in range(6)}


def test_interleave_pushes_the_tail_away_from_the_last_blocker():
    g = chain_graph(6)
    entries = [entry("n0", slot="blocker")] + [entry(f"n{i}") for i in (1, 2, 4, 5)]
    after = selection.interleave(entries, g)
    dist = selection._Distances(g)
    assert dist.between("n0", after[1].item.node_id) >= 2, \
        "do not follow a blocker with its own neighbour"


def test_interleave_handles_unreachable_nodes_and_tiny_sets():
    g = toy_graph()
    assert selection.interleave([], g) == []
    single = [entry("r1")]
    assert selection.interleave(single, g) == single
    pair = [entry("r1"), entry("iso")]
    assert {e.item.node_id for e in selection.interleave(pair, g)} == {"r1", "iso"}


def test_interleave_on_a_real_set_does_not_lose_or_invent_items(real_items):
    g = real_graph()
    entries = selection.compose_daily_set(g, {}, real_items, NOW)
    after = selection.interleave(entries, g)
    assert sorted(e.item.item_id for e in after) == sorted(e.item.item_id for e in entries)
    assert (selection.mean_adjacent_distance(after, g)
            >= selection.mean_adjacent_distance(entries, g))
