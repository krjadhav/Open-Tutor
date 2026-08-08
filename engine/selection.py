"""Item selection and daily set composition.

This module answers two questions:

  1. Given a node, which single item should the student see right now?
     Answer: the one whose predicted success is nearest TARGET_SUCCESS_RATE (0.85). We never
     serve a problem the model expects the student to fail, and we never serve a freebie.
  2. Given the whole graph, which handful of nodes should today's set touch?
     Answer: SET_BUDGET, filled in priority order (blocker, review, new, goal_link), then
     interleaved so that adjacent items are far apart in the graph.

See learning-design.md section 4.4. Everything here is pure: time is an argument, state is an
argument, nothing is mutated in place.

Divergence from the design doc, recorded deliberately: 4.4 specifies the review slot as a greedy
set cover over `encompasses` ("repetition compression"). This implementation ranks due nodes by
how overdue they are (lowest retrievability first), which is the simpler rule the engine contract
asks for. Set cover is a strictly better rule and should replace `_review_candidates` later.
"""

from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from typing import Iterable, Optional, Sequence

from engine.mastery import is_due, retrievability, status
from engine.types import (
    DAILY_SET_SIZE,
    NEW_SLOT_MAX,
    SET_BUDGET,
    TARGET_SUCCESS_RATE,
    Graph,
    Item,
    NodeState,
    SetEntry,
)

# Slot fill order. This is the priority order, and it is also the order the slots are revisited
# when a slot comes up empty and its budget has to be redistributed.
SLOT_ORDER: tuple[str, ...] = ("blocker", "review", "new", "goal_link")

# goal_link is RESERVED. SET_BUDGET now sums to exactly DAILY_SET_SIZE, so filling goal_link last
# would mean a day with two blockers and two reviews silently drops the one slot that answers "why
# am I doing this". What gets reserved is the SLOT, not a particular node: the other slots are
# capped so a place is left over, but they still choose their nodes first. Reserving the node
# instead would let goal_link steal a node that a blocker needed, and a blocker outranks it.
RESERVED_SLOTS: tuple[str, ...] = ("goal_link",)

# `locked` gates NEW LEARNING ONLY. It never suppresses a review or a remediation.
#
# A locked node is one whose prereqs have fallen below PREREQ_READY. For new work that is exactly
# right: the student is not ready. For a node they already learned it is wrong, and wrong in a way
# that does not recover on its own:
#
#   - Retrievability models forgetting, not skill removal. A student does not stop being able to
#     review a skill because a prerequisite got rusty. This is the same conflation that
#     `predicted_success` had, one layer up.
#   - The suppression is self-reinforcing and terminal. Not served, so it decays further, so it
#     stays locked, so it is never served again. The node leaves the schedule permanently and
#     nothing reports it.
#   - The review is the repair. A correct attempt refreshes the encompassed prereqs, which is what
#     pulls the stale prereq back above the gate. Blocking the review removes the only mechanism
#     that would have fixed the lock.
#
# So review and blocker candidates are chosen on evidence of prior learning (see
# `_has_prior_learning`) and on open misconceptions, not on status.
LOCK_GATED_SLOTS: frozenset[str] = frozenset({"new", "goal_link"})

# Evidence weight that counts as "this was genuinely learned once", for nodes with no spaced
# success yet. One photo of complete working, or a couple of typed attempts.
REVIEW_MIN_EVIDENCE = 1.0

# Hop distance used when two nodes are in different connected components. Large enough to always
# beat a real distance, small enough to stay readable in test output.
FAR_DISTANCE = 99

# Mastery is clamped before logit so that a saturated posterior does not produce infinities.
P_FLOOR = 0.01
P_CEIL = 0.99

_REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- item bank

def _resolve(path: str | Path) -> Path:
    """Accept a path relative to the repo root as well as one relative to the cwd."""
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p
    candidate = _REPO_ROOT / p
    return candidate if candidate.exists() else p


def load_items(path: str | Path = "data/items/items.json") -> dict[str, list[Item]]:
    """Load the item bank, keyed by node_id.

    The on-disk file carries extra fields (`check`, `answer_is_checkable`, licensing notes) that
    the engine does not model. They are dropped here rather than smuggled into `Item`, because
    `Item` is the contract and the checker owns the rest.
    """
    raw = json.loads(_resolve(path).read_text(encoding="utf-8"))
    records = raw["items"] if isinstance(raw, dict) else raw

    by_node: dict[str, list[Item]] = {}
    for rec in records:
        item = Item(
            item_id=rec["item_id"],
            node_id=rec["node_id"],
            stem_latex=rec.get("stem_latex", ""),
            answer_latex=rec.get("answer_latex"),
            answer_sympy=rec.get("answer_sympy"),
            answer_kind=rec.get("answer_kind"),
            difficulty_b=float(rec.get("difficulty_b") or 0.0),
            encompasses=tuple(rec.get("encompasses") or ()),
            source=rec.get("source", "openstax"),
        )
        by_node.setdefault(item.node_id, []).append(item)

    # Deterministic order: selection ties must not depend on json ordering.
    for items in by_node.values():
        items.sort(key=lambda i: i.item_id)
    return by_node


# --------------------------------------------------------------------------- difficulty targeting

def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)          # avoid overflow for very negative x
    return e / (1.0 + e)


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def predicted_success(state: NodeState, item: Item, now) -> float:
    """P(correct) for this student on this item, Rasch style: sigmoid(theta - b).

    theta comes from `state.p`, NOT from `p_eff`. This is deliberate and it is the one thing in
    this module most likely to be "corrected" back by mistake. Retrievability models forgetting,
    not skill loss: a student who reached p = 0.90 and has not seen the topic for six weeks is
    rusty, not a beginner, and p_eff would put them at 0.0005 and hand them a trivial item.
    Spaced repetition only works when the retrieval is effortful but achievable, so the item has
    to be pitched at the skill they built, not at the memory that decayed.

    p_eff keeps its job elsewhere: scheduling. It decides `is_due` and whether a prereq counts as
    satisfied. Those uses are correct and untouched.

    p is clamped into [0.01, 0.99] before the logit, otherwise a saturated posterior sends theta
    to infinity.
    """
    p = min(P_CEIL, max(P_FLOOR, state.p))
    theta = _logit(p)
    return _sigmoid(theta - item.difficulty_b)


def _state_for(node_id: str, states: dict[str, NodeState]) -> NodeState:
    """A node we have never touched has an honest unknown prior, not a missing entry."""
    return states.get(node_id) or NodeState(node_id=node_id)


def pick_item(
    node_id: str,
    states: dict[str, NodeState],
    items_by_node: dict[str, list[Item]],
    now,
    exclude: set[str],
) -> Optional[Item]:
    """The item for this node whose predicted success sits nearest TARGET_SUCCESS_RATE."""
    candidates = [i for i in items_by_node.get(node_id, ()) if i.item_id not in exclude]
    if not candidates:
        return None
    state = _state_for(node_id, states)
    return min(
        candidates,
        key=lambda i: (abs(predicted_success(state, i, now) - TARGET_SUCCESS_RATE), i.item_id),
    )


# --------------------------------------------------------------------------- graph geometry

def _undirected_adj(graph: Graph) -> dict[str, set[str]]:
    """Prereq edges, forgetting direction. Distance here means 'related topic', not 'depends on'."""
    adj: dict[str, set[str]] = {n: set() for n in graph.nodes}
    for node_id in graph.nodes:
        for prereq_id, _w in graph.prereqs(node_id):
            if prereq_id not in adj:
                adj[prereq_id] = set()
            adj[node_id].add(prereq_id)
            adj[prereq_id].add(node_id)
    return adj


def _hops_from(adj: dict[str, set[str]], source: str) -> dict[str, int]:
    """BFS hop distance from one node to every node it can reach."""
    dist = {source: 0}
    queue = deque([source])
    while queue:
        cur = queue.popleft()
        for nxt in sorted(adj.get(cur, ())):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                queue.append(nxt)
    return dist


class _Distances:
    """Lazily-BFS'd undirected hop distances for one graph."""

    def __init__(self, graph: Graph):
        self._adj = _undirected_adj(graph)
        self._cache: dict[str, dict[str, int]] = {}

    def between(self, a: str, b: str) -> int:
        if a == b:
            return 0
        if a not in self._cache:
            self._cache[a] = _hops_from(self._adj, a)
        return self._cache[a].get(b, FAR_DISTANCE)


def goal_path(graph: Graph, target: Optional[str] = None) -> list[str]:
    """The shortest prereq chain from a root node down to the target, target last.

    "On your path to the goal" has to mean something concrete and stable, so it means this chain:
    at every step take the prereq whose own chain is shortest, ties broken by node id. It is the
    cheapest honest route to the target, which is exactly what a student wants pointed out.
    """
    target = target or graph.target_node
    if not target or target not in graph.nodes:
        return []

    memo: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def chain(node_id: str) -> list[str]:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:          # the graph is meant to be a DAG; do not loop if it is not
            return [node_id]
        visiting.add(node_id)
        prereqs = [p for p, _w in graph.prereqs(node_id) if p in graph.nodes]
        best: list[str] = []
        for prereq_id in sorted(prereqs):
            candidate = chain(prereq_id)
            if not best or len(candidate) < len(best):
                best = candidate
        visiting.discard(node_id)
        memo[node_id] = best + [node_id]
        return memo[node_id]

    return chain(target)


def goal_ancestors(graph: Graph, target: Optional[str] = None) -> list[str]:
    """Every node the target transitively depends on, nearest to the target first.

    This is the goal slice of learning-design.md section 6. `goal_path` is a single thread through
    it; this is the whole cloth, used as the fallback when nothing on the thread is available.
    """
    target = target or graph.target_node
    if not target or target not in graph.nodes:
        return []
    order: list[str] = []
    seen = {target}
    queue = deque([target])
    while queue:
        cur = queue.popleft()
        for prereq_id, _w in sorted(graph.prereqs(cur)):
            if prereq_id in seen or prereq_id not in graph.nodes:
                continue
            seen.add(prereq_id)
            order.append(prereq_id)
            queue.append(prereq_id)
    return order


# --------------------------------------------------------------------------- daily set

def _humanise(tag: str) -> str:
    """Misconception tags arrive kebab-cased from the diagnosis call. Students read the reason."""
    return tag.replace("-", " ").replace("_", " ").strip()


def _blocker_candidates(graph, states, now, statuses) -> list[str]:
    """Nodes carrying open misconceptions, most misconceptions first.

    Status is not consulted. An open misconception is a reason to practise a node whatever its
    prereqs are doing, and clearing blockers is the core loop of the product.
    """
    scored = [
        (len(_state_for(n, states).misconceptions), n)
        for n in graph.nodes
        if _state_for(n, states).misconceptions
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [n for _count, n in scored]


def _has_prior_learning(state: NodeState) -> bool:
    """Did the student ever actually learn this, as opposed to merely touch it once?

    A spaced success is the strongest signal; failing that, enough accumulated evidence weight
    that the node is not just a single stray attempt. This is the gate on the review slot, and it
    is the reason lifting the locked check does not become "serve anything to anyone".
    """
    return state.successes >= 1 or state.evidence >= REVIEW_MIN_EVIDENCE


def _review_candidates(graph, states, now, statuses) -> list[str]:
    """Due nodes with prior learning behind them, most overdue first.

    Overdue is measured by how far retrievability has fallen. Status is not consulted, and that is
    the point: see LOCK_GATED_SLOTS.
    """
    due = [
        n for n in graph.nodes
        if is_due(_state_for(n, states), now) and _has_prior_learning(_state_for(n, states))
    ]
    due.sort(key=lambda n: (retrievability(_state_for(n, states), now), n))
    return due


def _new_candidates(graph, states, now, statuses) -> list[str]:
    """Frontier nodes, the ones on the goal path first."""
    on_path = {n: i for i, n in enumerate(goal_path(graph))}
    frontier = [n for n in graph.nodes if statuses[n] == "frontier"]
    frontier.sort(key=lambda n: (n not in on_path, on_path.get(n, 0), n))
    return frontier


def _goal_link_candidates(graph, states, now, statuses) -> list[str]:
    """Nodes on the route to the target that the student can actually work on today.

    The thread (`goal_path`) comes first because it is the sentence we can say out loud. If none
    of it is available we widen to the whole goal slice rather than dropping the slot, since the
    point of this slot is to make the goal visible every single day.
    """
    usable = {"frontier", "learning"}
    thread = [n for n in goal_path(graph) if statuses.get(n) in usable]
    thread.reverse()                                   # nearest the target first
    wider = [
        n for n in goal_ancestors(graph)
        if statuses.get(n) in usable and n not in thread
    ]
    return thread + wider


_CANDIDATE_FNS = {
    "blocker": _blocker_candidates,
    "review": _review_candidates,
    "new": _new_candidates,
    "goal_link": _goal_link_candidates,
}


def _reason(slot: str, node_id: str, graph: Graph, state: NodeState) -> str:
    if slot == "blocker":
        label = _humanise(state.misconceptions[0]) if state.misconceptions else graph.title(node_id)
        return f"Blocker: {label}"
    if slot == "review":
        return "Review, due today"
    if slot == "new":
        return f"New: {graph.title(node_id)}"
    target_title = graph.title(graph.target_node) if graph.target_node else "your goal"
    return f"On your path to {target_title}"


def compose_daily_set(
    graph: Graph,
    states: dict[str, NodeState],
    items_by_node: dict[str, list[Item]],
    now,
    size: int = DAILY_SET_SIZE,
) -> list[SetEntry]:
    """Today's set: `size` entries, blockers first, each carrying the reason it was chosen.

    Filling happens in three stages, because two different things want to be true at once.

      1. Reserve. If any node is eligible for goal_link, one place is held back for it. SET_BUDGET
         sums to exactly DAILY_SET_SIZE, so on a two-blocker two-review day, filling goal_link last
         would drop it. A day with two blockers should be a day you advance less, never a day the
         goal vanishes.
      2. Budgeted. blocker, review then new, each up to SET_BUDGET, in priority order, and
         together capped at `size` minus the reserve.
      3. Reserved slot. goal_link now spends the place held for it.
      4. Redistribution. Slots that came up empty hand their budget back. First round-robin under
         relaxed caps (`new` may grow to NEW_SLOT_MAX using slots the blockers did not need), then
         round-robin uncapped, so a quiet day still comes back full instead of half empty. A
         goal_link whose nodes were all taken by higher-priority slots releases its place here
         rather than leaving the set short.

    `locked` gates the new and goal_link slots only. A node the student already learned stays
    reviewable, and a node with an open misconception stays remediable, however far its prereqs
    have drifted. See LOCK_GATED_SLOTS.

    If the bank genuinely cannot fill the set (no eligible node has an unused item left) this
    returns a SHORTER list. It never pads. A fifth item the student has no business seeing is
    worse than a set of four, and a short set is a visible signal that the graph slice or the item
    bank is too thin, which is information we want rather than information we want hidden.
    """
    statuses = {n: status(n, graph, states, now) for n in graph.nodes}

    queues: dict[str, list[str]] = {}
    cursors: dict[str, int] = {}
    for slot in SLOT_ORDER:
        candidates = _CANDIDATE_FNS[slot](graph, states, now, statuses)
        if slot in LOCK_GATED_SLOTS:
            # New learning only. Review and remediation deliberately pass through: see
            # LOCK_GATED_SLOTS for why suppressing those is a black hole rather than a safeguard.
            candidates = [n for n in candidates if statuses.get(n) != "locked"]
        queues[slot] = candidates
        cursors[slot] = 0

    used_nodes: set[str] = set()
    used_items: set[str] = set()
    entries: list[SetEntry] = []
    filled: dict[str, int] = {slot: 0 for slot in SLOT_ORDER}

    def take(slot: str) -> bool:
        """Pull the next usable node for this slot. False when the slot is exhausted."""
        queue = queues[slot]
        while cursors[slot] < len(queue):
            node_id = queue[cursors[slot]]
            cursors[slot] += 1
            if node_id in used_nodes:
                continue
            item = pick_item(node_id, states, items_by_node, now, used_items)
            if item is None:                         # no item left for this node, try the next one
                continue
            state = _state_for(node_id, states)
            entries.append(SetEntry(
                item=item,
                slot=slot,
                reason=_reason(slot, node_id, graph, state),
                predicted_success=predicted_success(state, item, now),
            ))
            used_nodes.add(node_id)
            used_items.add(item.item_id)
            filled[slot] += 1
            return True
        return False

    def fill(slot: str, cap: int, ceiling: int) -> None:
        while len(entries) < min(size, ceiling) and filled[slot] < cap:
            if not take(slot):
                return

    # Stage 1: hold a place for every reserved slot that has something to put in it.
    reserved = sum(min(SET_BUDGET.get(slot, 0), 1 if queues[slot] else 0)
                   for slot in RESERVED_SLOTS)

    # Stage 2: the rest of the budget, in priority order, leaving the reserve alone.
    for slot in SLOT_ORDER:
        if slot in RESERVED_SLOTS:
            continue
        fill(slot, SET_BUDGET.get(slot, 0), size - reserved)

    # Stage 3: the reserved slots spend what was held for them.
    for slot in RESERVED_SLOTS:
        fill(slot, SET_BUDGET.get(slot, 0), size)

    # Stage 4: redistribute what the empty slots gave back. Round-robin in priority order so one
    # deep slot cannot swallow the whole remainder. Relaxed caps first (this is where `new` grows
    # into unused blocker slots, up to NEW_SLOT_MAX), then uncapped so the set still comes back
    # full on a day with no blockers, no reviews and nothing left on the goal path.
    relaxed = dict(SET_BUDGET)
    relaxed["new"] = max(relaxed.get("new", 0), NEW_SLOT_MAX)
    for caps in (relaxed, None):
        while len(entries) < size:
            progressed = False
            for slot in SLOT_ORDER:
                if len(entries) >= size:
                    break
                if caps is not None and filled[slot] >= caps.get(slot, 0):
                    continue
                if take(slot):
                    progressed = True
            if not progressed:
                break                                # this stage is exhausted, try the next one

    # Blockers lead the set. Everything else keeps its fill order.
    return ([e for e in entries if e.slot == "blocker"]
            + [e for e in entries if e.slot != "blocker"])


# --------------------------------------------------------------------------- interleaving

def _adjacent_score(order: Sequence[SetEntry], dist: _Distances, anchor: Optional[str]) -> tuple:
    """Rank an ordering: worst adjacent gap first, then the total. Bigger is better."""
    gaps = []
    prev = anchor
    for entry in order:
        if prev is not None:
            gaps.append(dist.between(prev, entry.item.node_id))
        prev = entry.item.node_id
    if not gaps:
        return (FAR_DISTANCE, FAR_DISTANCE)
    return (min(gaps), sum(gaps))


def interleave(entries: list[SetEntry], graph: Graph) -> list[SetEntry]:
    """Reorder so that adjacent items are as far apart in the graph as possible.

    Blockers keep the front, in their existing order: they are the reason the set exists and the
    student should hit them while fresh. The tail is ordered greedily, each step taking the
    remaining entry furthest from the one just placed, with every possible seed tried because the
    set is tiny (6) and a bad seed costs the whole ordering.
    """
    blockers = [e for e in entries if e.slot == "blocker"]
    rest = [e for e in entries if e.slot != "blocker"]
    if len(rest) < 2:
        return blockers + rest

    dist = _Distances(graph)
    anchor = blockers[-1].item.node_id if blockers else None

    def greedy(seed_index: int) -> list[SetEntry]:
        remaining = list(rest)
        order = [remaining.pop(seed_index)]
        while remaining:
            prev = order[-1].item.node_id
            before = order[-2].item.node_id if len(order) > 1 else None
            remaining.sort(key=lambda e: (
                -dist.between(prev, e.item.node_id),
                -(dist.between(before, e.item.node_id) if before else 0),
                e.item.item_id,
            ))
            order.append(remaining.pop(0))
        return order

    best = max(
        (greedy(i) for i in range(len(rest))),
        key=lambda order: _adjacent_score(order, dist, anchor),
    )
    return blockers + best


def mean_adjacent_distance(entries: Iterable[SetEntry], graph: Graph) -> float:
    """Mean graph distance between neighbours in a set. Exposed because it is how you check that
    interleaving did anything, and it is worth watching in production too."""
    seq = list(entries)
    if len(seq) < 2:
        return 0.0
    dist = _Distances(graph)
    gaps = [
        dist.between(seq[i].item.node_id, seq[i + 1].item.node_id)
        for i in range(len(seq) - 1)
    ]
    return sum(gaps) / len(gaps)
