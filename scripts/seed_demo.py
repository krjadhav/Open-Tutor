#!/usr/bin/env python3
"""Generate the seeded 5-day attempt history for the demo student.

Why this file exists
--------------------
A day-1 empty graph demos terribly. The judge has to see a student mid-journey: nodes already
gold, two live blockers, a frontier that is one step away from the problem we are about to solve
on stage. That state is NOT hand-written. It is EARNED by replaying a real append-only attempt
log through `engine.replay.replay`, because "state is derived, never stored" is the property the
whole event-log design exists to guarantee (learning-design.md section 12.1). A hand-written
state file would be a lie that the first retune would expose.

So this script emits attempts, not state. Everything the demo shows falls out of the replay.

The shape of the week
---------------------
Five study days, then the demo runs on the sixth (`ANCHOR_TODAY`).

  day 1  placement plus a long first session. The student already knows most of the algebra,
         trig and limits material and demonstrates it. Sign distribution bites twice.
  day 2  consolidation. Sign distribution bites again inside a quotient-rule problem.
  day 3  limits and the derivative definition. Fraction arithmetic bites.
  day 4  derivative rules. Clean day, which is what lets both blocked skills grow their review
         interval back.
  day 5  the quotient rule fails again on sign distribution, and the student runs the "Fix this"
         drill that evening. That is the most recent occurrence, one day before the demo.

Three engine facts drive every timestamp below, and none of them are cosmetic:

  1. `successes` (the mastered gate) only increments on a SPACED success, i.e. one where
     retrievability had already fallen under DUE_RETRIEVABILITY. So a skill has to be practised
     on separate days, and its block has to sit EARLIER in the day than any problem that would
     hand it implicit credit, because implicit credit stamps `last_seen` and would make the next
     morning's retrieval look un-spaced.
  2. `apply_blame` halves stability. Four blames land on sign distribution, so they are clustered
     at the front of the week: a blame on day 3 or 4 would leave the node with a one-day review
     interval, its p_eff under PREREQ_READY, and der.quotient-rule LOCKED instead of frontier.
     Front-loading is not a fudge, it is the only arrangement in which a student with four slips
     still legitimately reaches the quotient rule.
  3. A correct, unhinted answer RETIRES an open misconception. Every correct attempt on a blocked
     node after that node's last blame therefore carries a hint level, which is also the honest
     story: the student only gets these right with help, which is exactly why the blocker is
     still open.

Run `python3 scripts/seed_demo.py --verify` to replay what was generated and print the achieved
state. Tuning this file means moving counts and re-reading that output, never editing the state.

Output is `data/demo/history.json`, and it is byte-stable: nothing here reads the clock or a
random source.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.mastery import is_due, p_eff, retrievability, status          # noqa: E402
from engine.replay import load_graph, replay                              # noqa: E402
from engine.selection import compose_daily_set, load_items                # noqa: E402
from engine.types import Attempt, Graph, utc                              # noqa: E402

# --------------------------------------------------------------------------- constants

STUDENT_ID = "demo"
HISTORY_DAYS = 5

# The instant the demo runs. Every attempt is placed relative to this, and the API layer offsets
# the whole log when the real "today" is not this date. Explicit and never read from the clock:
# the engine contract forbids it and the demo has to reproduce exactly.
ANCHOR_TODAY = utc(2026, 8, 8, 8)

ITEMS_PATH = ROOT / "data" / "items" / "items.json"
GRAPH_PATH = ROOT / "data" / "graph" / "nodes.json"
HISTORY_PATH = ROOT / "data" / "demo" / "history.json"

MINUTES_PER_PROBLEM = 3

TAG_SIGNS = "sign-distribution"
TAG_FRACTIONS = "fraction-arithmetic"

NODE_SIGNS = "alg.sign-distribution"
NODE_FRACTIONS = "alg.fraction-arithmetic"
NODE_QUOTIENT = "der.quotient-rule"


# --------------------------------------------------------------------------- plan types

@dataclass(frozen=True)
class Drill:
    """A block of attempts on one node, correct ones first.

    Correct first is deliberate: the first attempt of the block is the one that can score a
    spaced success, and a failure ahead of it would stamp `last_seen` and throw that away.
    """
    node: str
    correct: int = 0
    wrong: int = 0
    channel: str = "photo"
    hint: int = 0
    uses: Optional[tuple[str, ...]] = None      # None means "every skill this node encompasses"
    item: Optional[str] = None                  # pin an item; otherwise cycle the node's bank


@dataclass(frozen=True)
class Miss:
    """A diagnosed failure: the student attempted `node` and the blame landed on `blamed`.

    `failed_step` and `corrected_step` are the student's own wrong line and its repair. The
    Blockers card shows them as a struck-through line above the right one, so they are content,
    not metadata.
    """
    node: str
    blamed: str
    confidence: float
    failed_step: str
    corrected_step: str
    message: str
    # Pinned, never cycled. The Blockers card puts `failed_step` on screen as the student's own
    # line, so it has to be the working for THIS problem. A miss whose item drifted would show a
    # wrong line belonging to a problem the student never saw.
    item: str = ""
    tag: Optional[str] = None
    channel: str = "photo"
    hint: int = 0


@dataclass(frozen=True)
class Session:
    """One sitting. Entries are laid out MINUTES_PER_PROBLEM apart, in the order given."""
    day: int                                    # 1..HISTORY_DAYS
    hour: int
    minute: int
    label: str
    entries: tuple = ()


# --------------------------------------------------------------------------- the week

def _sign_miss(node: str, item: str, confidence: float,
               failed: str, corrected: str, message: str) -> Miss:
    return Miss(node=node, item=item, blamed=NODE_SIGNS, confidence=confidence,
                failed_step=failed, corrected_step=corrected, message=message, tag=TAG_SIGNS)


def _fraction_miss(node: str, item: str, confidence: float,
                   failed: str, corrected: str, message: str) -> Miss:
    return Miss(node=node, item=item, blamed=NODE_FRACTIONS, confidence=confidence,
                failed_step=failed, corrected_step=corrected, message=message, tag=TAG_FRACTIONS)


# The four sign-distribution slips. Two on day 1, one on day 2, one on day 5. Clustering them at
# the front is what lets the review interval recover far enough for der.quotient-rule to be
# reachable at all; see the module docstring, point 2.
#
# Every wrong line below is the working for its own pinned item. They are all the same error
# wearing different clothes, which is the point the Blockers tab is making: the student is not
# bad at the quotient rule or at difference quotients, they lose the negative across a bracket.
MISS_SIGNS_1 = _sign_miss(
    # f(x) = x^2 + 2x + 1, secant from 3 to 3.5. f(3) = 9 + 6 + 1.
    "der.definition", "os-fs-id1169736589136", 0.74,
    "= [20.25 - 9 + 6 + 1] / 0.5",
    "= [20.25 - 9 - 6 - 1] / 0.5",
    "The secant setup is right. Subtracting f(3) means subtracting all of (9 + 6 + 1), so every "
    "term inside flips sign.",
)
MISS_SIGNS_2 = _sign_miss(
    # f(x) = x^2 + 9x at a = 2. f(2) = 4 + 18.
    "der.definition", "os-fs-id1169739067661", 0.68,
    "= [4 + 4h + h^2 + 18 + 9h - 4 + 18] / h",
    "= [4 + 4h + h^2 + 18 + 9h - 4 - 18] / h",
    "Same step as Monday: subtracting f(2) means -(4 + 18), which is -4 - 18.",
)
MISS_SIGNS_3 = _sign_miss(
    # f(x) = x^2 / (x + 1). Numerator 2x(x+1) - x^2(1).
    NODE_QUOTIENT, "gen-der.quotient-rule-2", 0.82,
    "= [2x^2 + 2x + x^2] / (x+1)^2",
    "= [2x^2 + 2x - x^2] / (x+1)^2",
    "Your quotient rule is right. Subtracting x^2(1) takes the x^2 off, it does not add it on.",
)
MISS_SIGNS_4 = _sign_miss(
    # f(x) = (x - 4) / (x^2 + 1). Numerator (x^2+1) - (x-4)(2x).
    NODE_QUOTIENT, "gen-der.quotient-rule-3", 0.88,
    "= [x^2 + 1 - 2x^2 - 8x] / (x^2+1)^2",
    "= [x^2 + 1 - 2x^2 + 8x] / (x^2+1)^2",
    "Your quotient rule is correct. You dropped the negative distributing across -(x - 4)(2x), "
    "so -2x^2 + 8x became -2x^2 - 8x.",
)

# The two fraction-arithmetic slips.
MISS_FRACTIONS_1 = _fraction_miss(
    # lim x->3 of 1/(x-3) - 4/(x^2-2x-3), where x^2-2x-3 = (x-3)(x+1).
    "lim.indeterminate-factoring", "os-fs-id1170571681425", 0.85,
    "= [1 - 4] / ((x-3)(x+1))",
    "= [(x+1) - 4] / ((x-3)(x+1))",
    "Over the common denominator (x-3)(x+1), the first term's numerator becomes (x+1), not 1.",
)
MISS_FRACTIONS_2 = _fraction_miss(
    # f(x) = 1/x at a = 2: the difference quotient needs 1/(2+h) - 1/2 over one denominator.
    "der.definition", "os-fs-id1169739304170", 0.78,
    "= [2 - (2+h)] / [2 + (2+h)] * 1/h",
    "= [2 - (2+h)] / [2(2+h)] * 1/h",
    "Common denominators multiply, they do not add: 1/(2+h) - 1/2 is over 2(2+h).",
)


WEEK: tuple[Session, ...] = (

    # ------------------------------------------------------------------ day 1: placement
    Session(1, 16, 0, "placement: what you already know", (
        Drill(NODE_SIGNS, correct=5),
        Drill(NODE_FRACTIONS, correct=3),
        Drill("alg.exponent-rules", correct=2),
        Drill("alg.factoring", correct=2),
        Drill("alg.solving-equations", correct=3),
        Drill("trig.unit-circle", correct=3),
        Drill("lim.concept", correct=2),
        Drill("lim.direct-substitution", correct=2),
        Drill("lim.continuity", correct=3),
        Drill("alg.function-composition", correct=2),
        Drill("alg.radicals", correct=2),
        Drill("alg.vectors", correct=2),
    )),
    Session(1, 17, 40, "first set: where the derivative comes from", (
        Drill("der.power-rule", correct=2),
        Drill("lim.indeterminate-factoring", correct=2),
        MISS_FRACTIONS_1,
        Drill("der.definition", correct=2),
        MISS_SIGNS_1,
        MISS_SIGNS_2,
    )),
    Session(1, 19, 0, "stretch: rules the student has not earned yet", (
        Drill("der.chain-rule", correct=1, wrong=3),
        Drill("der.product-rule", correct=1, wrong=2),
    )),

    # ------------------------------------------------------------------ day 2
    Session(2, 8, 30, "morning set", (
        MISS_SIGNS_3,
        Drill("der.slope-interpretation", correct=2),
    )),
    Session(2, 16, 0, "review drills", (
        Drill(NODE_SIGNS, correct=4),
        Drill(NODE_FRACTIONS, correct=3),
        Drill("alg.exponent-rules", correct=2),
        Drill("alg.factoring", correct=2),
        Drill("alg.solving-equations", correct=3),
        Drill("trig.unit-circle", correct=2),
        Drill("lim.concept", correct=2),
        Drill("lim.direct-substitution", correct=2),
        Drill("lim.continuity", correct=3),
        Drill("alg.function-composition", correct=2),
        Drill("trig.identities", correct=2, wrong=1),
    )),
    Session(2, 17, 40, "today's set", (
        Drill("der.power-rule", correct=2),
        Drill("lim.indeterminate-factoring", correct=2),
        Drill("der.definition", correct=2, wrong=1),
        Drill("der.constant-multiple-sum", correct=2, wrong=1),
        Drill("explog.rules", correct=2, wrong=3),
    )),
    Session(2, 19, 0, "stretch", (
        Drill("der.trig-derivatives", correct=1, wrong=2),
        Drill("der.exp-log-derivatives", correct=1, wrong=2),
        Drill("alg.radicals", correct=1, wrong=2),
    )),

    # ------------------------------------------------------------------ day 3
    Session(3, 16, 0, "review drills", (
        Drill(NODE_SIGNS, correct=4),
        Drill(NODE_FRACTIONS, correct=3),
        Drill("alg.exponent-rules", correct=2),
        Drill("alg.factoring", correct=2),
        Drill("alg.solving-equations", correct=3),
        Drill("lim.concept", correct=2),
        Drill("lim.direct-substitution", correct=2),
        Drill("trig.identities", correct=2),
    )),
    Session(3, 17, 40, "today's set", (
        Drill("der.power-rule", correct=2),
        Drill("lim.indeterminate-factoring", correct=2),
        Drill("der.definition", correct=2),
        MISS_FRACTIONS_2,
        Drill("der.trig-derivatives", correct=2, wrong=3),
        Drill("der.higher-order", correct=2, wrong=1),
    )),
    Session(3, 19, 0, "stretch", (
        Drill(NODE_QUOTIENT, correct=3, wrong=2),
        Drill("der.chain-rule", correct=1, wrong=3),
        Drill("der.slope-interpretation", correct=1, wrong=2),
    )),

    # ------------------------------------------------------------------ day 4: clean day
    Session(4, 16, 0, "review drills", (
        Drill(NODE_SIGNS, correct=4),
        Drill(NODE_FRACTIONS, correct=2, hint=1),
        Drill("alg.exponent-rules", correct=2),
        Drill("alg.factoring", correct=2),
        Drill("alg.solving-equations", correct=2),
        Drill("trig.unit-circle", correct=3),
        Drill("lim.concept", correct=2),
        Drill("lim.direct-substitution", correct=2),
        Drill("lim.continuity", correct=3),
        Drill("alg.function-composition", correct=3),
    )),
    Session(4, 17, 40, "today's set", (
        Drill("der.power-rule", correct=2),
        Drill("lim.indeterminate-factoring", correct=2),
        Drill("der.definition", correct=2),
        Drill(NODE_QUOTIENT, correct=3, wrong=2),
        Drill("der.chain-rule", correct=2, wrong=3),
        Drill("der.product-rule", correct=2, wrong=2),
    )),
    Session(4, 19, 0, "stretch", (
        Drill("der.trig-derivatives", correct=1, wrong=2),
        Drill("der.higher-order", correct=1, wrong=2),
        Drill("alg.vectors", correct=1, wrong=2),
    )),

    # ------------------------------------------------------------------ day 5: yesterday
    # The morning set carries the fourth sign-distribution slip. Nothing in this session hands
    # implicit credit to either blocked node (the quotient-rule attempt is wrong, and the power
    # and chain rules encompass neither), which is what leaves both of them genuinely due for the
    # evening drills below. That is the honest version of "the blockers are still live".
    # Yesterday's warm-up sweeps the rules the student had been dropping. It matters for the demo
    # as well as for the story: the review slot ranks by how far retrievability has fallen, so
    # touching these leaves der.quotient-rule, failed yesterday morning and never revisited, as
    # the most overdue thing the student owns. That is how the climax problem earns its place in
    # today's set instead of being wired there.
    Session(5, 7, 40, "warm-up sweep", (
        Drill("explog.rules", correct=1),
        Drill("alg.radicals", correct=1),
        Drill("alg.vectors", correct=1),
        Drill("der.slope-interpretation", correct=1),
        Drill("der.product-rule", correct=1),
        Drill("trig.identities", correct=1),
        Drill("der.higher-order", correct=1),
    )),
    Session(5, 8, 30, "morning set", (
        MISS_SIGNS_4,
        # Wrong only, and deliberately so. A correct quotient-rule attempt encompasses BOTH
        # blocked nodes, and crediting them here would stamp `last_seen` twelve hours before the
        # evening drills, leaving those drills un-spaced: no stability growth, p_eff under
        # PREREQ_READY, der.quotient-rule locked instead of frontier. The demo state is that
        # sensitive to one line of the plan, which is exactly why --verify exists.
        # It also sits first, so nothing the student touched later is more overdue.
        Drill(NODE_QUOTIENT, wrong=2),
        Drill("der.power-rule", correct=2),
        Drill("der.chain-rule", correct=2, wrong=1),
        Drill("der.constant-multiple-sum", correct=2),
        Drill("der.exp-log-derivatives", correct=1, wrong=2),
        Drill("der.trig-derivatives", wrong=2),
    )),
    Session(5, 17, 0, "review drills", (
        Drill(NODE_FRACTIONS, correct=3, hint=1),
        Drill("alg.exponent-rules", correct=2),
        Drill("alg.factoring", correct=2),
        Drill("lim.concept", correct=2),
        Drill("lim.direct-substitution", correct=2),
        Drill("lim.indeterminate-factoring", correct=2),
        Drill("der.higher-order", correct=2),
    )),
    # "Fix this": the targeted drill the Blockers tab launches. Late, after the day's failure,
    # and hinted throughout, so the misconception survives the night.
    Session(5, 21, 0, "fix this: distributing a negative", (
        Drill(NODE_SIGNS, correct=4, hint=1),
    )),
)


# --------------------------------------------------------------------------- item bank

# Held back from the seeded week so the student meets it for the first time on stage. It is the
# problem the demo is built around, `f(x) = (2x + 1)/(x - 3)`, and the one the cached diagnosis in
# data/demo/diagnosis_cache.json answers.
RESERVED_FOR_DEMO: frozenset[str] = frozenset({"gen-der.quotient-rule-1"})


class _ItemBank:
    """Gradeable items only, cycled deterministically so repeats spread across the bank.

    Serving an ungradeable item is the single most damaging failure the system can produce (see
    the note in items.json), so a seeded history that referenced one would be seeding the bug.
    """

    def __init__(self, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = raw["items"] if isinstance(raw, dict) else raw
        self.by_node: dict[str, list[str]] = {}
        self.gradeable: set[str] = set()
        self.node_of: dict[str, str] = {}
        for rec in records:
            self.node_of[rec["item_id"]] = rec["node_id"]
            if not rec.get("gradeable"):
                continue
            self.gradeable.add(rec["item_id"])
            if rec["item_id"] in RESERVED_FOR_DEMO:
                continue
            self.by_node.setdefault(rec["node_id"], []).append(rec["item_id"])
        for ids in self.by_node.values():
            ids.sort()
        self._cursor: dict[str, int] = {}

    def next_item(self, node_id: str) -> str:
        ids = self.by_node.get(node_id)
        if not ids:
            raise ValueError(f"no gradeable items for node {node_id}")
        index = self._cursor.get(node_id, 0)
        self._cursor[node_id] = index + 1
        return ids[index % len(ids)]

    def pinned(self, item_id: str, node_id: str) -> str:
        if item_id not in self.gradeable:
            raise ValueError(f"pinned item {item_id} is missing or not gradeable")
        if self.node_of[item_id] != node_id:
            raise ValueError(f"pinned item {item_id} is not tagged to {node_id}")
        return item_id


# --------------------------------------------------------------------------- generation

def day_start(day: int) -> datetime:
    """Midnight UTC on study day `day`, counting back from the demo day."""
    offset = HISTORY_DAYS + 1 - day
    d = ANCHOR_TODAY.date() - timedelta(days=offset)
    return utc(d.year, d.month, d.day, 0)


def at(day: int, hour: int, minute: int) -> datetime:
    return day_start(day) + timedelta(hours=hour, minutes=minute)


def _encompassed(graph: Graph, node_id: str) -> tuple[str, ...]:
    return tuple(child for child, _credit in graph.encompasses(node_id))


def build_attempts(graph: Graph, bank: _ItemBank) -> list[dict]:
    """Expand WEEK into the flat, chronologically ordered attempt log."""
    rows: list[dict] = []

    for session in WEEK:
        slot = 0
        for entry in session.entries:
            if isinstance(entry, Drill):
                specs = ([(True, entry.hint)] * entry.correct
                         + [(False, entry.hint)] * entry.wrong)
                for correct, hint in specs:
                    ts = at(session.day, session.hour,
                            session.minute + slot * MINUTES_PER_PROBLEM)
                    slot += 1
                    uses = entry.uses if entry.uses is not None else _encompassed(graph, entry.node)
                    item = (bank.pinned(entry.item, entry.node) if entry.item
                            else bank.next_item(entry.node))
                    rows.append(_row(
                        item=item, ts=ts, node=entry.node, correct=correct,
                        hint=hint, channel=entry.channel,
                        used_nodes=uses if correct else (),
                        session=session.label,
                    ))
            elif isinstance(entry, Miss):
                ts = at(session.day, session.hour, session.minute + slot * MINUTES_PER_PROBLEM)
                slot += 1
                rows.append(_row(
                    item=bank.pinned(entry.item, entry.node), ts=ts, node=entry.node, correct=False,
                    hint=entry.hint, channel=entry.channel, used_nodes=(),
                    session=session.label,
                    blamed_node=entry.blamed, blame_confidence=entry.confidence,
                    misconception_tag=entry.tag,
                    failed_step=entry.failed_step, corrected_step=entry.corrected_step,
                    student_message=entry.message,
                ))
            else:                                       # pragma: no cover - plan authoring bug
                raise TypeError(f"unknown plan entry: {entry!r}")

    rows.sort(key=lambda r: r["ts"])
    for index, row in enumerate(rows, start=1):
        row["attempt_id"] = f"a{index:04d}"
    # attempt_id is assigned in timestamp order, so replay's (ts, attempt_id) tie-break agrees
    # with the order the student actually worked in.
    return [_ordered_row(r) for r in rows]


def _row(*, item: str, ts: datetime, node: str, correct: bool, hint: int, channel: str,
         used_nodes: Iterable[str], session: str, blamed_node: Optional[str] = None,
         blame_confidence: float = 0.0, misconception_tag: Optional[str] = None,
         failed_step: Optional[str] = None, corrected_step: Optional[str] = None,
         student_message: Optional[str] = None) -> dict:
    return {
        "attempt_id": "",
        "student_id": STUDENT_ID,
        "item_id": item,
        "node_id": node,
        "ts": ts.isoformat(),
        "correct": correct,
        "hint_level": hint,
        "channel": channel,
        "used_nodes": list(used_nodes),
        "blamed_node": blamed_node,
        "blame_confidence": blame_confidence,
        "misconception_tag": misconception_tag,
        "failed_step": failed_step,
        "corrected_step": corrected_step,
        "student_message": student_message,
        "session": session,
    }


_ROW_ORDER = ("attempt_id", "student_id", "item_id", "node_id", "ts", "correct", "hint_level",
              "channel", "used_nodes", "blamed_node", "blame_confidence", "misconception_tag",
              "failed_step", "corrected_step", "student_message", "session")


def _ordered_row(row: dict) -> dict:
    return {key: row[key] for key in _ROW_ORDER}


# --------------------------------------------------------------------------- validation

def validate(rows: list[dict], graph: Graph, bank: _ItemBank) -> None:
    """Every reference must be real. A seeded history that points at a missing or ungradeable
    item is worse than no history: it fails on stage, in the one flow nobody can debug live."""
    seen_ids: set[str] = set()
    for row in rows:
        aid = row["attempt_id"]
        assert aid and aid not in seen_ids, f"duplicate attempt_id {aid}"
        seen_ids.add(aid)

        item_id, node_id = row["item_id"], row["node_id"]
        assert item_id in bank.node_of, f"{aid}: unknown item {item_id}"
        assert item_id in bank.gradeable, f"{aid}: item {item_id} is not gradeable"
        assert bank.node_of[item_id] == node_id, (
            f"{aid}: item {item_id} belongs to {bank.node_of[item_id]}, not {node_id}")
        assert node_id in graph.nodes, f"{aid}: unknown node {node_id}"

        blamed = row["blamed_node"]
        if blamed is not None:
            assert blamed in graph.nodes, f"{aid}: unknown blamed node {blamed}"
            assert not row["correct"], f"{aid}: a correct attempt cannot carry blame"
            assert row["failed_step"] and row["corrected_step"], (
                f"{aid}: a blamed attempt must carry the student's line and its repair")
        for used in row["used_nodes"]:
            assert used in graph.nodes, f"{aid}: unknown used node {used}"

    timestamps = [row["ts"] for row in rows]
    assert timestamps == sorted(timestamps), "history is not in chronological order"
    assert rows[-1]["ts"] < ANCHOR_TODAY.isoformat(), "history must end before the demo day"


# --------------------------------------------------------------------------- history file

NOTE = (
    "Seeded 5-day history for the demo student. Attempts are the source of truth; every number "
    "the UI shows is derived by replaying this log through engine.replay.replay. Regenerate with "
    "`python3 scripts/seed_demo.py`, inspect with `--verify`. Never edit by hand."
)


def build_history(graph: Graph, bank: _ItemBank) -> dict:
    rows = build_attempts(graph, bank)
    validate(rows, graph, bank)
    correct = sum(1 for r in rows if r["correct"])
    return {
        "generated_by": "scripts/seed_demo.py",
        "note": NOTE,
        "student_id": STUDENT_ID,
        "anchor_today": ANCHOR_TODAY.isoformat(),
        "history_start": day_start(1).isoformat(),
        "history_days": HISTORY_DAYS,
        "attempt_count": len(rows),
        "correct_count": correct,
        "attempts": rows,
    }


def serialise(history: dict) -> str:
    return json.dumps(history, indent=2, ensure_ascii=False) + "\n"


def write_history(path: Path = HISTORY_PATH) -> dict:
    graph = load_graph(GRAPH_PATH)
    bank = _ItemBank(ITEMS_PATH)
    history = build_history(graph, bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialise(history), encoding="utf-8")
    return history


# --------------------------------------------------------------------------- loading, replay

def attempt_from_row(row: dict) -> Attempt:
    """Row to Attempt, keeping only the fields Attempt actually declares.

    `failed_step`, `corrected_step`, `student_message` and `session` are carried in the file for
    the Blockers card and are ignored by the engine. Filtering on the dataclass fields rather
    than a hardcoded list means this keeps working the day Attempt grows those fields.
    """
    known = {f.name for f in dataclasses.fields(Attempt)}
    kwargs = {k: v for k, v in row.items() if k in known}
    kwargs["ts"] = datetime.fromisoformat(row["ts"])
    if "used_nodes" in known:
        kwargs["used_nodes"] = tuple(row.get("used_nodes") or ())
    return Attempt(**kwargs)


def load_history(path: Path = HISTORY_PATH) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {"attempts": raw}


def load_attempts(path: Path = HISTORY_PATH) -> list[Attempt]:
    return [attempt_from_row(r) for r in load_history(path)["attempts"]]


def misconception_counts(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """(blamed node, tag) -> occurrence count and the most recent blamed attempt."""
    counts: dict[tuple[str, str], dict] = {}
    for row in rows:
        tag = row.get("misconception_tag")
        blamed = row.get("blamed_node")
        if not tag or not blamed:
            continue
        key = (blamed, tag)
        entry = counts.setdefault(key, {"count": 0, "last": None})
        entry["count"] += 1
        entry["last"] = row
    return counts


# --------------------------------------------------------------------------- verify

def verify(path: Path = HISTORY_PATH) -> int:
    graph = load_graph(GRAPH_PATH)
    items = load_items(ITEMS_PATH)
    history = load_history(path)
    rows = history["attempts"]
    attempts = [attempt_from_row(r) for r in rows]
    now = datetime.fromisoformat(history["anchor_today"])
    states = replay(attempts, graph)

    print(f"history      {len(rows)} attempts, "
          f"{history['history_start'][:10]} to {rows[-1]['ts'][:10]}")
    print(f"demo day     {now.isoformat()}")

    by_status: dict[str, list[str]] = {}
    for node_id in graph.nodes:
        by_status.setdefault(status(node_id, graph, states, now), []).append(node_id)

    print("\nnodes by status")
    for name in ("mastered", "learning", "frontier", "locked"):
        ids = sorted(by_status.get(name, []))
        print(f"  {name:9s} {len(ids):2d}  {', '.join(ids) if ids else '-'}")

    print("\nopen misconceptions (from replayed state)")
    counts = misconception_counts(rows)
    any_open = False
    for node_id in sorted(graph.nodes):
        state = states[node_id]
        for tag in state.misconceptions:
            any_open = True
            info = counts.get((node_id, tag), {"count": 0, "last": None})
            last = info["last"]
            print(f"  {node_id:26s} tag={tag:22s} occurrences={info['count']} "
                  f"last={last['ts'][:16] if last else '-'}")
            if last:
                print(f"      wrong: {last['failed_step']}")
                print(f"      right: {last['corrected_step']}")
    if not any_open:
        print("  none")

    correct = sum(1 for r in rows if r["correct"])
    print(f"\naccuracy     {correct}/{len(rows)} = {100.0 * correct / len(rows):.1f}%")

    due = sorted(n for n in graph.nodes if is_due(states[n], now))
    print(f"due reviews  {len(due)}: {', '.join(due) if due else '-'}")

    print("\nkey nodes")
    for node_id in (NODE_SIGNS, NODE_FRACTIONS, NODE_QUOTIENT, "der.power-rule"):
        s = states[node_id]
        print(f"  {node_id:26s} p={s.p:.3f} p_eff={p_eff(s, now):.3f} R={retrievability(s, now):.3f} "
              f"S={s.stability:.2f} succ={s.successes} status={status(node_id, graph, states, now)}")

    print("\ndaily set for the demo day")
    entries = compose_daily_set(graph, states, items, now)
    for i, entry in enumerate(entries, start=1):
        print(f"  {i}. [{entry.slot:9s}] {entry.item.item_id:32s} "
              f"p_success={entry.predicted_success:.2f}  {entry.reason}")
    print(f"  ({len(entries)} entries)")
    return 0


# --------------------------------------------------------------------------- cli

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true",
                        help="replay the shipped history and print the state it produces")
    parser.add_argument("--out", default=str(HISTORY_PATH), help="where to write the history")
    args = parser.parse_args(argv)

    path = Path(args.out)
    if args.verify:
        return verify(path)

    history = write_history(path)
    print(f"wrote {path} ({history['attempt_count']} attempts, "
          f"{history['correct_count']} correct)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
