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
BLAME_MAX_B_DELTA = 2.0         # hard ceiling on one blame update
BLAME_STABILITY_FACTOR = 0.5    # S <- max(S * this, STABILITY_MIN_DAYS)

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


@dataclass
class Graph:
    """The knowledge graph. Adjacency is precomputed because selection queries it constantly."""
    nodes: dict[str, dict] = field(default_factory=dict)
    target_node: Optional[str] = None

    def prereqs(self, node_id: str) -> list[tuple[str, float]]:
        return [(p["id"], p.get("weight", 1.0))
                for p in self.nodes.get(node_id, {}).get("prereqs", [])]

    def encompasses(self, node_id: str) -> list[tuple[str, float]]:
        return [(e["id"], e.get("credit", IMPLICIT_CREDIT_DEFAULT))
                for e in self.nodes.get(node_id, {}).get("encompasses", [])]

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
