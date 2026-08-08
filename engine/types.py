"""Shared types and tuning constants for the Open Tutor engine.

This module is the contract every other engine module builds against. It owns the data shapes and
the constants; it owns no behaviour. Nothing here does I/O.

Design rules that the rest of the engine must respect:

  - `Attempt` records are the source of truth and are append-only. `NodeState` is DERIVED by
    replaying attempts, never mutated in place and stored. We will retune LAMBDA_BLAME, BETA_*
    and the thresholds repeatedly against real user data, and replay is the only way to tell
    whether a change helped. See learning-design.md section 12.1.
  - Every function that changes state takes and returns state. No hidden mutation.
  - Time is always an explicit argument, never `now()` read from inside. Otherwise nothing is
    testable and the seeded demo history cannot be constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

# --------------------------------------------------------------------------- tuning

# Mastery thresholds. Status is always derived from these, never stored.
PREREQ_READY = 0.70      # a prereq counts as satisfied at or above this p_eff
LEARNING_FLOOR = 0.70    # own p at or above this leaves the frontier
MASTERED_P = 0.90        # plus MASTERED_MIN_SUCCESSES spaced successes
MASTERED_MIN_SUCCESSES = 3

# Spaced repetition. R = exp(-elapsed_days / stability).
DUE_RETRIEVABILITY = 0.85
STABILITY_INITIAL_DAYS = 1.0
STABILITY_GROWTH = 0.9          # on a spaced success: S <- S * (1 + STABILITY_GROWTH)
STABILITY_MIN_DAYS = 1.0
STABILITY_MAX_DAYS = 365.0

# Evidence quality by how the answer arrived. A photo of complete working is the richest signal
# we get and an MCQ is the weakest, so they must not move mastery by the same amount.
QUALITY_PHOTO = 1.0
QUALITY_TYPED = 0.7
QUALITY_MCQ = 0.4
HINT_PENALTY = 0.25             # weight multiplier is (1 - HINT_PENALTY * hint_level)

# Implicit credit rippling down to component skills (FIRe). Discounted hard: an implicit rep is
# worth less than an explicit one and often arrives too early to count as a proper repetition.
IMPLICIT_CREDIT_DEFAULT = 0.25

# Blame propagation. Blame hits harder than credit, but is capped: experiment 2b showed the
# model's confidence is NOT separable between right and wrong answers, so confidence cannot gate
# anything and a single blame must never be able to wipe out a node.
LAMBDA_BLAME = 1.5
BLAME_MAX_B_DELTA = 2.0         # absolute backstop on one blame update, see the note below
BLAME_STABILITY_FACTOR = 0.5    # S <- max(S * this, STABILITY_MIN_DAYS)

# BLAME_MAX_B_DELTA is a BACKSTOP, not the working cap. At the model's observed confidence
# (0.93 to 0.97) LAMBDA_BLAME * confidence lands near 1.4, so a fixed ceiling of 2.0 never
# engages and the protective work falls entirely to the 25% blame discount in `mastery.py`.
# The cap that actually binds is a PROPERTY, not a magic number: one blame may move a node down
# by at most one status boundary. See `engine.mastery.blame_delta`.

# Item selection.
TARGET_SUCCESS_RATE = 0.85      # pick the item whose predicted success is nearest this
DAILY_SET_SIZE = 6

# Maximum slots per kind. These sum to DAILY_SET_SIZE exactly, so a maximally busy day (two
# blockers) still carries its goal link. `new` expands into any unused blocker slots, which is
# the intended trade: a day with two things blocking you is a day you advance less.
# goal_link is RESERVED, not filled last. A student who never sees why they are doing this is a
# student who quits, so it must survive the busiest day.
SET_BUDGET = {"blocker": 2, "review": 2, "new": 1, "goal_link": 1}
NEW_SLOT_MAX = 2                # `new` may grow to this when blockers do not use their slots

# XP. Grinding must be worth almost nothing, which is what makes a leaderboard safe later.
XP_BASE = 10
NOVELTY_NEW = 3.0
NOVELTY_REVIEW = 1.0
NOVELTY_OVERPRACTICE = 0.15
QUALITY_XP_CLEAN = 1.0
QUALITY_XP_HINTED = 0.6
QUALITY_XP_REVEALED = 0.4


# --------------------------------------------------------------------------- behaviour switch

@dataclass(frozen=True)
class MasteryConfig:
    """Which VARIANT of the update rules to run.

    This exists for exactly one reason: the event log is only useful as a tuning instrument if the
    same log can be replayed under the old and the new behaviour and the two results compared.
    A module-level constant cannot do that, because both variants have to run in one process.
    So every behaviour change that is still being argued about lands here as a flag, gets measured
    by `scripts/tune/replay_compare.py`, and only then becomes unconditional.

    A flag's DEFAULT is what the engine ships. A flag defaulting to False is not dead code: it is
    a behaviour that is implemented, tested and measured, and whose measurement said no.
    `LEGACY_CONFIG` is the behaviour from before any of this, kept runnable so "is this an
    improvement" stays a question we answer with numbers rather than opinion.
    """

    # OFF, and the measurement is the reason. Transitive credit (core_engine.md 2.2, engine.md
    # section 8) is implemented and correct: multiply along a path, max across paths, gated on
    # `used_nodes` at the first hop only. Replayed against data/demo/history.json it buys nothing
    # and costs the demo. It moves no node's status and no daily-set slot, but it stamps
    # `last_seen` on nodes several hops up, and a node whose `last_seen` was just refreshed is not
    # DUE, so its next retrieval is not a spaced one, so it earns no `successes` and no stability
    # growth. On the seeded week one attempt (a0207, a correct der.slope-interpretation) now
    # credits alg.fraction-arithmetic and alg.sign-distribution two hops down, nine hours before
    # the evening drills on those exact nodes. Both lose a spaced success, stability falls 6.52 to
    # 3.43 days, and p_eff on alg.fraction-arithmetic drops from 0.763 to 0.703 against a
    # PREREQ_READY gate of 0.70. One further day of decay, which is exactly what the API's
    # day-offset applies, pushes it to 0.694 and LOCKS der.quotient-rule, der.definition and
    # lim.indeterminate-factoring. That is the demo's climax node, locked.
    # Flip it on with `MasteryConfig(transitive_credit=True)` and re-measure with
    # `scripts/tune/replay_compare.py`; it should ship only alongside a fix for the
    # credit-suppresses-spacing interaction, which is a change to rule 4, not to rule 2.
    transitive_credit: bool = False    # implicit credit walks the whole encompassing DAG

    # ON. Costs nothing measurable (it changes no number on the seeded history) and replaces an
    # arbitrary constant with a property a person can reason about. See `mastery.blame_delta`.
    boundary_blame_cap: bool = True    # one blame moves a node at most one status boundary


DEFAULT_CONFIG = MasteryConfig()
LEGACY_CONFIG = MasteryConfig(transitive_credit=False, boundary_blame_cap=False)
ALL_ON_CONFIG = MasteryConfig(transitive_credit=True, boundary_blame_cap=True)


# --------------------------------------------------------------------------- data

@dataclass(frozen=True)
class NodeState:
    """Derived mastery state for one student on one node. Never stored as truth."""
    node_id: str
    a: float = 1.0                       # Beta posterior; a=b=1 is an honest "unknown"
    b: float = 1.0
    stability: float = STABILITY_INITIAL_DAYS
    last_seen: Optional[datetime] = None
    successes: int = 0                   # spaced successes, for the mastered gate
    misconceptions: tuple[str, ...] = ()

    @property
    def p(self) -> float:
        return self.a / (self.a + self.b)

    @property
    def evidence(self) -> float:
        """Total weight of evidence seen. a+b starts at 2 from the prior."""
        return self.a + self.b - 2.0

    def with_(self, **kw) -> "NodeState":
        return replace(self, **kw)


@dataclass(frozen=True)
class Attempt:
    """One immutable event. The append-only log these are drawn from is the source of truth."""
    attempt_id: str
    student_id: str
    item_id: str
    node_id: str                          # the node the ITEM is tagged to
    ts: datetime
    correct: bool
    hint_level: int = 0                   # 0..4, see the hint ladder
    channel: str = "typed"                # photo | typed | mcq
    used_nodes: tuple[str, ...] = ()      # component skills the solution actually exercised
    blamed_node: Optional[str] = None     # from diagnosis; may differ from node_id
    blame_confidence: float = 0.0
    misconception_tag: Optional[str] = None

    @property
    def quality(self) -> float:
        base = {"photo": QUALITY_PHOTO, "typed": QUALITY_TYPED,
                "mcq": QUALITY_MCQ}.get(self.channel, QUALITY_TYPED)
        return max(0.0, base * (1.0 - HINT_PENALTY * self.hint_level))


@dataclass(frozen=True)
class Item:
    item_id: str
    node_id: str
    stem_latex: str
    answer_latex: Optional[str] = None
    answer_sympy: Optional[str] = None
    answer_kind: Optional[str] = None     # expr | set | vector
    difficulty_b: float = 0.0
    encompasses: tuple[str, ...] = ()
    source: str = "openstax"              # openstax (CC BY-NC-SA) | generated (ours)


@dataclass(frozen=True)
class SetEntry:
    """One slot in a daily set, carrying why it was chosen.

    The reason is not debug output. It is shown to the student ("Blocker: sign distribution"),
    which is one of the few places the system's reasoning becomes legible.
    """
    item: Item
    slot: str                             # blocker | review | new | goal_link
    reason: str                           # human-readable, shown in the UI
    predicted_success: float = 0.0


def clamp_credit(weight: float) -> float:
    """Force a credit weight into the axiom's range, (0, 1].

    core_engine.md section 2.2 requires every encompassing weight to lie in (0, 1]. That range is
    what makes composition ATTENUATE: a product of numbers at or below 1 can never exceed the
    smallest of them, so a long path can only ever dilute credit. A weight above 1 would let a
    two-hop path pay more than a one-hop path, which is the one thing transitive propagation must
    never do. A weight at or below 0 is not an edge and is reported as 0 so callers can drop it.
    """
    w = float(weight)
    if w <= 0.0:
        return 0.0
    return min(w, 1.0)


@dataclass
class Graph:
    """The knowledge graph. Adjacency is precomputed because selection queries it constantly."""
    nodes: dict[str, dict] = field(default_factory=dict)
    target_node: Optional[str] = None
    # Memo for `implied_credit`. The graph is static for the whole of a session, so the transitive
    # credit map of a node is a pure function of the graph and worth computing once. Excluded from
    # equality and repr: it is derived, so two graphs that differ only in what has been queried
    # are the same graph.
    _credit_cache: dict[str, dict[str, float]] = field(
        default_factory=dict, repr=False, compare=False)

    def prereqs(self, node_id: str) -> list[tuple[str, float]]:
        return [(p["id"], p.get("weight", 1.0))
                for p in self.nodes.get(node_id, {}).get("prereqs", [])]

    def encompasses(self, node_id: str) -> list[tuple[str, float]]:
        return [(e["id"], e.get("credit", IMPLICIT_CREDIT_DEFAULT))
                for e in self.nodes.get(node_id, {}).get("encompasses", [])]

    def implied_credit(self, node_id: str) -> dict[str, float]:
        """Transitive credit implied by one completed task on `node_id`: `{node: credit}`.

        This is the static half of implicit credit, per core_engine.md section 2.2. Direct
        `encompasses` answers "what does one task on v exercise one level down"; this answers the
        same question all the way down, so a chain-rule task credits the power rule AND the
        exponent rules the power rule is built from.

        Semantics, and the three properties they are chosen to satisfy:

          - MULTIPLY along a path, take the MAX across paths.
          - Every credit stays in (0, 1]. Weights are clamped into (0, 1] first, and a product of
            such weights is in (0, 1].
          - Credit through a single path never exceeds the smallest weight on that path, because
            every other factor is at most 1. Composition therefore attenuates and never amplifies.
          - The map is monotone non-increasing in the edge weights: lowering any weight can only
            lower the products through the paths that use it, and `max` of non-increasing terms is
            non-increasing.

        `node_id` itself is never in the result; the caller is responsible for the explicit
        repetition. Termination does not depend on the graph being acyclic: a node already on the
        current path is skipped, and credit only ever propagates further when it strictly improves
        on the best credit found so far, which is a strictly decreasing quantity.

        Cached per node. The returned dict is a copy, so a caller cannot poison the cache.
        """
        cached = self._credit_cache.get(node_id)
        if cached is None:
            cached = self._compute_implied_credit(node_id)
            self._credit_cache[node_id] = cached
        return dict(cached)

    def _compute_implied_credit(self, node_id: str) -> dict[str, float]:
        credit: dict[str, float] = {}
        stack: list[tuple[str, float, frozenset[str]]] = [(node_id, 1.0, frozenset({node_id}))]
        while stack:
            current, acc, path = stack.pop()
            for child_id, raw in self.encompasses(current):
                weight = clamp_credit(raw)
                if weight <= 0.0 or child_id in path:
                    continue
                value = acc * weight
                if value > credit.get(child_id, 0.0):
                    credit[child_id] = value
                    stack.append((child_id, value, path | {child_id}))
        credit.pop(node_id, None)
        return credit

    def title(self, node_id: str) -> str:
        return self.nodes.get(node_id, {}).get("title", node_id)


# --------------------------------------------------------------------------- helpers

def days_between(a: datetime, b: datetime) -> float:
    """Signed days from a to b. Both must be timezone-aware."""
    return (b - a).total_seconds() / 86400.0


def utc(y: int, m: int, d: int, hh: int = 12) -> datetime:
    """Deterministic timestamp helper, used by tests and the seeded demo history."""
    return datetime(y, m, d, hh, tzinfo=timezone.utc)


def add_days(t: datetime, n: float) -> datetime:
    return t + timedelta(days=n)
