"""XP, built so that grinding is worthless.

    XP = XP_BASE * depth * novelty * quality

Every term is there to close a specific hole:

  depth    stops a student farming the easy roots of the graph. A root is worth 1x, the target
           node is worth its full chain length.
  novelty  stops repetition farming. A new frontier node is worth 3.0, a genuine due review 1.0,
           and re-practising something not due 0.15, i.e. twenty times less than learning it.
  quality  stops hint-mining. Hints are never blocked and never refused (see the hint ladder in
           learning-design.md 4.6), they just pay less.

Difficulty is already auto-targeted at 85% success by engine.selection, so there is no supply of
easy wins to farm either. Those two properties together are what make a leaderboard safe to ship.

See learning-design.md section 5.
"""

from __future__ import annotations

from typing import Optional

from engine.types import (
    NOVELTY_NEW,
    NOVELTY_OVERPRACTICE,
    NOVELTY_REVIEW,
    QUALITY_XP_CLEAN,
    QUALITY_XP_HINTED,
    QUALITY_XP_REVEALED,
    XP_BASE,
    Attempt,
    Graph,
)

# depth caches, keyed by id(graph). The graph object is kept alive alongside its cache so that a
# recycled id can never hand back another graph's depths. Graph is a mutable, unhashable dataclass,
# so it cannot be a dict key or a WeakKeyDictionary key; this is the honest way to memoise it.
_DEPTH_CACHE: dict[int, tuple[Graph, dict[str, int]]] = {}


def clear_depth_cache() -> None:
    """Drop every memoised depth. Only needed if a graph is mutated in place, which the engine
    does not do, and by tests."""
    _DEPTH_CACHE.clear()


def _cache_for(graph: Graph) -> dict[str, int]:
    entry = _DEPTH_CACHE.get(id(graph))
    if entry is None or entry[0] is not graph:
        entry = (graph, {})
        _DEPTH_CACHE[id(graph)] = entry
    return entry[1]


def node_depth(graph: Graph, node_id: str) -> int:
    """1 + the longest prereq chain from this node down to a root.

    A node with no prereqs is depth 1. Memoised per graph, and guarded against cycles: the graph
    is required to be a DAG, but a bad import must degrade rather than hang the server.
    """
    cache = _cache_for(graph)
    if node_id in cache:
        return cache[node_id]

    visiting: set[str] = set()

    def depth(current: str) -> int:
        if current in cache:
            return cache[current]
        if current in visiting:
            # Cycle. The graph is malformed; treat the back edge as if it were a root so the
            # computation terminates instead of recursing forever.
            return 0
        visiting.add(current)
        best = 0
        for prereq_id, _weight in graph.prereqs(current):
            if prereq_id == current or prereq_id not in graph.nodes:
                continue
            best = max(best, depth(prereq_id))
        visiting.discard(current)
        result = best + 1
        cache[current] = result
        return result

    return depth(node_id)


def novelty_for(status_before: str, was_due: bool, prior_attempts_on_node: int) -> float:
    """NOVELTY_NEW is paid once per node, on the first attempt, and never again.

    "Frontier" alone is not enough. A node stays on the frontier until mastery crosses
    LEARNING_FLOOR, so paying 3.0x for every frontier attempt would let a student sit on one node
    and collect the new-work rate indefinitely, which is the exact farm this formula exists to
    prevent. Later attempts on a still-frontier node are honest work, so they earn the review
    rate rather than the over-practice rate.

    `prior_attempts_on_node` is the count of earlier attempts on this node from the attempt log.
    The caller has it for free, so this function stays stateless.
    """
    if status_before == "frontier":
        return NOVELTY_NEW if prior_attempts_on_node <= 0 else NOVELTY_REVIEW
    if was_due:
        return NOVELTY_REVIEW
    return NOVELTY_OVERPRACTICE


def quality_for(hint_level: int) -> float:
    """Rung 0 is clean, rungs 1 to 3 are hinted, rung 4 is the full reveal plus twin problem."""
    if hint_level <= 0:
        return QUALITY_XP_CLEAN
    if hint_level <= 3:
        return QUALITY_XP_HINTED
    return QUALITY_XP_REVEALED


def xp_for(attempt: Attempt, graph: Graph, status_before: str, was_due: bool,
           *, prior_attempts_on_node: int) -> int:
    """XP earned for one attempt. Wrong answers earn nothing, and nothing ever earns less than 0.

    `status_before` is the node's status BEFORE this attempt was applied, because the whole point
    is to pay for the transition, not for the state it left behind. `was_due` likewise comes from
    the pre-attempt state, and `prior_attempts_on_node` counts this student's earlier attempts on
    this node in the log, excluding this one. It is keyword-only and required: defaulting it to 0
    would silently reopen the new-work farm for every caller that forgot it.
    """
    if not attempt.correct:
        return 0
    raw = (XP_BASE
           * node_depth(graph, attempt.node_id)
           * novelty_for(status_before, was_due, prior_attempts_on_node)
           * quality_for(attempt.hint_level))
    return max(0, int(round(raw)))
