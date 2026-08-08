"""Mastery state: retrievability, status derivation, and the four update rules.

Everything in this module is a pure function. No I/O, no globals, no clocks. `now` is always an
explicit argument and every update returns a NEW `states` dict; the input dict and the
`NodeState` objects inside it are never mutated. That is what makes replay work: `NodeState` is
derived by folding `apply_attempt` over the append-only attempt log, so retuning any constant in
`types.py` is a matter of replaying the log, not migrating stored state.

The four rules, per learning-design.md section 4.3:

  1. `apply_direct`          evidence from the item the student actually attempted
  2. `apply_implicit`        FIRe credit rippling down to component skills the solution used
  3. `apply_blame`           the backward pass, routing a failure to its true cause
  4. `apply_consolidation`   spaced-repetition stability growth and collapse

`apply_attempt` is the orchestrator that sequences them for one `Attempt`. The sequencing is not
incidental; see the comments there, especially the blame-discount rule.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator, Optional

from engine.types import (
    BLAME_MAX_B_DELTA,
    BLAME_STABILITY_FACTOR,
    DEFAULT_CONFIG,
    DUE_RETRIEVABILITY,
    LAMBDA_BLAME,
    LEARNING_FLOOR,
    MASTERED_MIN_SUCCESSES,
    MASTERED_P,
    PREREQ_READY,
    STABILITY_GROWTH,
    STABILITY_MAX_DAYS,
    STABILITY_MIN_DAYS,
    Attempt,
    Graph,
    MasteryConfig,
    NodeState,
    clamp_credit,
    days_between,
)

# How much of the direct negative evidence survives when the failure was blamed on a DIFFERENT
# node. See `apply_attempt` step 4 for why this exists at all.
BLAME_DISCOUNT_KEEP = 0.25

STATUS_LOCKED = "locked"
STATUS_FRONTIER = "frontier"
STATUS_LEARNING = "learning"
STATUS_MASTERED = "mastered"

# The status ladder, as a total order. This is what "one status boundary" is measured in, and it
# is the only place the ordering of the four statuses is written down.
STATUS_ORDER: tuple[str, ...] = (STATUS_LOCKED, STATUS_FRONTIER, STATUS_LEARNING, STATUS_MASTERED)
STATUS_RANK: dict[str, int] = {name: i for i, name in enumerate(STATUS_ORDER)}

# The minimum own-`p` each rung requires. `locked` and `frontier` have no floor (locked is decided
# by prereqs, frontier is the bottom of the own-mastery ladder), so no amount of blame can push a
# node below them.
_MIN_P_FOR_RANK: dict[int, float] = {
    STATUS_RANK[STATUS_LOCKED]: 0.0,
    STATUS_RANK[STATUS_FRONTIER]: 0.0,
    STATUS_RANK[STATUS_LEARNING]: LEARNING_FLOOR,
    STATUS_RANK[STATUS_MASTERED]: MASTERED_P,
}


# --------------------------------------------------------------------------- behaviour switch

_ACTIVE_CONFIG: MasteryConfig = DEFAULT_CONFIG


def active_config() -> MasteryConfig:
    """The config used when a caller does not pass one explicitly."""
    return _ACTIVE_CONFIG


def _cfg(config: Optional[MasteryConfig]) -> MasteryConfig:
    return config if config is not None else _ACTIVE_CONFIG


@contextmanager
def using(config: MasteryConfig) -> Iterator[MasteryConfig]:
    """Run a block with `config` as the default behaviour.

    Every rule below also takes an explicit `config=` argument, which is the honest way to inject
    one. This context manager exists for the code path where that is not available: `replay.py`
    and `selection.py` call `apply_attempt` without threading a config through, and
    `scripts/tune/replay_compare.py` has to replay the SAME log through those exact functions
    under both behaviours to compare them. Swapping the default around that call is preferable to
    maintaining a second copy of the replay loop that could quietly drift from the real one.

    Single-threaded use only, and restores the previous default even if the block raises.
    """
    global _ACTIVE_CONFIG
    previous = _ACTIVE_CONFIG
    _ACTIVE_CONFIG = config
    try:
        yield config
    finally:
        _ACTIVE_CONFIG = previous


# --------------------------------------------------------------------------- state access

def get_state(states: dict[str, NodeState], node_id: str) -> NodeState:
    """The state for `node_id`, or a fresh honest-prior state if we have never seen it.

    A node the student has no evidence on is a=b=1, p=0.5. That is deliberately below
    PREREQ_READY, so an unseen prereq locks its dependants rather than silently unlocking them.
    """
    existing = states.get(node_id)
    return existing if existing is not None else NodeState(node_id=node_id)


def _put(states: dict[str, NodeState], state: NodeState) -> dict[str, NodeState]:
    """Return a new dict with `state` swapped in. Never mutates the caller's dict."""
    new_states = dict(states)
    new_states[state.node_id] = state
    return new_states


# --------------------------------------------------------------------------- retrievability

def retrievability(state: NodeState, now: datetime) -> float:
    """R = exp(-elapsed_days / stability). 1.0 for a node that has never been seen.

    A never-seen node is not "forgotten", it is simply unmeasured, so R must not penalise it;
    p already carries the uncertainty. Time running backwards (now < last_seen) is clamped to
    R = 1.0 rather than allowed to exceed 1.
    """
    if state.last_seen is None:
        return 1.0
    elapsed = days_between(state.last_seen, now)
    if elapsed <= 0.0:
        return 1.0
    stability = max(state.stability, STABILITY_MIN_DAYS)
    return math.exp(-elapsed / stability)


def p_eff(state: NodeState, now: datetime) -> float:
    """Effective mastery: what we believe the student can retrieve right now."""
    return state.p * retrievability(state, now)


def is_due(state: NodeState, now: datetime) -> bool:
    """Due for review: memory has decayed past the threshold.

    A never-seen node is NOT due. It has never been learned, so it belongs to the frontier/new
    pipeline, not to review.
    """
    if state.last_seen is None:
        return False
    return retrievability(state, now) < DUE_RETRIEVABILITY


# --------------------------------------------------------------------------- status

def is_mastered(state: NodeState) -> bool:
    return state.p >= MASTERED_P and state.successes >= MASTERED_MIN_SUCCESSES


def status(node_id: str, graph: Graph, states: dict[str, NodeState], now: datetime) -> str:
    """Derive one of locked | frontier | learning | mastered. Never stored.

    Order of checks is meaningful: locked wins over everything, because a node whose foundations
    have decayed is not safe to serve regardless of what its own p says. A node with no prereqs
    can never be locked, which is what makes the root layer of the graph always reachable.

    Prereq weights are deliberately ignored here: the rule is "any prereq below PREREQ_READY",
    per learning-design.md section 4.2. Weight is used for ranking blockers, not for gating.
    """
    state = get_state(states, node_id)

    for prereq_id, _weight in graph.prereqs(node_id):
        if p_eff(get_state(states, prereq_id), now) < PREREQ_READY:
            return STATUS_LOCKED

    if is_mastered(state):
        return STATUS_MASTERED
    if state.p >= LEARNING_FLOOR:
        return STATUS_LEARNING
    return STATUS_FRONTIER


# --------------------------------------------------------------------------- rule 1: direct

def apply_direct(
    states: dict[str, NodeState],
    node_id: str,
    correct: bool,
    weight: float,
    now: datetime,
) -> dict[str, NodeState]:
    """Beta update from the item the student actually attempted.

    a += w*correct, b += w*(1-correct). `weight` is the evidence quality (channel and hint
    penalty), so a photo of full working moves mastery more than an MCQ guess.

    This is the only rule that stamps `last_seen`, because it is the only one that corresponds to
    the student genuinely retrieving this node under test conditions.
    """
    state = get_state(states, node_id)
    w = max(0.0, float(weight))
    c = 1.0 if correct else 0.0
    return _put(states, state.with_(
        a=state.a + w * c,
        b=state.b + w * (1.0 - c),
        last_seen=now,
    ))


# --------------------------------------------------------------------------- rule 2: implicit

def credited_nodes(
    graph: Graph,
    node_id: str,
    used_nodes: Iterable[str],
    config: Optional[MasteryConfig] = None,
) -> dict[str, float]:
    """Which nodes one correct task on `node_id` credits, and at what fraction: `{node: credit}`.

    Two hops with very different rules, and the split is the whole design decision here.

    FIRST HOP, gated on `used_nodes`. A direct child of `node_id` is credited only if it is in
    BOTH `graph.encompasses(node_id)` (the solution *could* have used it) and `used_nodes` (the
    diagnosis says it *did*). Crediting everything a node encompasses would inflate skills the
    student happened to route around.

    BELOW THE FIRST HOP, ungated: credit propagates through `graph.implied_credit`, multiplying
    along each path and taking the max across paths.

    Why the gate stops after one hop. `used_nodes` comes from the diagnosis, which reads the
    student's WORKING. A grandchild skill is by construction not visible there: a student who
    writes `d/dx (3x^5) = 15x^4` exercises the exponent rules, but nothing in that line says so,
    and the diagnosis will never report it. Filtering every hop on `used_nodes` would therefore
    kill transitive credit outright, since no transitively reached node is ever in the list. The
    honest reading is that the diagnosis can only report the skills the working makes visible, and
    that once a first-hop skill is confirmed used, what THAT skill is built from was used too.

    The result is always a subset of `{first hop in used_nodes} union {their descendants}`, so the
    existing guarantee still holds in the form that matters: credit only reaches nodes reachable
    from a skill the diagnosis actually reported. `node_id` itself is never credited; the explicit
    repetition is rule 1's job.

    With `config.transitive_credit` off this degrades to exactly the old single-hop behaviour,
    which is what makes the two comparable on one replay.
    """
    used = set(used_nodes or ())
    if not used:
        return {}

    cfg = _cfg(config)
    credit: dict[str, float] = {}

    for child_id, raw in graph.encompasses(node_id):
        if child_id not in used:
            continue
        first_hop = clamp_credit(raw)
        if first_hop <= 0.0:
            continue
        if first_hop > credit.get(child_id, 0.0):
            credit[child_id] = first_hop
        if not cfg.transitive_credit:
            continue
        for deep_id, deep_credit in graph.implied_credit(child_id).items():
            value = first_hop * deep_credit
            if value > credit.get(deep_id, 0.0):
                credit[deep_id] = value

    # The attempted node takes explicit evidence in rule 1; a cyclic authoring mistake in the
    # encompassing graph must not let it collect implicit credit on top of that.
    credit.pop(node_id, None)
    return credit


def apply_implicit(
    states: dict[str, NodeState],
    graph: Graph,
    node_id: str,
    weight: float,
    used_nodes: Iterable[str],
    now: datetime,
    config: Optional[MasteryConfig] = None,
) -> dict[str, NodeState]:
    """FIRe credit down to the component skills the solution actually exercised.

    `credited_nodes` decides WHO gets credit and HOW MUCH, transitively through the encompassing
    DAG. This function applies it, and everything it does per credited node is unchanged from the
    single-hop version:

      - `last_seen` IS advanced to `now`. The skill was genuinely exercised, so it must not decay
        as though it were not. Without this, a student doing chain-rule problems every day would
        watch the power rule rot until its p_eff fell under PREREQ_READY and silently LOCKED the
        chain rule, a topic they are actively practising. That failure is invisible: nothing
        errors, the frontier just quietly closes. It is also the whole point of FIRe, where a
        hard problem is supposed to knock out the due reviews on its component skills.
      - `successes` is NOT incremented and `stability` is NOT grown. Keeping a node warm is not
        the same as proving you own it. The mastered gate requires MASTERED_MIN_SUCCESSES spaced
        retrievals of the skill ITSELF, and implicit credit alone must never reach it. Nor should
        an implicit rep push the review interval further out, because the skill was never tested
        in isolation.
      - the mastery credit stays hard-discounted by `credit` from the graph, and transitive credit
        is discounted further at every hop: it is a product of weights in (0, 1], so a grandchild
        can never be credited more than its parent was.

    Only ever called for correct attempts; incorrect work carries no credit downward, it carries
    blame, which is rule 3.

    Iteration is in sorted node order so replay stays deterministic regardless of dict ordering.
    """
    credit = credited_nodes(graph, node_id, used_nodes, config)
    if not credit:
        return states

    w = max(0.0, float(weight))
    new_states = states
    for child_id in sorted(credit):
        child = get_state(new_states, child_id)
        new_states = _put(new_states, child.with_(
            a=child.a + credit[child_id] * w,
            last_seen=now,          # schedule: keep it warm. Mastery gate: untouched below.
        ))
    return new_states


# --------------------------------------------------------------------------- rule 3: blame

@dataclass(frozen=True)
class BlameDelta:
    """How big one blame's `b` increment came out, and what limited it.

    Returned rather than just the number so tuning can see WHICH bound bound. A cap nobody can
    observe engaging is how BLAME_MAX_B_DELTA stayed inert for so long.
    """
    requested: float          # LAMBDA_BLAME * confidence, before any limit
    ceiling: float            # BLAME_MAX_B_DELTA, the absolute backstop
    boundary: float           # largest delta that keeps the one-boundary property (inf = free)
    applied: float            # what actually lands on b
    pre_status: str           # the blamed node's status before the blame
    floor_status: str         # the status the blame is not allowed to push it below

    @property
    def bound_by(self) -> str:
        """"none" | "ceiling" | "boundary", naming the binding constraint."""
        if self.applied >= self.requested - 1e-12:
            return "none"
        return "boundary" if self.boundary <= self.ceiling else "ceiling"


def _one_boundary_limit(
    state: NodeState,
    graph: Graph,
    states: dict[str, NodeState],
    now: datetime,
) -> tuple[float, str, str]:
    """Largest `b` increment that leaves the node at most one status boundary lower.

    Returns `(limit, pre_status, floor_status)`, with `limit = inf` when nothing constrains it.

    Two facts do the work. First, `apply_blame` always resets `successes`, so a blamed node can
    never come out `mastered`: the demotion from `mastered` to `learning` is spent before `b`
    moves at all, and the increment's whole budget is "do not spend the SECOND boundary too".
    Second, the blame touches only the blamed node, so its prereqs (and therefore whether it is
    `locked`) are exactly as they were.

    Given a floor status with a minimum `p`, the algebra is one line:
        a / (a + b + d) >= min_p   <=>   d <= a/min_p - a - b
    """
    pre_status = status(state.node_id, graph, states, now)
    floor_rank = max(STATUS_RANK[STATUS_LOCKED], STATUS_RANK[pre_status] - 1)
    # A blamed node cannot be mastered afterwards whatever we do to b, so a `mastered` floor is
    # not a constraint we can honour with the increment; the binding rung is `learning`.
    floor_rank = min(floor_rank, STATUS_RANK[STATUS_LEARNING])
    floor_status = STATUS_ORDER[floor_rank]

    min_p = _MIN_P_FOR_RANK[floor_rank]
    if min_p <= 0.0:
        return math.inf, pre_status, floor_status
    return max(0.0, state.a / min_p - state.a - state.b), pre_status, floor_status


def blame_delta(
    states: dict[str, NodeState],
    graph: Graph,
    blamed_node: str,
    confidence: float,
    now: datetime,
    config: Optional[MasteryConfig] = None,
) -> BlameDelta:
    """The `b` increment for one blame, and the reasoning behind its size.

    THE PROPERTY: **one blame moves a node down by at most one status boundary.**

    `mastered` may fall to `learning`; `learning` may fall to `frontier`; `frontier` and `locked`
    are already the bottom of the ladder and are unconstrained. What a single blame may never do
    is skip a rung, so a student who was solid on a skill this morning is at worst "still learning
    it" tonight, never "back at the frontier", on the strength of one diagnosis.

    Why a property and not a number. BLAME_MAX_B_DELTA = 2.0 was supposed to be the mitigation for
    experiment 2 finding 3, that the model's stated confidence is NOT separable between right and
    wrong diagnoses (0.97 vs 0.93) and so cannot gate anything. It never mitigated anything: at
    the observed confidence range `LAMBDA_BLAME * confidence` is about 1.4, the 2.0 ceiling never
    engaged, and the 25% blame discount was silently doing all of the protective work. A fixed
    ceiling also cannot do the job in principle, because the damage one unit of `b` does depends
    entirely on how much evidence the node already carries: 1.4 is a scratch on a node with 40
    reps and a demolition on a node with 3. A boundary is scale-free and, unlike 2.0, is a rule
    a person can reason about and a test can state.

    BLAME_MAX_B_DELTA survives as an absolute backstop for the case the boundary rule cannot see:
    a `frontier` node has no rung below it, so nothing else would bound a runaway confidence.
    """
    cfg = _cfg(config)
    state = get_state(states, blamed_node)

    requested = LAMBDA_BLAME * max(0.0, float(confidence))
    boundary, pre_status, floor_status = (
        _one_boundary_limit(state, graph, states, now)
        if cfg.boundary_blame_cap
        else (math.inf, status(state.node_id, graph, states, now), STATUS_LOCKED)
    )
    applied = min(requested, BLAME_MAX_B_DELTA, boundary)
    return BlameDelta(
        requested=requested,
        ceiling=BLAME_MAX_B_DELTA,
        boundary=boundary,
        applied=applied,
        pre_status=pre_status,
        floor_status=floor_status,
    )


def apply_blame(
    states: dict[str, NodeState],
    graph: Graph,
    blamed_node: str,
    confidence: float,
    tag: Optional[str],
    now: datetime,
    config: Optional[MasteryConfig] = None,
) -> dict[str, NodeState]:
    """The backward pass: route a failure to the node that actually caused it.

    `b += blame_delta(...).applied`, which is `LAMBDA_BLAME * confidence` limited so that **one
    blame moves the node down by at most one status boundary**, with BLAME_MAX_B_DELTA as an
    absolute backstop. See `blame_delta` for why the cap is a property rather than a constant.

    Stability collapses (interval halves, floored at STABILITY_MIN_DAYS) so the node comes back
    soon, the misconception tag is recorded for the remediation slot, and the spaced-success
    counter resets: a node with a live misconception has no business being called mastered.

    Note what the boundary rule does NOT cover. It bounds the blamed node's own status. A blamed
    node is still free to drag its DEPENDANTS from frontier to locked, because that cascade runs
    on `p_eff = p * R` and the stability collapse above cuts `R` hard, which no bound on `b` can
    undo. Softening that is a separate change to BLAME_STABILITY_FACTOR, not to this cap.

    `graph` is used to derive the blamed node's status, which is what "one boundary" is measured
    against; blame still lands on exactly one node.
    """
    state = get_state(states, blamed_node)

    delta_b = blame_delta(states, graph, blamed_node, confidence, now, config).applied

    misconceptions = state.misconceptions
    if tag and tag not in misconceptions:
        misconceptions = misconceptions + (tag,)

    return _put(states, state.with_(
        b=state.b + delta_b,
        stability=max(state.stability * BLAME_STABILITY_FACTOR, STABILITY_MIN_DAYS),
        misconceptions=misconceptions,
        successes=0,
    ))


# --------------------------------------------------------------------------- rule 4: consolidation

def apply_consolidation(
    states: dict[str, NodeState],
    node_id: str,
    correct: bool,
    now: datetime,
    was_due: Optional[bool] = None,
) -> dict[str, NodeState]:
    """Spaced-repetition stability: grows multiplicatively on success, halves on failure.

    On a correct attempt, stability only grows if the retrieval was a genuine spaced one, that
    is, the node was due before the attempt, or had never been seen. Answering the same node
    three times in a row within one session must not triple the review interval, and for the same
    reason `successes` (the mastered gate) only increments on a genuine spaced success.

    `was_due` must be computed BEFORE any other rule runs, because `apply_direct` stamps
    `last_seen = now`, after which `is_due` is trivially false. Callers that have already applied
    direct evidence therefore have to pass it in; `apply_attempt` does exactly that. When it is
    omitted we fall back to reading it off the current state, which is only correct if nothing
    has touched the node yet this attempt.

    Stability is clamped to [STABILITY_MIN_DAYS, STABILITY_MAX_DAYS] on every path.
    """
    state = get_state(states, node_id)

    if not correct:
        return _put(states, state.with_(
            stability=max(state.stability / 2.0, STABILITY_MIN_DAYS),
        ))

    if was_due is None:
        was_due = state.last_seen is None or is_due(state, now)

    if not was_due:
        return _put(states, state)

    grown = min(state.stability * (1.0 + STABILITY_GROWTH), STABILITY_MAX_DAYS)
    return _put(states, state.with_(
        stability=max(grown, STABILITY_MIN_DAYS),
        successes=state.successes + 1,
    ))


# --------------------------------------------------------------------------- misconceptions

def clear_misconception(
    states: dict[str, NodeState],
    node_id: str,
    tag: Optional[str] = None,
) -> dict[str, NodeState]:
    """Retire an open misconception once the student has demonstrably shed it.

    If `tag` is given and open, that one is cleared. Otherwise the oldest open misconception is
    cleared: one clean unhinted success is evidence for one misconception, not amnesty for all of
    them. Order is preserved so the remediation slot still fires oldest-first.
    """
    state = get_state(states, node_id)
    if not state.misconceptions:
        return states

    if tag is not None:
        if tag not in state.misconceptions:
            return states
        remaining = tuple(t for t in state.misconceptions if t != tag)
    else:
        remaining = state.misconceptions[1:]

    return _put(states, state.with_(misconceptions=remaining))


# --------------------------------------------------------------------------- orchestrator

def apply_attempt(
    states: dict[str, NodeState],
    graph: Graph,
    attempt: Attempt,
    now: Optional[datetime] = None,
    config: Optional[MasteryConfig] = None,
) -> dict[str, NodeState]:
    """Fold one attempt into state. Pure: returns a new dict, mutates nothing.

    Sequencing, and why each step sits where it does:

      1. Snapshot `was_due` for the attempted node BEFORE anything changes. Step 2 stamps
         `last_seen`, which destroys the information consolidation needs.
      2. Direct evidence on `attempt.node_id`, weighted by `attempt.quality`.
      3. On a correct attempt: implicit credit to the component skills the solution used, then
         consolidation (stability growth plus the spaced-success counter).
      4. On an incorrect attempt with a diagnosis: blame the node the diagnosis names, and
         DISCOUNT the direct negative evidence if that node is not the one attempted.
      5. A correct answer at hint_level 0 retires an open misconception on the attempted node.

    Step 4's discount is the crux of the whole product. If a student fails a quotient-rule problem
    because they botched -(3x - 2), the quotient rule itself is not the thing they got wrong.
    Marking them down on it would send them to re-practise a rule they already understand while
    the actual gap, sign distribution, stays invisible. So when the blame lands elsewhere we undo
    most of the b that step 2 added to the attempted node, keeping only BLAME_DISCOUNT_KEEP
    (25%) of it. Not zero: the diagnosis is roughly 83% accurate (section 14), so a small residue
    is the honest hedge against a misrouted blame, and it also reflects that the student did fail
    to carry the problem to the end. The full-strength penalty goes to the blamed node instead.

    Uses `attempt.ts` when `now` is not supplied, and `active_config()` when `config` is not.
    """
    if now is None:
        now = attempt.ts
    cfg = _cfg(config)

    node_id = attempt.node_id
    weight = attempt.quality

    # 1. Snapshot before anything moves.
    before = get_state(states, node_id)
    was_due = before.last_seen is None or is_due(before, now)

    # 2. Direct evidence.
    new_states = apply_direct(states, node_id, attempt.correct, weight, now)

    if attempt.correct:
        # 3. Credit down, then consolidate up.
        new_states = apply_implicit(
            new_states, graph, node_id, weight, attempt.used_nodes, now, config=cfg
        )
        new_states = apply_consolidation(
            new_states, node_id, True, now, was_due=was_due
        )
    else:
        blamed = attempt.blamed_node
        if blamed:
            new_states = apply_blame(
                new_states, graph, blamed, attempt.blame_confidence,
                attempt.misconception_tag, now, config=cfg,
            )
            if blamed != node_id:
                # Route the failure to its true cause: give back most of the b step 2 added.
                attempted = get_state(new_states, node_id)
                give_back = weight * (1.0 - BLAME_DISCOUNT_KEEP)
                new_states = _put(new_states, attempted.with_(
                    b=max(1.0, attempted.b - give_back),
                ))
                # No stability collapse on the attempted node either. The student retrieved it
                # fine; only the blamed node's interval should snap shut, which apply_blame
                # already did.
            # blamed == node_id: apply_blame has already halved stability, so calling
            # consolidation here would halve it twice for a single failure.
        else:
            # Undiagnosed failure. We cannot route it, so the attempted node takes the hit and
            # its review interval collapses.
            new_states = apply_consolidation(new_states, node_id, False, now)

    # 5. A clean, unhinted correct answer is the evidence that a misconception is gone.
    if attempt.correct and attempt.hint_level == 0:
        new_states = clear_misconception(new_states, node_id, attempt.misconception_tag)

    return new_states
