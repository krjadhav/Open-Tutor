"""The in-memory demo session: an append-only attempt log and nothing else.

The one rule this module exists to hold, from `db/schema.sql` and `engine/replay.py`: **attempts
are truth and are append-only; every mastery number is a fold over them.** So this module stores
a list of `Attempt` records and never a `NodeState`. `Session.states()` calls `engine.replay.replay`
on every request. It is a 37-node graph and a log of tens of rows, so refolding is free, and the
alternative (a cached state dict that someone eventually mutates) is exactly the failure the whole
design was built to prevent.

Time is a constant, not a clock. `DEMO_TODAY` is a module constant and the seeded history is
shifted so that its newest attempt lands on `DEMO_YESTERDAY`, whatever dates the seed file was
authored with. A demo whose "4 times this week" quietly became "4 times last week" overnight is a
demo that breaks on stage, and reading `datetime.now()` anywhere in here would also make every
test time-dependent.

`data/demo/history.json` is produced by another agent. If it is missing we fall back to an EMPTY
log and say so loudly, both in a log line and in `Session.history_source`. We deliberately do not
invent a plausible-looking history: a fabricated one would be indistinguishable from the real seed
on screen and would silently ship wrong numbers.

Alongside the log the session holds the three facts the onboarding flow turns on: whether anyone
has signed up, what they asked to be called, and which course they picked. That is the whole of
"who is using this", and `Session.flow` folds it into the one answer the frontend asks for. It is
kept here rather than derived in the API layer for the same reason `NodeState` is: two readers of
the same rule drift, and the flow rule changes under the frontend on `POST /api/reset?full=1`,
which is a call the frontend did not make.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field, replace as _dc_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:               # the repo is not installed as a package
    sys.path.insert(0, str(_REPO_ROOT))

from engine.replay import load_graph, replay                    # noqa: E402
from engine.selection import compose_daily_set, interleave, load_items   # noqa: E402
from engine.types import Attempt, Graph, Item, NodeState, SetEntry       # noqa: E402
from services.grading import load_item_bank                     # noqa: E402

log = logging.getLogger("app.state")

# --------------------------------------------------------------------------- constants

#: The instant the demo runs. A CONSTANT, never a clock read: see the module docstring.
DEMO_TODAY = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
DEMO_YESTERDAY = DEMO_TODAY - timedelta(days=1)

STUDENT_ID = "demo"

HISTORY_PATH = "data/demo/history.json"
GRAPH_PATH = "data/graph/nodes.json"
ITEMS_PATH = "data/items/items.json"
DIAGNOSIS_CACHE_PATH = "data/demo/diagnosis_cache.json"
COURSES_PATH = "data/courses.json"
I18N_DIR = "data/i18n"

#: The ONE thing that gates course selection. A course is playable when its manifest `state` says
#: so, and nothing else in the process is allowed to have an opinion: `GET /api/courses` reports
#: `selectable` from this predicate and `POST /api/courses/{id}/select` refuses on the same one, so
#: the card and the endpoint can never disagree.
COURSE_ACTIVE = "active"

#: How many days back "this week" reaches, for blocker frequency and accuracy.
WEEK_DAYS = 7


def resolve(path: str | Path) -> Path:
    """A path relative to the repo root as well as to the cwd. The server, the tests and a
    notebook all run from different directories."""
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p
    return _REPO_ROOT / p


# --------------------------------------------------------------------------- pending attempt

@dataclass
class Pending:
    """A graded attempt that has NOT yet been appended to the log.

    `/solve/grade` creates one, `/solve/diagnose` attaches blame to it, `/solve/commit` turns it
    into an `Attempt` and appends. Nothing before commit touches derived state, which is what lets
    the student contest the blame ("was this actually your mistake?") without having to unwind a
    graph update.
    """
    attempt_id: str
    item_id: str
    node_id: str
    ts: datetime
    correct: bool
    hint_level: int = 0
    channel: str = "typed"
    typed_answer: str = ""
    work_lines: tuple[str, ...] = ()
    unsimplified: bool = False
    used_nodes: tuple[str, ...] = ()
    blamed_node: Optional[str] = None
    blame_confidence: float = 0.0
    misconception_tag: Optional[str] = None
    failed_step: Optional[str] = None
    corrected_step: Optional[str] = None
    committed: bool = False

    def to_attempt(self, *, accept_blame: bool = True) -> Attempt:
        """The immutable log row. Rejecting the blame drops the routing, not the failure."""
        blamed = self.blamed_node if accept_blame else None
        return Attempt(
            attempt_id=self.attempt_id,
            student_id=STUDENT_ID,
            item_id=self.item_id,
            node_id=self.node_id,
            ts=self.ts,
            correct=self.correct,
            hint_level=self.hint_level,
            channel=self.channel,
            used_nodes=tuple(self.used_nodes),
            blamed_node=blamed,
            blame_confidence=self.blame_confidence if accept_blame else 0.0,
            misconception_tag=self.misconception_tag if accept_blame else None,
        )


@dataclass
class CommitRecord:
    """What one commit did, kept so `/session/complete` does not have to re-derive it."""
    attempt_id: str
    node_id: str
    xp: int
    node_changes: list[dict] = field(default_factory=list)
    unlocked: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- history loading

_TS_KEYS = ("ts", "timestamp", "created_at", "at", "when")
_ID_KEYS = ("attempt_id", "id")


def _parse_ts(rec: dict) -> Optional[datetime]:
    """An ISO timestamp under any of the usual keys, or a relative `days_ago` offset.

    Tolerant on purpose: the seed file is written by another agent and a demo that dies because
    the key was called `created_at` instead of `ts` is a demo lost to a naming argument.
    """
    for key in _TS_KEYS:
        raw = rec.get(key)
        if isinstance(raw, str) and raw.strip():
            text = raw.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    for key in ("days_ago", "day_offset", "days_before_today"):
        raw = rec.get(key)
        if isinstance(raw, (int, float)):
            hour = rec.get("hour")
            base = DEMO_TODAY - timedelta(days=float(raw))
            if isinstance(hour, (int, float)):
                base = base.replace(hour=int(hour), minute=0, second=0, microsecond=0)
            return base
    return None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _load_raw_items(path: str | Path) -> dict[str, dict]:
    """The on-disk item records, keyed by id.

    `engine.types.Item` deliberately drops the `check` spec (it is the pipeline's business, not
    the engine's). Twin generation rewrites that spec and recomputes the answer from it, so the
    raw records have to be kept alongside the typed bank rather than reloaded per request.
    """
    raw = json.loads(resolve(path).read_text(encoding="utf-8"))
    rows = raw["items"] if isinstance(raw, dict) else raw
    return {row["item_id"]: row for row in rows}


def load_history(
    path: str | Path,
    item_bank: dict[str, Item],
) -> tuple[list[Attempt], dict[str, dict], str]:
    """`(attempts, extras_by_attempt_id, source_description)`.

    `extras` carries the two fields the Blockers card needs that `Attempt` does not model:
    `failed_step` (the student's own wrong line) and `corrected_step`. They are display data, not
    engine input, so they live beside the log rather than inside it.

    Every attempt is shifted by one constant offset so the NEWEST seeded attempt lands on
    `DEMO_YESTERDAY`. Spacing between attempts, which is what drives decay and consolidation, is
    preserved exactly.
    """
    p = resolve(path)
    if not p.exists():
        log.warning(
            "SEEDED HISTORY MISSING: %s does not exist. Falling back to an EMPTY attempt log. "
            "Every screen will render, but the demo has no blockers, no streak and no mastered "
            "nodes until that file lands. No history has been invented in its place.", p)
        return [], {}, f"empty (no {path})"

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        log.warning("SEEDED HISTORY UNREADABLE: %s (%s). Falling back to an EMPTY attempt log.",
                    p, e)
        return [], {}, f"empty ({path} unreadable: {e})"

    records = raw.get("attempts", raw) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        log.warning("SEEDED HISTORY MALFORMED: %s has no attempt list. Using an EMPTY log.", p)
        return [], {}, f"empty ({path} malformed)"

    attempts: list[Attempt] = []
    extras: dict[str, dict] = {}
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        ts = _parse_ts(rec)
        if ts is None:
            log.warning("history row %s has no readable timestamp; skipped", i)
            continue
        item_id = str(rec.get("item_id") or "")
        node_id = rec.get("node_id")
        if not node_id:
            item = item_bank.get(item_id)
            node_id = item.node_id if item else None
        if not node_id:
            log.warning("history row %s has no node_id and item %r is unknown; skipped",
                        i, item_id)
            continue
        attempt_id = str(next((rec[k] for k in _ID_KEYS if rec.get(k)), f"seed-{i + 1}"))
        attempts.append(Attempt(
            attempt_id=attempt_id,
            student_id=str(rec.get("student_id") or STUDENT_ID),
            item_id=item_id,
            node_id=str(node_id),
            ts=ts,
            correct=bool(rec.get("correct", False)),
            hint_level=int(rec.get("hint_level") or 0),
            channel=str(rec.get("channel") or "typed"),
            used_nodes=_as_tuple(rec.get("used_nodes")),
            blamed_node=rec.get("blamed_node") or None,
            blame_confidence=float(rec.get("blame_confidence") or 0.0),
            misconception_tag=rec.get("misconception_tag") or None,
        ))
        extras[attempt_id] = {
            "failed_step": rec.get("failed_step"),
            "corrected_step": rec.get("corrected_step"),
            "answer_given": rec.get("answer_given"),
        }

    if not attempts:
        log.warning("SEEDED HISTORY EMPTY: %s parsed to zero usable attempts.", p)
        return [], {}, f"empty ({path} had no usable rows)"

    # A WHOLE-DAY offset, not an exact-instant one. The seed file places attempts at real times of
    # day (a morning session, an evening session), and those times are the day boundaries that
    # decay, the streak and "4 times this week" are all counted against. Shifting by a fractional
    # day would slide an evening attempt into the next calendar date and quietly rewrite the
    # history's shape; shifting by whole days lands the newest attempt on DEMO_YESTERDAY's date
    # with its clock time, and every gap, intact.
    newest = max(a.ts for a in attempts)
    offset = timedelta(days=(DEMO_YESTERDAY.date() - newest.date()).days)
    attempts = [_dc_replace(a, ts=a.ts + offset) for a in attempts]
    log.info("seeded history: %d attempts from %s, shifted by %s so the newest reads as %s",
             len(attempts), p, offset, DEMO_YESTERDAY.date())
    return attempts, extras, f"{p} ({len(attempts)} attempts, anchored to {DEMO_YESTERDAY.date()})"


# --------------------------------------------------------------------------- the course manifest

def load_courses(path: str | Path = COURSES_PATH) -> list[dict]:
    """The course manifest, or `[]` if it is missing or unreadable.

    Same degradation rule as the seeded history: content that is not there produces an empty list
    and a loud log line, never an exception inside a request. An empty manifest means the course
    screen has nothing to offer, which is visible and honest; a fabricated course is neither.
    """
    p = resolve(path)
    if not p.exists():
        log.warning("COURSE MANIFEST MISSING: %s does not exist. No course is selectable.", p)
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                        # noqa: BLE001
        log.warning("COURSE MANIFEST UNREADABLE: %s (%s). No course is selectable.", p, e)
        return []
    rows = raw.get("courses", []) if isinstance(raw, dict) else raw
    return [row for row in rows if isinstance(row, dict) and row.get("id")]


def is_selectable(course: dict) -> bool:
    """Is this course playable? THE definition, and the only one in the process.

    Deliberately a function over the manifest record rather than a field the frontend derives from
    `state`. Two readers of the same rule drift the first time the rule changes, and the one that
    drifts is always the one that is not enforcing anything.
    """
    return course.get("state") == COURSE_ACTIVE


def default_course_id(path: str | Path = COURSES_PATH) -> Optional[str]:
    """The course `POST /api/reset` lands on: the first selectable one in the manifest.

    Read from the manifest rather than hardcoded, so the same predicate that makes exactly one card
    tappable also decides what a demo rehearsal starts in.
    """
    for course in load_courses(path):
        if is_selectable(course):
            return str(course["id"])
    return None


# --------------------------------------------------------------------------- the session

class Session:
    """One student's demo session, held in memory.

    Static content (graph, item bank) is loaded once. Everything about the STUDENT is the attempt
    log plus the pending attempts, and every derived number is recomputed from the log on demand.
    """

    def __init__(self, history_path: str | Path = HISTORY_PATH,
                 courses_path: str | Path = COURSES_PATH) -> None:
        self.history_path = history_path
        self.courses_path = courses_path
        self.graph: Graph = load_graph(GRAPH_PATH)
        self.items_by_node: dict[str, list[Item]] = load_items(ITEMS_PATH)
        self.item_bank: dict[str, Item] = load_item_bank(ITEMS_PATH)
        self.raw_items: dict[str, dict] = _load_raw_items(ITEMS_PATH)
        self.reset()

    # ------------------------------------------------------------------ lifecycle

    def reset(self, *, full: bool = False) -> None:
        """Back to the demo's starting position.

        The DEFAULT (`full=False`) is the seeded student **with the course already selected**, so a
        demo rehearsal lands straight on Today and does not walk the three onboarding screens
        again. The seed file is re-read, so a history.json that lands while the server is running
        is picked up by one POST /api/reset.

        `full=True` clears the session itself: no sign up, no course, and an EMPTY attempt log, so
        the next screen is Welcome. The log has to go with it. A cleared session that still carried
        a five-day history would put the seeded student's blockers behind a sign-up form belonging
        to nobody, and `POST /api/courses/{id}/select` is the thing that installs a history, so
        leaving one in place would make that installation a no-op you could not see.
        """
        if full:
            self._seeded: list[Attempt] = []
            self._extras: dict[str, dict] = {}
            self.history_source = "empty (no course selected)"
        else:
            self._reload_seeded()
        self._clear_runtime()
        # Identity is reset alongside, in both directions: `reset()` is the seeded student and
        # `reset(full=True)` is nobody. A display name that survived either would belong to a
        # session that no longer exists.
        self.student_name: Optional[str] = None
        self.signed_up: bool = not full
        self.selected_course: Optional[str] = None if full else default_course_id(
            self.courses_path)

    def _reload_seeded(self) -> None:
        """Re-read the seed file into the log. The only place the seeded history is installed."""
        seeded, extras, source = load_history(self.history_path, self.item_bank)
        self._seeded = seeded
        self._extras = dict(extras)
        self.history_source = source

    def _clear_runtime(self) -> None:
        """Everything this process accumulated on top of the seed."""
        self._live: list[Attempt] = []
        self.pending: dict[str, Pending] = {}
        self.commits: list[CommitRecord] = []
        self._counter = 0
        self._daily: Optional[list[SetEntry]] = None
        self.done_items: set[str] = set()
        # A "Fix this" drill set, targeted at one blocked node. Not part of today's set and not
        # part of the log; it only gives those items a position so Solve can say "1 of 3".
        self.focus_set: list[str] = []
        self.focus_node: Optional[str] = None
        # Twins minted this session. They are real, CAS-validated items, but they are NOT in the
        # bank and must never enter selection: a twin exists to re-test one reveal, not to become
        # a permanent extra drill that skews the item pool for that node.
        self.twins: dict[str, Item] = {}
        self.twin_seeds: dict[str, int] = {}

    # ------------------------------------------------------------------ onboarding

    def sign_up(self, name: Optional[str] = None) -> None:
        """The placeholder sign up: a display name, and deliberately nothing else.

        **No credential is taken, so none is asked for.** No password field, no email, not even a
        fake one. A form that looks like it takes a credential and does not is a worse lie than an
        honest placeholder, and this screen is shown to judges.

        Creates or resets the session, which means the log is emptied and any course selection is
        dropped: the next screen is Course selection, and the history arrives with the course.
        """
        self.reset(full=True)
        self.signed_up = True
        self.student_name = (name or "").strip() or None

    def install_course(self, course_id: str) -> None:
        """Select a course and install its seeded history, keeping who the student is.

        Deliberately NOT `reset()`: that is the demo's rewind and clears the display name, which
        the student typed one screen ago.
        """
        self._reload_seeded()
        self._clear_runtime()
        self.signed_up = True
        self.selected_course = course_id

    @property
    def flow(self) -> str:
        """Which screen the frontend lands on: `welcome`, `courses` or `ready`.

        Decided here so it is decided once. The frontend asking "am I signed up, and do I have a
        course?" would be a second implementation of this rule living in a place that cannot see
        `POST /api/reset?full=1` change the answer underneath it.
        """
        if not self.signed_up:
            return "welcome"
        if not self.selected_course:
            return "courses"
        return "ready"

    # ------------------------------------------------------------------ the log

    @property
    def seeded_attempts(self) -> tuple[Attempt, ...]:
        return tuple(self._seeded)

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        """The whole append-only log. A tuple, so a caller cannot append behind our back."""
        return tuple(self._seeded) + tuple(self._live)

    def append(self, attempt: Attempt) -> None:
        """The ONLY way an attempt enters the log. Append only: nothing is ever edited."""
        self._live.append(attempt)

    @property
    def now(self) -> datetime:
        """`DEMO_TODAY`, or the newest attempt if the session has run past it.

        `replay(upto=...)` is inclusive, so a `now` behind the newest attempt would silently drop
        the attempt the student just committed.
        """
        latest = max((a.ts for a in self.attempts), default=DEMO_TODAY)
        return max(DEMO_TODAY, latest)

    def states(self, upto: Optional[datetime] = None) -> dict[str, NodeState]:
        """Derived state, refolded from the log every call. Never cached, never mutated."""
        return replay(self.attempts, self.graph, upto=upto or self.now)

    def states_without(self, attempt_id: str,
                       upto: Optional[datetime] = None) -> dict[str, NodeState]:
        """State as it was before one attempt, used to compute what that attempt changed."""
        log_without = [a for a in self.attempts if a.attempt_id != attempt_id]
        return replay(log_without, self.graph, upto=upto or self.now)

    # ------------------------------------------------------------------ items

    def item(self, item_id: str) -> Optional[Item]:
        """A bank item, or a twin minted during this session. The only item lookup callers use."""
        return self.item_bank.get(item_id) or self.twins.get(item_id)

    def raw_item(self, item_id: str) -> Optional[dict]:
        """The on-disk record, including the `check` spec that `Item` does not model. Twin
        generation rewrites that spec, so it needs the record and not the dataclass."""
        return self.raw_items.get(item_id)

    def register_twin(self, record: dict) -> Item:
        """Make a freshly generated twin gradeable, and nothing more.

        It goes into `twins` and `raw_items` so `/solve/grade` and a twin-of-a-twin can find it,
        and deliberately NOT into `items_by_node`, which is what `engine.selection` reads.
        """
        item = Item(
            item_id=record["item_id"],
            node_id=record["node_id"],
            stem_latex=record.get("stem_latex", ""),
            answer_latex=record.get("answer_latex"),
            answer_sympy=record.get("answer_sympy"),
            answer_kind=record.get("answer_kind"),
            difficulty_b=float(record.get("difficulty_b") or 0.0),
            encompasses=tuple(record.get("encompasses") or ()),
            source=record.get("source", "generated"),
        )
        self.twins[item.item_id] = item
        self.raw_items[item.item_id] = record
        return item

    def next_twin_seed(self, item_id: str) -> int:
        """0 the first time an item is twinned, then 1, 2, ... Deterministic, so the demo's first
        twin is always the same twin, and a student who asks again gets a different one."""
        seed = self.twin_seeds.get(item_id, 0)
        self.twin_seeds[item_id] = seed + 1
        return seed

    def extras(self, attempt_id: str) -> dict:
        return self._extras.get(attempt_id, {})

    def set_extras(self, attempt_id: str, **kw) -> None:
        self._extras.setdefault(attempt_id, {}).update(kw)

    # ------------------------------------------------------------------ today's set

    def daily_set(self) -> list[SetEntry]:
        """Today's six problems, composed once per session and then FROZEN.

        Recomposing on every request would be more "live", and it would also renumber the set
        under the student mid-session: `Problem 4 of 6` would start pointing at a different item
        the moment a commit changed the state that selection reads. The set is a day's plan, so it
        is fixed for the day; the Path, the Blockers tab and `/session/complete` all recompute
        freely and are where the state change becomes visible.
        """
        if self._daily is None:
            entries = compose_daily_set(self.graph, self.states(), self.items_by_node, self.now)
            self._daily = interleave(entries, self.graph)
        return self._daily

    def tomorrow_set(self) -> list[SetEntry]:
        """What the set would be if it were composed right now. Never frozen: this is the
        'tomorrow rebuilds around your new blocker' view."""
        return interleave(
            compose_daily_set(self.graph, self.states(), self.items_by_node, self.now),
            self.graph,
        )

    # ------------------------------------------------------------------ ids and clocks

    def next_attempt(self, **kw) -> Pending:
        """Mint a pending attempt with the next id and a deterministic timestamp."""
        self._counter += 1
        pending = Pending(
            attempt_id=f"a-{self._counter}",
            ts=DEMO_TODAY + timedelta(minutes=self._counter),
            **kw,
        )
        self.pending[pending.attempt_id] = pending
        return pending


# --------------------------------------------------------------------------- module singleton

_SESSION: Optional[Session] = None


def get_session() -> Session:
    """The process-wide demo session. One student, one process; this is a hackathon demo, not a
    multi-tenant service, and pretending otherwise would add a session store nothing reads."""
    global _SESSION
    if _SESSION is None:
        _SESSION = Session()
    return _SESSION


def reset_session() -> Session:
    session = get_session()
    session.reset()
    return session
