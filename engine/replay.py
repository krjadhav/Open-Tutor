"""Deterministic replay of the attempt log into mastery state.

This module is the reason the whole design insists on an event log.

`Attempt` rows are the source of truth and are append-only; `NodeState` is DERIVED by folding
`apply_attempt` over them, and is never mutated in place and stored. That is not fastidiousness,
it is the only thing that makes the system tunable. We are going to change LAMBDA_BLAME,
STABILITY_GROWTH, the implicit-credit discount and the mastery thresholds many times, and the
only honest way to tell whether a change helped is to replay real attempts under the old and new
constants and compare the two histories. If we mutated stored state in place instead, every past
attempt would have already been folded away at the old constants and that history would be gone
permanently: no rebuild, no A/B, no answer. See learning-design.md section 12.1.

Three consequences that the code below has to honour:

  - Replay must be deterministic. Same log, same graph, same constants, same result, bit for bit.
  - Replay must depend on attempt TIMESTAMPS and not on the order rows happen to arrive in.
    Rows come back from Postgres in whatever order the planner likes; a state that depended on
    that would be unreproducible.
  - Replay must be able to stop at an arbitrary point in the past (`upto`) and to emit the whole
    history as frames (`replay_to_frames`), because both offline retuning and the UI's
    "watch your graph light up" animation are the same operation run at different resolutions.

Nothing here mutates its arguments. `apply_attempt` is owned by `engine/mastery.py`; this module
only sequences it.
"""

from __future__ import annotations

import json
from dataclasses import replace as _dc_replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from engine.mastery import apply_attempt
from engine.types import Attempt, Graph, NodeState

DEFAULT_GRAPH_PATH = "data/graph/nodes.json"

# Repo root, so a relative default path works no matter what directory the caller ran from.
# The API server, the tests and a notebook all have different working directories.
_REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- graph loading

def load_graph(path: str | Path = DEFAULT_GRAPH_PATH) -> Graph:
    """Load the knowledge graph from the authored JSON slice.

    Accepts `nodes` as either a list of node dicts (the on-disk format) or an already-keyed dict.
    The Graph itself keys by node id because selection queries prereqs and encompassings
    constantly and a linear scan per query would dominate.

    A relative path is resolved against the current directory first, then against the repo root.
    """
    resolved = _resolve(path)
    with open(resolved, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    raw_nodes = raw.get("nodes", [])
    if isinstance(raw_nodes, dict):
        nodes = {node_id: dict(node) for node_id, node in raw_nodes.items()}
    else:
        nodes = {}
        for node in raw_nodes:
            node_id = node["id"]
            if node_id in nodes:
                raise ValueError(f"duplicate node id in {resolved}: {node_id}")
            nodes[node_id] = dict(node)

    return Graph(nodes=nodes, target_node=raw.get("target_node"))


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate
    return _REPO_ROOT / candidate


# --------------------------------------------------------------------------- replay

def replay(
    attempts: Iterable[Attempt],
    graph: Graph,
    upto: Optional[datetime] = None,
) -> dict[str, NodeState]:
    """Fold an attempt log into per-node state, as of `upto` (or the end of the log).

    Attempts are sorted by timestamp before folding, so the result depends on when things
    happened and not on the order the caller handed them over. Ties are broken by `attempt_id`,
    which keeps two attempts sharing a timestamp from silently producing two different histories.

    `upto` is inclusive and drops everything after it. Passing a past instant reconstructs exactly
    what the system believed at that instant, which is what offline retuning compares against.

    Every node in `graph` appears in the result. A node with no attempts gets a fresh a=b=1 prior,
    an honest "we have no evidence", which is deliberately below PREREQ_READY so an untested
    prereq locks its dependants instead of silently unlocking them. Callers can therefore index
    the result directly rather than guarding every lookup.
    """
    states = _fresh_states(graph)
    for attempt in _ordered(attempts, upto):
        states = apply_attempt(states, graph, attempt, attempt.ts)
    return _with_all_graph_nodes(states, graph)


def replay_to_frames(
    attempts: Iterable[Attempt],
    graph: Graph,
    days: int,
) -> list[tuple[datetime, dict[str, NodeState]]]:
    """One state snapshot per day: `[(end_of_day, states), ...]`, oldest first.

    The window is `days` consecutive days beginning with the day of the earliest attempt, and
    each frame is the state at 23:59:59.999999 UTC on that day. Days on which nothing was
    attempted still get a frame; they are identical to the previous one, which is what makes the
    UI animation play at a constant rate instead of jumping over quiet weeks.

    This is the history view that the two things this module exists for both consume: the UI
    animating a student's graph filling in over time, and offline retuning, where the same log is
    replayed under changed constants and the two frame sequences are compared day by day. Note
    that frames are produced by a SINGLE forward fold, not by re-replaying the log once per day,
    so the cost is the same as one `replay`.

    Returns `[]` for an empty log or `days <= 0`: with no attempts there is no anchor date, and
    inventing one would mean reading the clock, which this engine never does.
    """
    if days <= 0:
        return []

    ordered = _ordered(attempts)
    if not ordered:
        return []

    start_day = ordered[0].ts.astimezone(timezone.utc).date()
    states = _fresh_states(graph)
    frames: list[tuple[datetime, dict[str, NodeState]]] = []

    index = 0
    for offset in range(days):
        boundary = _end_of_day(start_day + timedelta(days=offset))
        while index < len(ordered) and ordered[index].ts <= boundary:
            attempt = ordered[index]
            states = apply_attempt(states, graph, attempt, attempt.ts)
            index += 1
        frames.append((boundary, _with_all_graph_nodes(states, graph)))

    return frames


# --------------------------------------------------------------------------- helpers

def _fresh_states(graph: Graph) -> dict[str, NodeState]:
    """Honest priors for every node in the graph."""
    return {node_id: NodeState(node_id=node_id) for node_id in graph.nodes}


def _with_all_graph_nodes(
    states: dict[str, NodeState],
    graph: Graph,
) -> dict[str, NodeState]:
    """A copy of `states` guaranteed to cover the whole graph.

    Also the defensive copy that stops frames from aliasing the running fold. Nodes that are not
    in the graph (an attempt against a retired node id) are kept rather than dropped: losing them
    would make a replay silently disagree with the log it came from.
    """
    complete = dict(states)
    for node_id in graph.nodes:
        if node_id not in complete:
            complete[node_id] = NodeState(node_id=node_id)
    return complete


def _ordered(
    attempts: Iterable[Attempt],
    upto: Optional[datetime] = None,
) -> list[Attempt]:
    """Attempts at or before `upto`, sorted by (ts, attempt_id)."""
    normalised = [_utc_ts(a) for a in attempts]
    if upto is not None:
        cutoff = upto if upto.tzinfo else upto.replace(tzinfo=timezone.utc)
        normalised = [a for a in normalised if a.ts <= cutoff]
    return sorted(normalised, key=lambda a: (a.ts, a.attempt_id))


def _utc_ts(attempt: Attempt) -> Attempt:
    """Treat a naive timestamp as UTC.

    Postgres `timestamptz` always comes back aware, but a hand-built fixture or a CSV import
    easily does not, and mixing the two raises inside the sort rather than at the boundary.
    Normalising here means the failure mode is a clearly documented assumption instead of a
    TypeError three frames deep.
    """
    if attempt.ts.tzinfo is not None:
        return attempt
    return _dc_replace(attempt, ts=attempt.ts.replace(tzinfo=timezone.utc))


def _end_of_day(day: date) -> datetime:
    """The last representable instant of `day` in UTC, so a frame includes that whole day."""
    return datetime(day.year, day.month, day.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
