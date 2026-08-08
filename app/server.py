"""The Open Tutor API. Implements `docs/api-contract.md` field for field.

Three rules run through every handler, and all three come from measured results rather than taste:

  1. **The CAS decides correctness.** `services.grading.grade` returns the verdict and
     `needs_diagnosis` is false for every correct answer, so the model is never shown one.
     Learning-design section 14 finding 2: the model invented a sign-distribution error inside a
     correct-but-unsimplified answer, and telling a student who was right that they were wrong is
     the most damaging thing this product can do. Gating on the CAS deletes that class entirely.
  2. **Diagnosis is served from `data/demo/diagnosis_cache.json`.** Section 16: `seed` does not pin
     the model's output, so anything on stage must be pre-computed. A live call is the fallback
     and only happens when an API key is present. With no key, a cache miss returns a graceful
     error payload, never an exception and never a blank screen.
  3. **Attempts are append-only and state is a fold over them.** `/solve/commit` appends one row
     and then recomputes by `engine.replay.replay`. No `NodeState` is stored or mutated anywhere
     in this process, so any state the API reports can be reproduced by replaying the log from
     scratch.

And one design rule from the contract itself: **the server returns UI-ready values.** Chips, stage
grouping, dot states, "N skills away", human-readable misconception names and the `needs:` line are
all decided here, where the graph and the engine live. The frontend renders; it never computes.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import sympy as sp                                                          # noqa: E402

from app.state import (                                                     # noqa: E402
    DIAGNOSIS_CACHE_PATH,
    I18N_DIR,
    STUDENT_ID,
    WEEK_DAYS,
    Pending,
    Session,
    CommitRecord,
    get_session,
    is_selectable,
    load_courses,
    resolve,
)
from engine.mastery import is_due, p_eff, status                             # noqa: E402
from engine.replay import replay                                            # noqa: E402
from engine.selection import goal_ancestors, pick_item                                 # noqa: E402
from engine.types import PREREQ_READY, Graph, Item, NodeState               # noqa: E402
from engine.xp import xp_for                                                # noqa: E402
from pipeline.drill_tasks import P, PU, load_answer                         # noqa: E402
from services.diagnose import (                                             # noqa: E402
    DEMO_ITEM,
    Diagnosis,
    cache_key,
    cache_lookup,
    diagnose as live_diagnose,
    load_cache,
    work_key,
)
from pipeline.twin import twin_of                                            # noqa: E402
from services.grading import NOT_CHECKABLE, grade, normalise_typed          # noqa: E402

log = logging.getLogger("app.server")

app = FastAPI(title="Open Tutor", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


#: Vendored KaTeX and the fonts: 996 KB that changes only when we re-vendor it, and the one thing
#: worth caching hard on venue wifi. Served from its own directory so this is a path test, not a
#: guess from the extension.
_IMMUTABLE_PREFIX = "/vendor/"

#: The app shell. Everything here is rewritten between demo runs.
_SHELL_SUFFIXES = (".html", ".js", ".css")


@app.middleware("http")
async def _cache_headers(request: Request, call_next):
    """Say out loud how long each static file may be reused for. Silence is not "do not cache".

    `StaticFiles` sends `ETag` and `Last-Modified` and NO `Cache-Control`, and a response with no
    explicit freshness may be assigned a HEURISTIC lifetime by the client (RFC 9111 section 4.2.2),
    conventionally a tenth of its age. Desktop browsers revalidate often enough to hide this;
    phones do not. The observed symptom was a phone running the previous build of `app.js` after
    the new one had shipped: the camera went straight to upload because the crop screen did not
    exist in the copy the phone had kept, and no amount of pulling to refresh dislodged it.

    A demo is exactly the case this hurts most, because the whole point is that the laptop and the
    phone agree about what the app is.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        return response
    if path.startswith(_IMMUTABLE_PREFIX):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/" or path.endswith(_SHELL_SUFFIXES):
        response.headers["Cache-Control"] = "no-store"
    return response

# --------------------------------------------------------------------------- vocabulary

#: Engine status -> the four dot states the UI draws. `frontier` is called `now` on screen because
#: that is what the accent ring means to a student: this is where you are.
UI_STATE = {"mastered": "mastered", "learning": "learning", "frontier": "now", "locked": "locked"}
STATE_RANK = {"locked": 0, "now": 1, "learning": 2, "mastered": 3}

#: Stage grouping comes from the node id NAMESPACE, not from an authored field, and is ordered
#: TARGET FIRST so the vertical spine in the mock reads top-down from the goal.
#:
#: `explog.` is the eighth entry and is NOT in the seven-stage table in docs/ui-spec.md section 3.
#: `explog.rules` is nonetheless a genuine member of the goal slice (an ancestor of
#: `ai.gradient-descent-step`), so dropping it would break "only nodes in the goal slice are
#: listed, and all of them are". It is ordered last, with the other foundations.
STAGES: tuple[tuple[str, str], ...] = (
    ("ai", "Gradient descent"),
    ("opt", "Optimisation"),
    ("mv", "Multivariable"),
    ("der", "Derivative rules"),
    ("lim", "Limits"),
    ("trig", "Trig values"),
    ("alg", "Algebra moves"),
    ("explog", "Exponentials and logs"),
)

#: Authored content, both keyed by ids the engine already uses. `misconceptions.json` is the
#: tag-to-human-name mapping docs/ui-spec.md section 4 asks for and carries its own `name_hi` /
#: `short_hi` fields. `hints.json` is the per-node hint ladder and is GENERATED by
#: `pipeline/build_hints.py`, so it holds no translations: anything hand-added there is discarded
#: by the next build. Hindi hint prose lives in `data/i18n/hints_<lang>.json` instead, keyed by the
#: English source string (see `_hint_translations`).
MISCONCEPTIONS_PATH = "data/content/misconceptions.json"
HINTS_PATH = "data/content/hints.json"

#: The cached demo diagnosis is keyed to `demo-quotient-sign`, which is the same problem as the
#: bank's `gen-der.quotient-rule-1` (identical stem and identical answer key, verified). The photo
#: path maps one to the other so a demo run against the real bank item still hits the cache.
PHOTO_ALIASES = {"gen-der.quotient-rule-1": DEMO_ITEM.item_id}

#: Per-course strings live under this prefix in `data/i18n/<lang>.json` rather than beside the
#: manifest, because `pipeline/tests/test_translate.py` pins hi.json to `node_title:`, `item_stem:`
#: and `ui:` and checks every entry against an English source it can find. A `course_title:` family
#: would have had no source table and no coverage check; under `ui:` each string is pinned to its
#: en.json twin like every other translated string in the app.
#:
#: They are excluded from the `ui` chrome map by `_ui_strings`: see that docstring.
CATALOGUE_PREFIX = "ui:catalogue."

MINUTES_PER_PROBLEM = 2.5

#: How many drills a "Fix this" set holds. Short on purpose: it is a targeted repair between
#: today's set and tomorrow's, not a second daily set.
FOCUS_SET_SIZE = 3

#: A chip is one short line on a 390px frame. Node titles are written to be unambiguous in a graph
#: audit ("Vector notation, scalar multiples and the dot product"), which is the opposite of what a
#: chip needs, so display labels are shortened here rather than in the graph.
SHORT_LABEL_CHARS = 18
MAX_CHIP_CHARS = 32

#: Words a truncated label must not end on. Cutting "Distributing a negative across a sum" on a
#: word boundary lands on "Distributing a", and a chip ending in a dangling article reads as a bug.
_TRAILING_STOPWORDS = {"a", "an", "the", "of", "and", "or", "in", "to", "for", "with",
                       "across", "at", "on", "by", "from", "as", "into"}


# --------------------------------------------------------------------------- localisation

@lru_cache(maxsize=8)
def _json_file(path: str, key: Optional[str] = None) -> dict:
    """A content file, or `{}` if it is not there. Missing content degrades; it never raises."""
    import json
    resolved = resolve(path)
    if not resolved.exists():
        log.warning("content file %s is missing; falling back to generated defaults", resolved)
        return {}
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as e:                                       # noqa: BLE001
        log.warning("content file %s unreadable (%s); falling back to defaults", resolved, e)
        return {}
    return dict(raw.get(key, {})) if key else dict(raw)


def _entries(lang: str) -> dict[str, str]:
    return _json_file(f"{I18N_DIR}/{lang}.json", "entries")


def _misconceptions() -> dict[str, dict]:
    """The tag table, with any non-tag key dropped.

    Content files carry a top-level `notes` string alongside the entries. Iterating the raw dict
    and calling `.get` on that string is an AttributeError inside a request, so the shape is
    filtered here once rather than guarded at each of the four call sites.
    """
    return {tag: entry for tag, entry in _json_file(MISCONCEPTIONS_PATH).items()
            if isinstance(entry, dict)}


def _hint_rungs(node_id: str) -> list[dict]:
    entry = _json_file(HINTS_PATH).get(node_id)
    if not isinstance(entry, dict):
        return []
    return [rung for rung in (entry.get("rungs") or ()) if isinstance(rung, dict)]


def _hint_translations(lang: str) -> dict[str, str]:
    """Hindi hint prose, keyed by the ENGLISH SOURCE STRING.

    Not by node id, and deliberately not stored in `data/content/hints.json`: that file is
    generated by `pipeline/build_hints.py` and the next build discards anything hand-added to it.
    Keying by the English text also means one entry covers every node that shares a templated
    rung, which is why 13 entries cover 37 of the 111 prose rungs.
    """
    if lang == "en":
        return {}
    return _json_file(f"{I18N_DIR}/hints_{lang}.json", "entries")


def _default_tag_for_node(node_id: str) -> str:
    """The misconception this node's failures are registered under.

    Used when the model returns `misconception_tag: null`, which it does on the demo case. A blame
    with no tag records no misconception (see `mastery.apply_blame`), so the Blockers tab would
    stay empty and the blocker slot would never fire, which is the entire product. Deriving the
    tag from the node keeps the tag vocabulary the authored one rather than inventing a new id.
    """
    for tag, meta in _misconceptions().items():
        if meta.get("node_id") == node_id:
            return tag
    return node_id.split(".", 1)[-1]


#: A `{{name}}` interpolation slot in a translated string.
_TOKEN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _fmt(key: str, lang: str, default: Optional[str] = None, **values: Any) -> str:
    """Look `key` up in `lang`, fall back to English, then to `default`, then substitute.

    Substitution has to happen HERE rather than by the caller concatenating a number onto a
    translated fragment, because only English puts the count at the front. "16 days at your pace"
    concatenated the same way in Hindi produced "16 अपनी वर्तमान गति से दिन", which is the number
    stapled to a phrase that wanted it in the middle. The whole sentence is one key with a
    `{{n}}` slot in whatever position the language needs.

    A slot the caller did not fill is dropped, not left in place. It is a bug either way and it is
    logged as one, but a student seeing "{{n}} skills away" is a broken product, whereas "skills
    away" is merely a worse sentence.
    """
    text = _entries(lang).get(key)
    if text is None:
        text = _entries("en").get(key)
    if text is None:
        text = default if default is not None else ""

    def substitute(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name in values:
            return str(values[name])
        log.warning("i18n key %r (%s) left {{%s}} unfilled", key, lang, name)
        return ""

    return re.sub(r"\s{2,}", " ", _TOKEN.sub(substitute, text)).strip()


class I18n:
    """Already-translated strings. The frontend never holds a translation table.

    Lookup order is requested language, then English, then the English default the caller passed
    in. A missing key therefore shows readable English, never a raw `ui:some.key`, which is the
    one thing worse on stage than the wrong language.

    Mathematics is never translated: node titles and item stems come out of the translation table
    with their `$...$` spans byte-identical to English, per learning-design section 13.
    """

    def __init__(self, lang: str, graph: Graph) -> None:
        self.lang = lang if lang in ("en", "hi") else "en"
        self.graph = graph

    @property
    def entries(self) -> dict[str, str]:
        return _entries(self.lang)

    def t(self, key: str, default: Optional[str] = None, **kw: Any) -> str:
        return _fmt(key, self.lang, default, **kw)

    def node_title(self, node_id: str) -> str:
        return self.t(f"node_title:{node_id}", default=self.graph.title(node_id))

    def node_short_title(self, node_id: str) -> str:
        """The chip-sized form of a node title. `node_short:<id>` overrides the truncation."""
        return self.t(f"node_short:{node_id}", default=_short_label(self.node_title(node_id)))

    def stage_name(self, node_id: str) -> Optional[str]:
        """The name of the stage this node sits in, or None for a namespace we do not group."""
        prefix = node_id.split(".", 1)[0]
        for stage_prefix, english_name in STAGES:
            if stage_prefix == prefix:
                return self.t(f"ui:stage.{stage_prefix}", default=english_name)
        return None

    def item_stem(self, item: Item) -> str:
        return self.t(f"item_stem:{item.item_id}", default=item.stem_latex)

    def state_label(self, ui_state: str) -> str:
        """The LEGEND label: a standalone noun beside a dot. "Mastered", "Locked"."""
        return self.t(f"ui:node.{ui_state}", default=ui_state)

    def status_label(self, ui_state: str) -> str:
        """The label used INSIDE a sentence: "... drops from mastered to needs-work."

        A separate vocabulary from `state_label` on purpose. A legend entry is a title and a
        clause fragment is not, and the two diverge further in Hindi than in English, where the
        legend reads "महारत हासिल" and the sentence wants "महारत".
        """
        return self.t(f"ui:status.{ui_state}", default=self.state_label(ui_state))

    def hint_text(self, english: str) -> str:
        """Translate one PROSE rung. Callers must not pass a rung with `is_math` true: those are
        LaTeX, they are never translated, and looking one up would only invite someone to add an
        entry for it later."""
        if self.lang == "en" or not english:
            return english
        return _hint_translations(self.lang).get(english, english)

    def _content_field(self, meta: dict, field: str) -> Optional[str]:
        """`name` in English, `name_hi` in Hindi. Falls back to English when a language is
        missing, which is the whole rule for content that is translated entry by entry."""
        if self.lang != "en":
            translated = meta.get(f"{field}_{self.lang}")
            if translated:
                return translated
        return meta.get(field) or None

    def misconception_name(self, tag: Optional[str], node_id: str) -> str:
        """The long, human-readable name: "Dropping negatives across brackets". Never a raw tag."""
        if not tag:
            return self.node_title(node_id)
        meta = _misconceptions().get(tag) or {}
        return self.t(f"misconception:{tag}",
                      default=self._content_field(meta, "name") or _humanise(tag))

    def misconception_short(self, tag: Optional[str], node_id: str) -> str:
        """The chip form: "signs". A card has room for the sentence; a chip has room for a word."""
        if not tag:
            return self.node_short_title(node_id)
        meta = _misconceptions().get(tag) or {}
        return self.t(f"misconception_short:{tag}",
                      default=self._content_field(meta, "short")
                      or _short_label(self.misconception_name(tag, node_id)))


def _humanise(tag: str) -> str:
    text = tag.replace("-", " ").replace("_", " ").replace(".", " ").strip()
    return text[:1].upper() + text[1:] if text else tag


def _short_label(title: str) -> str:
    """A chip-sized version of a node title.

    Cut at the first comma, because these titles are written as "headline, then the qualifiers"
    ("Vector notation, scalar multiples and the dot product" -> "Vector notation"). If that is
    still too long, cut on a word boundary and never end on a dangling article or preposition.
    A single word longer than the budget is returned whole: "Multivariable" truncated is worse
    than "Multivariable" overflowing by two characters.
    """
    text = (title or "").split(",")[0].strip()
    if len(text) <= SHORT_LABEL_CHARS:
        return text
    words = text.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join(kept + [word])
        if kept and len(candidate) > SHORT_LABEL_CHARS:
            break
        kept.append(word)
    while len(kept) > 1 and kept[-1].lower() in _TRAILING_STOPWORDS:
        kept.pop()
    return " ".join(kept)


def _lang_of(lang: str) -> str:
    return lang if lang in ("en", "hi") else "en"


def _tr(session: Session, lang: str) -> I18n:
    return I18n(_lang_of(lang), session.graph)


# --------------------------------------------------------------------------- derived helpers

def _ui_statuses(session: Session, states: dict[str, NodeState],
                 now=None) -> dict[str, str]:
    now = now or session.now
    return {n: UI_STATE[status(n, session.graph, states, now)] for n in session.graph.nodes}


def _slice_nodes(graph: Graph) -> list[str]:
    """The goal slice: every ancestor of the target, plus the target."""
    return list(goal_ancestors(graph)) + [graph.target_node]


def _blocker_nodes(states: dict[str, NodeState]) -> list[str]:
    return [n for n, s in states.items() if s.misconceptions]


def _visible_nodes(session: Session, states: dict[str, NodeState]) -> set[str]:
    """Every node the Path renders. THE UNION, and it must not be narrowed back:

        goal slice  UNION  nodes holding an open blocker  UNION  nodes of items in today's set

    The first term is the map to the goal. The second keeps a blocker actionable however far it
    sits from the target, which is what the Blockers tab promises. The third exists because the
    student is asked to solve those problems TODAY: a quotient rule problem in today's set whose
    node is missing from the Path means the map does not show the thing the student is doing, and
    a map that omits today's work is not a map. `count` still reports mastered/total within the
    goal slice, so widening what is shown does not inflate what is claimed.
    """
    return (set(_slice_nodes(session.graph))
            | set(_blocker_nodes(states))
            | {entry.item.node_id for entry in session.daily_set()})


def _stage_state(skill_states: list[str]) -> str:
    """The dot state for a whole stage: the most advanced ACTIONABLE state inside it.

    Not the least advanced. A stage is answering "where are you in this", and a `der` stage
    holding a mastered power rule alongside six locked nodes is a stage the student is making
    progress in, not a locked one. Taking the minimum made four of seven stages read `locked` and
    hid every bit of progress the seeded student had made.

    `now` outranks `learning` because the accent ring means "this is where you are", and that is
    the single most useful thing a stage badge can say.
    """
    if "now" in skill_states:
        return "now"
    if "learning" in skill_states:
        return "learning"
    if skill_states and all(s == "mastered" for s in skill_states):
        return "mastered"
    return "locked"


def _needs_line(node_id: str, session: Session, states: dict[str, NodeState],
                tr: I18n, now) -> str:
    """`needs: <first unmet prereq>`, generated from `graph.prereqs`. Never authored.

    "Unmet" is the engine's own gate: `p_eff` below `PREREQ_READY`, exactly the condition that
    makes `engine.mastery.status` return `locked`. Anything else here would let the chip disagree
    with the dot beside it.
    """
    for prereq_id, _weight in session.graph.prereqs(node_id):
        if p_eff(states.get(prereq_id) or NodeState(node_id=prereq_id), now) < PREREQ_READY:
            return tr.t("ui:path.needs", default="needs: {{node}}",
                        node=tr.node_title(prereq_id))
    return ""


def _stages_payload(session: Session, states: dict[str, NodeState], tr: I18n, now) -> list[dict]:
    """Stages, target first, over the visibility union defined in `_visible_nodes`.

    Read that docstring before changing what is listed here: the union is
    `goal slice | open blockers | today's set`, and each term is load-bearing.
    `count` is deliberately narrower than what is shown, being mastered/total *within the goal
    slice* as the contract defines it.
    """
    ui = _ui_statuses(session, states, now)
    slice_nodes = set(_slice_nodes(session.graph))
    listed = _visible_nodes(session, states)

    stages: list[dict] = []
    for prefix, english_name in STAGES:
        nodes = [n for n in session.graph.nodes
                 if n.split(".", 1)[0] == prefix and n in listed]
        if not nodes:
            continue
        in_slice = [n for n in nodes if n in slice_nodes]
        mastered = sum(1 for n in in_slice if ui[n] == "mastered")
        skills = [{
            "node_id": n,
            "name": tr.node_title(n),
            "state": ui[n],
            "needs": _needs_line(n, session, states, tr, now),
        } for n in nodes]
        stages.append({
            "id": prefix,
            "name": tr.t(f"ui:stage.{prefix}", default=english_name),
            "count": f"{mastered}/{len(in_slice)}",
            "state": _stage_state([s["state"] for s in skills]),
            "skills": skills,
        })
    return stages


def _legend(tr: I18n) -> list[dict]:
    return [{"state": s, "label": tr.state_label(s)}
            for s in ("mastered", "learning", "now", "locked")]


#: The four tabs. A tab label is not a screen title: `ui:screen.todays_set` is "Today's Set" and
#: the tab under it says "Today", so these have their own keys.
TABS: tuple[tuple[str, str, str], ...] = (
    ("today", "ui:tab.today", "Today"),
    ("path", "ui:tab.path", "Path"),
    ("blockers", "ui:tab.blockers", "Blockers"),
    ("you", "ui:tab.you", "You"),
)


def _ui_strings(tr: I18n) -> dict[str, str]:
    """The fixed chrome: screen titles, button labels, tab labels, legend labels.

    Sourced from `data/i18n/<lang>.json` so the chrome is translated like everything else, and
    emitted as a flat `key -> string` map (without the `ui:` prefix) so a frontend fallback table
    merges with it rather than having to be restructured around it.

    Entries CARRYING A PLACEHOLDER are excluded. Those are sentence templates, and a sentence
    template is not chrome: it is rendered server-side with its numbers already in it, because the
    count sits in a different place in each language. Shipping the raw template here would invite
    the frontend to interpolate it and reintroduce exactly the bug this all exists to fix.

    `ui:catalogue.*` is excluded for the second reason: it is per-course CONTENT, not chrome. Each
    of those strings is already delivered, translated, inside its own card by `GET /api/courses`,
    and a second copy in a flat map is a second place for a title to be read from and a way for the
    two to be used inconsistently.
    """
    def is_chrome(key: str, value: str) -> bool:
        return (key.startswith("ui:") and isinstance(value, str)
                and not key.startswith(CATALOGUE_PREFIX) and not _TOKEN.search(value))

    strings = {key[len("ui:"):]: value for key, value in tr.entries.items()
               if is_chrome(key, value)}
    for key, value in _entries("en").items():                # English backfill for missing keys
        short = key[len("ui:"):] if is_chrome(key, value) else None
        if short and short not in strings:
            strings[short] = value
    for tab_id, key, english in TABS:
        strings[f"tab.{tab_id}"] = tr.t(key, default=english)
    for entry in _legend(tr):
        strings[f'legend.{entry["state"]}'] = entry["label"]
    return dict(sorted(strings.items()))


def _blockers_payload(session: Session, states: dict[str, NodeState], tr: I18n, now) -> list[dict]:
    """One card per open misconception, ranked by how often it has bitten this week."""
    window_start = now - timedelta(days=WEEK_DAYS)
    cards = []
    for node_id in sorted(_blocker_nodes(states)):
        for tag in states[node_id].misconceptions:
            hits = [a for a in session.attempts
                    if a.blamed_node == node_id and a.misconception_tag == tag]
            recent = [a for a in hits if a.ts >= window_start]
            extras = _wrong_right(session, hits)
            cards.append({
                "node_id": node_id,
                "_freq_count": len(recent),
                "name": tr.misconception_name(tag, node_id),
                "freq": _freq_line(tr, len(recent)),
                "wrong": extras.get("failed_step") or "",
                "right": extras.get("corrected_step") or "",
                "item_count": len(session.items_by_node.get(node_id, ())),
            })
    cards.sort(key=lambda c: (-c["_freq_count"], c["node_id"], c["name"]))
    out = []
    for i, card in enumerate(cards, start=1):
        card.pop("_freq_count")
        out.append({"rank": f'{tr.t("ui:slot.blocker")} {i}', **card})
    return out


def _wrong_right(session: Session, hits: list) -> dict:
    """The struck-through wrong line and its correction, from the most recent attempt that has
    BOTH, falling back to the most recent that has the wrong line at all.

    The pair has to come from one attempt: it is a single line of the student's own working and
    the same line put right. A live diagnosis gives us `failed_step` but no correction (the model
    is not asked for one), so preferring a complete pair keeps the card whole instead of letting
    the newest attempt blank out the half we do have.
    """
    for require_both in (True, False):
        for attempt in sorted(hits, key=lambda a: a.ts, reverse=True):
            extras = session.extras(attempt.attempt_id)
            if extras.get("failed_step") and (extras.get("corrected_step") or not require_both):
                return extras
    return {}


def _freq_line(tr: I18n, count: int) -> str:
    """"4 times this week". Singular gets its own key: "1 times this week" on the Blockers card
    is the sort of thing a judge reads out loud."""
    if count == 1:
        return tr.t("ui:blocker.freq_one", default="once this week", n=count)
    return tr.t("ui:blocker.freq", default="{{n}} times this week", n=count)


def _chip(entry, tr: I18n, states: dict[str, NodeState]) -> str:
    """The reason chip, in the student's language and inside the phone's text budget.

    `SetEntry.reason` is the engine's English rendering of the same idea. We rebuild it here from
    the same `slot` and the same node/misconception rather than shipping `reason` verbatim, for
    two reasons: `reason` is not localised, and it is written at graph-audit length. "On your path
    to The gradient descent update step" is 44 characters and wraps to three lines on a 390px
    frame, which turns the chip into the loudest thing on the card. The short forms below all fit
    on one line, and none of them lose information the card does not already carry.
    """
    node_id = entry.item.node_id
    if entry.slot == "blocker":
        state = states.get(node_id) or NodeState(node_id=node_id)
        tag = state.misconceptions[0] if state.misconceptions else None
        return f'{tr.t("ui:slot.blocker")} · {tr.misconception_short(tag, node_id)}'
    if entry.slot == "review":
        return tr.t("ui:slot.review", default="Review")
    if entry.slot == "new":
        return f'{tr.t("ui:slot.new")} · {tr.node_short_title(node_id)}'
    # No node name at all: the target is already the headline of the Path tab, and repeating it on
    # six chips is the longest, least informative string in the payload.
    return tr.t("ui:slot.goal_link_short", default="On your path")


def _today_payload(session: Session, states: dict[str, NodeState], tr: I18n) -> dict:
    entries = session.daily_set()
    problems = [{
        "item_id": e.item.item_id,
        "math": tr.item_stem(e.item),
        "chip": _chip(e, tr, states),
        "slot": e.slot,
        "is_blocker": e.slot == "blocker",
        "done": e.item.item_id in session.done_items,
    } for e in entries]
    minutes = int(round(len(entries) * MINUTES_PER_PROBLEM))
    return {
        "headline": tr.t("ui:today.headline", default="{{n}} problems · {{minutes}} min",
                         n=len(entries), minutes=minutes),
        "problems": problems,
    }


def _goal_payload(session: Session, states: dict[str, NodeState], tr: I18n, now) -> dict:
    graph = session.graph
    ancestors = goal_ancestors(graph)
    ui = _ui_statuses(session, states, now)
    skills_away = sum(1 for n in ancestors if ui[n] != "mastered")

    # Pace: remaining skills over the rate at which this student has been mastering them. With no
    # evidence of any mastery in the window we assume one skill a week rather than dividing by
    # zero. That is a floor, not a forecast, and it is deliberately pessimistic.
    then = replay(session.attempts, graph, upto=now - timedelta(days=WEEK_DAYS))
    mastered_then = sum(1 for n in ancestors
                        if status(n, graph, then, now - timedelta(days=WEEK_DAYS)) == "mastered")
    mastered_now = len(ancestors) - skills_away
    gained = max(mastered_now - mastered_then, 0)
    rate_per_day = (gained / WEEK_DAYS) if gained else (1.0 / WEEK_DAYS)
    days = max(1, int(math.ceil(skills_away / rate_per_day))) if skills_away else 0

    return {
        "node_id": graph.target_node,
        "title": tr.node_title(graph.target_node),
        # The target card has one line for the goal. The node title is written for a graph audit
        # ("The gradient descent update step"); the stage this node lives in is already the topic
        # name a student would use for it, so the short form is derived rather than authored.
        "short_title": tr.stage_name(graph.target_node)
                       or tr.node_short_title(graph.target_node),
        "skills_away": skills_away,
        "skills_away_line": _skills_away_line(tr, skills_away),
        # One key, one whole sentence. The count sits wherever the language puts it.
        "pace_line": tr.t("ui:progress.days_at_pace",
                          default="about {{n}} days at your pace", n=days),
    }


def _skills_away_line(tr: I18n, count: int) -> str:
    """"18 skills away", rendered server-side so Hindi is not left assembling a sentence.

    `skills_away` stays in the payload as an int for the progress ring; this is the phrase beside
    it. Handing the frontend a bare number and expecting it to build the sentence is the one place
    the contract asked the frontend to compute, and it does not survive a second language.
    """
    if count <= 0:
        return tr.t("ui:path.skills_away_none", default="You are there", n=count)
    if count == 1:
        return tr.t("ui:path.skills_away_one", default="1 skill away", n=count)
    return tr.t("ui:path.skills_away", default="{{n}} skills away", n=count)


def _streak_days(session: Session, now) -> int:
    """Consecutive days ending today or yesterday on which the student attempted something."""
    days = {a.ts.date() for a in session.attempts}
    if not days:
        return 0
    cursor = now.date()
    if cursor not in days:
        cursor = cursor - timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor = cursor - timedelta(days=1)
    return count


def _you_payload(session: Session, states: dict[str, NodeState], tr: I18n, now) -> dict:
    ui = _ui_statuses(session, states, now)
    window_start = now - timedelta(days=WEEK_DAYS)
    recent = [a for a in session.attempts if a.ts >= window_start]
    accuracy = round(100 * sum(1 for a in recent if a.correct) / len(recent)) if recent else 0
    return {
        "streak_line": tr.t("ui:progress.streak", count=_streak_days(session, now)),
        "mastered": sum(1 for s in ui.values() if s == "mastered"),
        "accuracy": f"{accuracy}%",
    }


def _state_payload(session: Session, tr: I18n) -> dict:
    now = session.now
    states = session.states(now)
    return {
        "student_id": STUDENT_ID,
        "lang": tr.lang,
        # How far this student has got, decided server-side like everything else, and it is what
        # the frontend routes on. See `Session.flow`: not signed up -> signup, signed up with no
        # course -> courses, both -> ready.
        "flow": session.flow,
        "goal": _goal_payload(session, states, tr, now),
        "today": _today_payload(session, states, tr),
        "path": {
            "stages": _stages_payload(session, states, tr, now),
            "legend": _legend(tr),
        },
        "blockers": _blockers_payload(session, states, tr, now),
        "you": _you_payload(session, states, tr, now),
        "ui": _ui_strings(tr),
    }


# --------------------------------------------------------------------------- courses

def _course_card(course: dict, tr: I18n) -> dict:
    """One course card, every string already translated and `selectable` already decided.

    `selectable` comes from `app.state.is_selectable` and is NOT `state == "active"` recomputed
    here, which is the same rule the select endpoint refuses on. The frontend is handed the answer
    rather than the input, so a change to what "playable" means moves one predicate and not three.

    `detail` is the one line under the card. A `coming_soon` course gets the localised "Coming
    soon" and NOT an invented duration: the manifest carries no `hours` for those courses because
    nobody has estimated one, and a plausible "about 20 days" on a course that does not exist is a
    number we would be making up on a screen that is claiming to be honest about what is built.
    """
    course_id = str(course["id"])
    selectable = is_selectable(course)
    skills = int(course.get("skills") or 0)
    return {
        "id": course_id,
        "title": tr.t(f"{CATALOGUE_PREFIX}title.{course_id}",
                      default=course.get("title") or course_id),
        "subtitle": tr.t(f"{CATALOGUE_PREFIX}subtitle.{course_id}",
                         default=course.get("subtitle") or ""),
        "ends_at": tr.t(f"{CATALOGUE_PREFIX}ends_at.{course_id}",
                        default=course.get("ends_at") or ""),
        "state": str(course.get("state") or ""),
        "skills": skills,
        # The sentence beside the number, rendered here for the same reason `skills_away_line` is:
        # Hindi does not put the count where English does, and a frontend concatenating "37" onto
        # a translated word is the bug this whole layer exists to prevent.
        "skills_line": tr.t("ui:course.skills", default="{{n}} skills", n=skills),
        "detail": (tr.t(f"{CATALOGUE_PREFIX}detail.{course_id}",
                        default=course.get("hours") or "")
                   if selectable else tr.t("ui:course.coming_soon", default="Coming soon")),
        "selectable": selectable,
    }


def _courses_payload(session: Session, tr: I18n) -> dict:
    return {
        "courses": [_course_card(c, tr) for c in load_courses(session.courses_path)],
        "selected": session.selected_course,
    }


def _find_course(session: Session, course_id: str) -> Optional[dict]:
    return next((c for c in load_courses(session.courses_path)
                 if str(c["id"]) == course_id), None)


# --------------------------------------------------------------------------- answers and maths

def _canon(text: str) -> str:
    """Whitespace-FREE comparison form.

    Used to match a diagnosis `failed_step` against the student's work lines. Collapsing runs of
    whitespace is not enough: the two strings differ by spaces INSIDE the expression, so
    "[ 2x - 6 - 2x + 1 ]" never matched "= [2x - 6 - 2x + 1]" and the failing line silently failed
    to highlight. Spacing carries no meaning in this comparison. Same fix as services.diagnose._canon.
    """
    return re.sub(r"\s+", "", (text or "").strip())


def _clean_latex(raw: Optional[str]) -> str:
    """Strip `$` wrappers and a restated `f'(x) =` label off a textbook answer key."""
    text = (raw or "").strip()
    if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
        text = text[1:-1].strip()
    text = text.rstrip(".").strip()
    if text.count("=") == 1:
        lhs, rhs = text.split("=")
        if rhs.strip() and len(lhs.strip()) <= 12:
            text = rhs.strip()
    return text


def _expected_latex(item: Item) -> str:
    """The answer the student should have reached, in its SIMPLEST form.

    The bank stores the quotient rule's own output, `2/(x-3) - (2x+1)/(x-3)^2`, which is correct
    and useless as a thing to show someone who just got it wrong. Simplifying gives `-7/(x-3)^2`,
    which is the line in the contract and the line in the demo script.
    """
    if item.answer_sympy:
        try:
            expr = load_answer(item.answer_kind or "expr", item.answer_sympy)
            return sp.latex(sp.simplify(expr))
        except Exception:                                        # noqa: BLE001
            pass
    return _clean_latex(item.answer_latex)


def _is_unsimplified(typed: str) -> bool:
    """Correct, but not in simplest form: `5x - 3x + 2` for `2x + 2`.

    Measured by operation count on the UNEVALUATED parse against the simplified expression. It has
    to be the unevaluated parse: sympy folds `5x - 3x + 2` into `2x + 2` at parse time, so the
    evaluated form has already lost the evidence. This is the third Result state in
    docs/ui-spec.md section 3, and it is the exact case the model got wrong in section 14.
    """
    text = normalise_typed(typed)
    if not text:
        return False
    try:
        return sp.count_ops(PU(text)) > sp.count_ops(sp.simplify(P(text)))
    except Exception:                                            # noqa: BLE001
        return False


def _hints(item: Item, tr: I18n) -> list[dict]:
    """All four rungs at once; the frontend reveals them one at a time.

    Authored per node in `data/content/hints.json`, which is why rung 2 can be the actual rule in
    maths rather than a generic nudge. A node with no authored ladder falls back to the generic
    i18n rungs plus this item's own answer key, so the accordion is never empty.
    """
    rungs = _hint_rungs(item.node_id)
    if rungs:
        out = []
        for i, rung in enumerate(rungs[:4], start=1):
            english = rung.get("text") or ""
            is_math = bool(rung.get("is_math", False))
            # A maths rung is LaTeX. It is passed through untouched in every language, which is
            # the same rule as item stems: translate the prose, never the notation.
            out.append({"n": i,
                        "text": english if is_math else tr.hint_text(english),
                        "is_math": is_math})
        return out

    answer = _expected_latex(item)
    return [
        {"n": 1, "text": tr.t("ui:hint.level_1"), "is_math": False},
        {"n": 2, "text": f'{tr.t("ui:hint.level_2")} {tr.node_title(item.node_id)}.',
         "is_math": False},
        {"n": 3, "text": tr.t("ui:hint.level_3"), "is_math": False},
        {"n": 4, "text": f'{tr.t("ui:hint.level_4")}: ${answer}$', "is_math": True},
    ]


# --------------------------------------------------------------------------- request bodies

class StartRequest(BaseModel):
    item_id: str


class GradeRequest(BaseModel):
    item_id: str
    typed_answer: str = ""
    hint_level: int = 0
    channel: str = "typed"
    work_lines: list[str] = []


class DiagnoseRequest(BaseModel):
    attempt_id: str
    item_id: str
    work_text: str = ""


class CommitRequest(BaseModel):
    attempt_id: str
    blame_confirmed: Optional[bool] = None


class SignupRequest(BaseModel):
    """The whole sign up form. One optional display name.

    **There is no password field and no email field, and neither is ever to be added here.** We are
    not building auth for a hackathon; a form that renders a credential input and then ignores what
    is typed into it is a lie told to the person using it, and worse on a stage than an admitted
    placeholder. If a real account system ever lands, it lands as a real one.
    """
    name: str = ""


# --------------------------------------------------------------------------- endpoints

@app.get("/api/state")
def get_state(lang: str = Query("en")) -> dict:
    session = get_session()
    return _state_payload(session, _tr(session, lang))


# --------------------------------------------------------------------------- onboarding

@app.get("/api/courses")
def get_courses(lang: str = Query("en")) -> dict:
    """The course manifest, localised, with `selectable` already computed."""
    session = get_session()
    return _courses_payload(session, _tr(session, lang))


@app.post("/api/session/signout")
def session_signout(lang: str = "en") -> dict:
    """Clear the session, so the student is signed up no longer and `flow` reads `signup` again.

    Same effect as POST /api/reset?full=1 today, deliberately kept as its own endpoint: reset is a
    demo affordance that KEEPS the course selected so rehearsals skip onboarding, and signing out
    is a student action that must not. Collapsing them would make one of the two wrong the first
    time either changes.
    """
    get_session().reset(full=True)
    return {"ok": True, "next": "signup", "flow": get_session().flow}


@app.post("/api/session/signup")
def session_signup(body: Optional[SignupRequest] = None, lang: str = Query("en")) -> dict:
    """The placeholder sign up. Creates or resets the session and records a display name.

    No password, no email, no persistence beyond this process. See `SignupRequest` for why that is
    a decision rather than an omission.
    """
    session = get_session()
    tr = _tr(session, lang)
    session.sign_up(body.name if body else None)
    return {
        "student_id": STUDENT_ID,
        # A student who skipped the optional field still needs something to be called on screen,
        # and it has to be translated: rendering the fallback here rather than in the frontend
        # keeps "" and "Avinash" the same shape as far as the caller is concerned.
        "name": session.student_name or tr.t("ui:signup.default_name", default="Student"),
        "next": "courses",
    }


@app.post("/api/courses/{course_id}/select")
def select_course(course_id: str, lang: str = Query("en")) -> dict:
    """Install a course's seeded history and hand back the whole `GET /api/state` body.

    One call, not two: the frontend goes straight from the course card to Today without a second
    round trip that could half-fail and leave a selected course with no state on screen.

    An unknown or `coming_soon` id is HTTP 200 with an `error` and the selection UNCHANGED, per the
    contract's degrade-never-blank rule. The card is already non-interactive, so this refusal is
    the backstop rather than the gate, and it refuses on `is_selectable`, the same predicate that
    made the card non-interactive in the first place.
    """
    session = get_session()
    tr = _tr(session, lang)
    course = _find_course(session, course_id)
    if course is None:
        return {"error": f"unknown course {course_id}", "selected": session.selected_course}
    if not is_selectable(course):
        return {"error": "not available yet", "selected": session.selected_course}
    session.install_course(course_id)
    return _state_payload(session, tr)


def _position_of(session: Session, item_id: str) -> tuple[int, int]:
    """`(index, total)` for the set this item belongs to, 1-based.

    An OPEN "Fix this" set wins over today's set, and the two genuinely overlap: the blocker slot
    means a remediation item is often in both. The student who just tapped "Fix this" is inside
    that set of three, so telling them "2 of 6" would be counting the wrong set. Today's set is
    the fallback, and an item in neither (the demo item, a twin) is a set of one rather than a
    fake position inside a set it does not belong to.
    """
    if item_id in session.focus_set:
        return session.focus_set.index(item_id) + 1, len(session.focus_set)
    ids = [entry.item.item_id for entry in session.daily_set()]
    if item_id in ids:
        return ids.index(item_id) + 1, len(ids)
    return 1, 1


def _solve_payload(session: Session, tr: I18n, item: Item) -> dict:
    """The Solve screen for one item. `/solve/start`, `/blockers/{id}/start` and `/solve/twin`
    all return this, so the frontend has one shape to render and not three."""
    index, total = _position_of(session, item.item_id)
    return {
        "item_id": item.item_id,
        "index": index,
        "total": total,
        "problem_label": tr.t("ui:set.counter", index=index, total=total),
        "math": tr.item_stem(item),
        "hints": _hints(item, tr),
    }


@app.post("/api/solve/start")
def solve_start(body: StartRequest, lang: str = Query("en")) -> dict:
    session = get_session()
    tr = _tr(session, lang)
    item = session.item(body.item_id)
    if item is None:
        return {"item_id": body.item_id, "index": 0, "total": 0, "problem_label": "",
                "math": "", "hints": [], "error": f"unknown item {body.item_id}"}
    return _solve_payload(session, tr, item)


@app.post("/api/blockers/{node_id}/start")
def blocker_start(node_id: str, lang: str = Query("en")) -> dict:
    """"Fix this": a short set of drills for one blocked node, and the first one ready to solve.

    **No status gate.** A blocker is actionable even when its node is `locked`, per
    docs/ui-spec.md section 3: locking gates NEW LEARNING only, and the practice is the repair.
    Gating this on status would give the Blockers tab a "come back later" state, which is the one
    state it must never have.

    The items are chosen by the same difficulty targeting the daily set uses, so a remediation
    drill is pitched where the student can actually succeed rather than at whatever came first in
    the bank.
    """
    session = get_session()
    tr = _tr(session, lang)
    if node_id not in session.graph.nodes:
        return {"node_id": node_id, "item_ids": [], "item_id": "", "index": 0, "total": 0,
                "problem_label": "", "math": "", "hints": [],
                "error": f"unknown node {node_id}"}

    states = session.states()
    now = session.now
    chosen: list[Item] = []
    used: set[str] = set()
    while len(chosen) < FOCUS_SET_SIZE:
        item = pick_item(node_id, states, session.items_by_node, now, used)
        if item is None:
            break
        chosen.append(item)
        used.add(item.item_id)

    if not chosen:
        return {"node_id": node_id, "item_ids": [], "item_id": "", "index": 0, "total": 0,
                "problem_label": "", "math": "", "hints": [],
                "error": f"no items in the bank for {node_id}"}

    session.focus_node = node_id
    session.focus_set = [item.item_id for item in chosen]
    return {
        "node_id": node_id,
        "item_ids": list(session.focus_set),
        **_solve_payload(session, tr, chosen[0]),
    }


@app.post("/api/solve/twin")
def solve_twin(body: StartRequest, lang: str = Query("en")) -> dict:
    """An isomorphic twin of this item: same shape, different numbers, answer recomputed by the CAS.

    Rung 4 of the hint ladder reveals the full method, which on its own buys relief at the cost of
    a hole in the evidence. The twin closes it: the reveal is followed by the same problem with
    new numbers, and THAT attempt is real evidence.

    `{"available": false}` at HTTP 200 is a normal outcome, not an error. `pipeline.twin.twin_of`
    returns None for every OpenStax item (no `check` spec to recompute from) and for specs with no
    movable literal, so about 81% of generated items can twin and the rest cannot. The UI simply
    does not offer the button.
    """
    session = get_session()
    tr = _tr(session, lang)
    record = session.raw_item(body.item_id)
    if record is None:
        return {"available": False, "item_id": body.item_id,
                "error": f"unknown item {body.item_id}"}

    # `hints.json` decides whether a node offers twins at all. Asking here rather than promising
    # in rung 4 and failing at the tap is the difference between a feature and a broken button.
    if not _node_offers_twin(record.get("node_id", "")):
        return {"available": False, "item_id": body.item_id}

    twin = twin_of(record, session.next_twin_seed(body.item_id))
    if twin is None:
        return {"available": False, "item_id": body.item_id}

    item = session.register_twin(twin)
    return {"available": True, "twin_of": body.item_id, **_solve_payload(session, tr, item)}


def _node_offers_twin(node_id: str) -> bool:
    """Does this node's authored rung 4 promise a twin?

    The promise is encoded in the ENGLISH `text` of the last rung ("Then try a twin problem with
    new numbers"), which `pipeline/build_hints.py` writes only for nodes whose bank can actually
    produce one. We read `text` and never `text_hi`, because the English field is the source and a
    translation would not carry the word.
    """
    rungs = _hint_rungs(node_id)
    if not rungs:
        return False
    return "twin" in (rungs[-1].get("text") or "").lower()


@app.post("/api/solve/transcribe")
async def solve_transcribe(
    item_id: str = Form(...),
    image: Optional[UploadFile] = File(None),
    lang: str = Query("en"),
) -> dict:
    """Photo path. Returns the transcription for this item from the demo cache.

    There is no live Vision call here on purpose: the credits are nearly gone and section 15's
    finding (Vision inserted a spurious `^-` in one of four real samples) is precisely why the
    Check screen exists. A miss returns HTTP 200 with `lines: []` and an `error`, so the UI falls
    back to the typed path rather than dead-ending.

    The upload is still drained and its bytes are still unused, but it is now MEASURED on the way
    past. The frontend crops and downscales before it uploads (see `IMAGE_LIMITS` in
    `app/static/app.js`), and the only way to know what real phones on venue wifi actually send
    through that path is to write down what arrived.
    """
    if image is not None:
        _log_upload(await image.read(), image, item_id)   # drained; the bytes are unused offline

    lines = _cached_transcription(item_id)
    if lines is None:
        tr = _tr(get_session(), lang)
        return {"lines": [], "ocr_seconds": 0.0, "source": "cache",
                "error": tr.t("ui:error.generic",
                              default="No cached transcription for this problem.")}
    return {"lines": lines, "ocr_seconds": 0.0, "source": "cache"}


def _enable_upload_logging() -> None:
    """Make sure this module's INFO lines actually reach a stream.

    Uvicorn configures `uvicorn`, `uvicorn.error` and `uvicorn.access`, and leaves the ROOT logger
    at its default: level WARNING and no handler. An `INFO` record from `app.server` was therefore
    filtered out before it reached anything, and the upload line below was written and never seen,
    which is the same as not writing it. Uvicorn's own handler is borrowed when there is one so the
    line is formatted like every other line in the terminal.

    `propagate` is deliberately left alone: pytest's `caplog` listens on the root logger, and
    turning propagation off here would make the log unobservable in exactly the place it is
    asserted on.
    """
    log.setLevel(logging.INFO)
    if log.handlers:
        return
    for handler in logging.getLogger("uvicorn.error").handlers:
        log.addHandler(handler)
    if not log.handlers and not logging.getLogger().handlers:
        log.addHandler(logging.StreamHandler())


_enable_upload_logging()


def _log_upload(raw: bytes, image: UploadFile, item_id: str) -> None:
    """One line per photo: which problem it was for, how big it is, and what it says it is.

    This is the only visibility we have into what a phone actually sends. It never raises: a
    malformed header is logged as unknown dimensions, because a photo that cannot be measured is
    still a photo we can serve a cached transcription for.
    """
    size = _image_dimensions(raw)
    log.info(
        "transcribe upload: item=%s filename=%r content_type=%r bytes=%d dimensions=%s",
        item_id, image.filename, image.content_type, len(raw),
        f"{size[0]}x{size[1]}" if size else "unknown",
    )


def _image_dimensions(raw: bytes) -> Optional[tuple[int, int]]:
    """`(width, height)` from the file header alone, for JPEG and PNG. No decode, no Pillow.

    Cheap enough to run on every upload: PNG carries the size at a fixed offset, and a JPEG needs
    a walk over its marker segments to the start-of-frame header, which is a few dozen bytes in.
    Anything else, including HEIC, returns None rather than a guess.
    """
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if raw[:2] != b"\xff\xd8":
        return None

    #: SOF0 to SOF15, minus the four codes in that range that are not frame headers:
    #: DHT (c4), JPG (c8) and DAC (cc). DNL (dc) is outside the range.
    frame_markers = {m for m in range(0xC0, 0xD0)} - {0xC4, 0xC8, 0xCC}
    pos = 2
    while pos + 4 <= len(raw):
        if raw[pos] != 0xFF:
            return None
        marker = raw[pos + 1]
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:                     # start of scan: the header is behind us
            return None
        length = int.from_bytes(raw[pos + 2:pos + 4], "big")
        if marker in frame_markers and pos + 9 <= len(raw):
            return (int.from_bytes(raw[pos + 7:pos + 9], "big"),
                    int.from_bytes(raw[pos + 5:pos + 7], "big"))
        if length < 2:
            return None
        pos += 2 + length
    return None


def _cached_transcription(item_id: str) -> Optional[list[str]]:
    """The working to show on the Check screen for this problem, or None if we have none.

    `entries` is consulted FIRST and deliberately: it holds the wrong-answer transcript that the
    diagnosis is cached against, and a correct transcript from `transcriptions` shadowing it would
    silently retire the demo climax. Only `gen-der.quotient-rule-5` is in both, and it must resolve
    to the one with the sign error.
    """
    cache = load_cache(DIAGNOSIS_CACHE_PATH)
    wanted = {item_id, PHOTO_ALIASES.get(item_id, item_id)}
    for entry in cache.get("entries", {}).values():
        if entry.get("item_id") in wanted:
            return _work_lines(entry.get("student_work"))

    for candidate in wanted:
        work = cache.get("transcriptions", {}).get(candidate)
        if work:
            return _work_lines(work)
    return None


def _work_lines(work: Optional[str]) -> list[str]:
    return [line.strip() for line in (work or "").splitlines() if line.strip()]


class RevealRequest(BaseModel):
    item_id: str


@app.post("/api/solve/reveal")
def solve_reveal(body: RevealRequest) -> dict:
    """DEMO AFFORDANCE, not a product feature.

    Returns a correct and a deliberately wrong answer for an item so the presenter can skip ahead
    without typing algebra on stage. The frontend triggers it with a triple tap on the problem,
    which is deliberately hard to hit by accident and invisible to anyone not told about it.

    Both values are VERIFIED through the real grader before being returned, so the cheat cannot
    quietly hand back something that fails, which would be worse than no cheat at all. Where the
    demo cache holds the student working for this item, its final line is preferred as the wrong
    answer, so the cheat leads into the cached diagnosis rather than an unrelated miss.
    """
    session = get_session()
    item = session.item_bank.get(body.item_id)
    if item is None:
        return {"error": "unknown item", "correct": None, "wrong": None}

    correct = item.answer_sympy or item.answer_latex or ""
    if not correct or not grade(item, correct).correct:
        return {"error": "no verifiable answer", "correct": None, "wrong": None}

    wrong = _cached_wrong_answer(body.item_id)
    if not (wrong and not grade(item, wrong).correct):
        wrong = None
        for candidate in (f"({correct}) + 1", f"-({correct})", f"2*({correct})"):
            if not grade(item, candidate).correct:
                wrong = candidate
                break
    return {"correct": correct, "wrong": wrong, "error": None}


def _cached_wrong_answer(item_id: str) -> Optional[str]:
    """The last line of the cached working, which is the answer that working arrives at."""
    cache = load_cache(DIAGNOSIS_CACHE_PATH)
    for entry in cache.get("entries", {}).values():
        if entry.get("item_id") != item_id:
            continue
        lines = [ln.strip() for ln in (entry.get("student_work") or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1].lstrip("=").strip()
    return None


@app.post("/api/solve/grade")
def solve_grade(body: GradeRequest, lang: str = Query("en")) -> dict:
    """The CAS verdict. `needs_diagnosis` is FALSE for every correct answer, always."""
    session = get_session()
    tr = _tr(session, lang)
    item = session.item(body.item_id)
    if item is None:
        return {"attempt_id": "", "correct": False, "verdict": tr.t("ui:error.generic"),
                "expected_latex": "", "unsimplified": False, "needs_diagnosis": False,
                "error": f"unknown item {body.item_id}"}

    result = grade(item, body.typed_answer)
    expected = _expected_latex(item)

    if result.error:
        # We could not grade. That is NOT a verdict on the student, so nothing is recorded, no
        # diagnosis is asked for, and the caller is told why.
        return {"attempt_id": "", "correct": False, "verdict": tr.t("ui:error.generic"),
                "expected_latex": expected, "unsimplified": False, "needs_diagnosis": False,
                "error": NOT_CHECKABLE if result.error == NOT_CHECKABLE else result.error}

    correct = bool(result.correct)
    unsimplified = correct and _is_unsimplified(body.typed_answer)

    pending = session.next_attempt(
        item_id=item.item_id,
        node_id=item.node_id,
        correct=correct,
        hint_level=max(0, min(4, int(body.hint_level or 0))),
        channel=body.channel if body.channel in ("photo", "typed", "mcq") else "typed",
        typed_answer=body.typed_answer,
        work_lines=tuple(body.work_lines or ()),
        unsimplified=unsimplified,
        # Component skills the item exercises. The engine intersects these with
        # `graph.encompasses`, so only credit that is both possible and used is handed out.
        used_nodes=tuple(item.encompasses) if correct else (),
    )

    return {
        "attempt_id": pending.attempt_id,
        "correct": correct,
        "verdict": tr.t("ui:verdict.correct") if correct else tr.t("ui:verdict.not_quite"),
        "expected_latex": expected,
        "unsimplified": unsimplified,
        # THE RULE: the model is never asked about a correct answer. See section 14 finding 2.
        "needs_diagnosis": not correct,
    }


@app.post("/api/solve/diagnose")
def solve_diagnose(body: DiagnoseRequest, lang: str = Query("en")) -> dict:
    session = get_session()
    tr = _tr(session, lang)
    pending = session.pending.get(body.attempt_id)

    if pending is not None and pending.correct:
        # The gate, enforced at the boundary as well as by the caller: a correct answer is never
        # shown to the model, whatever the frontend asks for.
        return _diagnosis_error(tr, "the answer was correct; no diagnosis is asked for")

    work = body.work_text
    if not work and pending is not None:
        work = "\n".join(pending.work_lines)
    hit = cache_lookup(DIAGNOSIS_CACHE_PATH, body.item_id, work)

    if hit is not None:
        diagnosis = hit
    else:
        api_key = os.environ.get("SARVAM_API_KEY")
        item = session.item(body.item_id) or DEMO_ITEM
        if not api_key:
            log.warning("diagnosis cache miss for item %s and no SARVAM_API_KEY; "
                        "returning a graceful error payload", body.item_id)
            return _diagnosis_error(
                tr, "no cached diagnosis for this work and no api key for a live call")
        diagnosis = live_diagnose(item, work, _expected_latex(item), session.graph, api_key,
                                 cache_path=DIAGNOSIS_CACHE_PATH)
        if diagnosis.error and not diagnosis.student_message:
            return _diagnosis_error(tr, diagnosis.error)

    return _diagnosis_payload(session, tr, pending, body, diagnosis, work)


def _cached_entry(item_id: str, work: str) -> Optional[dict]:
    """The RAW cache record for this work, or None.

    `cache_lookup` returns a `Diagnosis`, and `Diagnosis` models only the fields the diagnosis
    service itself produces, so the hand-translated `student_message_hi` never survives it. Rather
    than widen a dataclass we do not own, the raw record is fetched alongside, following the same
    two-step resolution `cache_lookup` uses: the (item, work) key first, then the work-only alias
    that exists because the demo item may be given a different id by whoever wires the UI.
    """
    cache = load_cache(DIAGNOSIS_CACHE_PATH)
    entry = cache.get("entries", {}).get(cache_key(item_id, work))
    if entry is None:
        alias = cache.get("by_work", {}).get(work_key(work))
        entry = cache.get("entries", {}).get(alias) if alias else None
    return entry


def _diagnosis_body(tr: I18n, diagnosis: Diagnosis, item_id: str, work: str) -> str:
    """The student-facing explanation, in the student's language where one was written.

    Section 16: in every case where the model routed blame to the wrong node, its message was
    still right. The message is the trustworthy half of a diagnosis and the sentence the whole
    product exists to produce, so it is the last string that should be showing in English.

    A LIVE diagnosis has no `_hi`: it was generated seconds ago by a model we asked in English.
    That falls back to `student_message` rather than blanking, because a correct explanation in
    the wrong language is worth far more than an empty panel.
    """
    if tr.lang == "en":
        return diagnosis.student_message
    entry = _cached_entry(item_id, work) or {}
    localised = (entry.get("diagnosis") or {}).get(f"student_message_{tr.lang}")
    return localised or diagnosis.student_message


def _diagnosis_error(tr: I18n, message: str) -> dict:
    """A payload the Result screen can still render. The demo degrades, it never blanks."""
    return {
        "blamed_node": None,
        "headline": tr.t("ui:diagnosis.heading"),
        "body": "",
        "recurrence": "",
        "failed_line_index": -1,
        "consequence": "",
        "from_cache": False,
        "latency_s": 0.0,
        "error": message,
    }


def _diagnosis_payload(session: Session, tr: I18n, pending: Optional[Pending],
                       body: DiagnoseRequest, diagnosis: Diagnosis, work: str) -> dict:
    blamed = diagnosis.blamed_node
    item = session.item(body.item_id) or DEMO_ITEM

    # The model returns `misconception_tag: null` on the demo case; see `_default_tag_for_node`
    # for why an untagged blame would silently empty the Blockers tab.
    tag = diagnosis.misconception_tag or (_default_tag_for_node(blamed) if blamed else None)

    lines = list(pending.work_lines) if (pending and pending.work_lines) else [
        line for line in work.splitlines() if line.strip()
    ]
    failed_index = _failed_line_index(lines, diagnosis.failed_step)

    if pending is not None:
        pending.blamed_node = blamed
        pending.blame_confidence = float(diagnosis.confidence or 0.0)
        pending.misconception_tag = tag
        pending.failed_step = diagnosis.failed_step
        if not pending.work_lines and lines:
            pending.work_lines = tuple(lines)

    # Headline vs body, per the contract's own example: the headline says what the student got
    # RIGHT, the body says what went wrong. Section 16: when routing was wrong the message was
    # still right every time, so the message is the half we show most prominently.
    if blamed and blamed != item.node_id:
        headline = tr.t("ui:diagnosis.topic_ok", default="Your {{node}} is correct.",
                        node=_lower_first(tr.node_title(item.node_id)))
    else:
        headline = tr.t("ui:diagnosis.heading")

    return {
        "blamed_node": blamed,
        "headline": headline,
        "body": _diagnosis_body(tr, diagnosis, body.item_id, work),
        "recurrence": _recurrence_line(session, tr, blamed, tag),
        "failed_line_index": failed_index,
        "consequence": _consequence_line(session, tr, pending, blamed),
        "from_cache": bool(diagnosis.from_cache),
        "latency_s": float(diagnosis.latency_s or 0.0),
    }


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _failed_line_index(lines: list[str], failed_step: Optional[str]) -> int:
    if not failed_step or not lines:
        return -1
    target = _canon(failed_step)
    for i, line in enumerate(lines):
        if _canon(line) == target:
            return i
    for i, line in enumerate(lines):
        if target in _canon(line) or _canon(line) in target:
            return i
    return -1


#: Weekday index (Monday = 0) to its i18n key, matching `datetime.weekday()`.
_DAY_KEYS = ("ui:day.monday", "ui:day.tuesday", "ui:day.wednesday", "ui:day.thursday",
             "ui:day.friday", "ui:day.saturday", "ui:day.sunday")


def _when_label(session: Session, tr: I18n, when) -> Optional[str]:
    """"yesterday", or a weekday name, or None when it is too long ago to name a day.

    A weekday only identifies a date inside one week: "same mistake as Tuesday" about something
    three weeks old is not a smaller claim, it is a wrong one. Past that window we return None and
    the caller drops the sentence, which is also why the authored key set stops at seven days plus
    `yesterday`. The Blockers card still carries "4 times this week" for the longer view.
    """
    days = (session.now.date() - when.date()).days
    if days <= 1:
        return tr.t("ui:day.yesterday", default="yesterday")
    if days <= WEEK_DAYS:
        return tr.t(_DAY_KEYS[when.weekday()], default=when.strftime("%A"))
    return None


def _recurrence_line(session: Session, tr: I18n, blamed: Optional[str],
                     tag: Optional[str]) -> str:
    """"Same mistake as Tuesday." Empty string on a first occurrence, per the contract."""
    if not blamed:
        return ""
    prior = [a for a in session.attempts
             if a.blamed_node == blamed and (tag is None or a.misconception_tag == tag)]
    if not prior:
        return ""
    label = _when_label(session, tr, max(prior, key=lambda a: a.ts).ts)
    if label is None:
        return ""
    return tr.t("ui:diagnosis.recurrence", default="Same mistake as {{when}}.", when=label)


def _consequence_line(session: Session, tr: I18n, pending: Optional[Pending],
                      blamed: Optional[str]) -> str:
    """What committing this attempt would do to the blamed node, computed by actually doing it.

    A dry run over a throwaway copy of the log, not a rule of thumb: the sentence on screen is
    then the same transition the commit will produce, because it came from the same fold.
    """
    if not blamed or pending is None:
        return ""
    before_states = session.states(pending.ts)
    trial = replay(list(session.attempts) + [pending.to_attempt()], session.graph,
                   upto=pending.ts)

    # Visibility is judged on the POST-commit state, because that is the Path the student will be
    # looking at when they read this sentence. Naming a node that is not rendered anywhere is
    # worse than saying less: it sends them hunting for something that is not on the map.
    visible = _visible_nodes(session, trial)

    changes = {}
    for node_id in visible:
        was = UI_STATE[status(node_id, session.graph, before_states, pending.ts)]
        now_ = UI_STATE[status(node_id, session.graph, trial, pending.ts)]
        if was != now_:
            changes[node_id] = (was, now_)

    if changes:
        # The blamed node's own transition is the sentence the contract shows. When the blame
        # lands on a node that was already marked down, the visible cost is downstream instead
        # (the topics that depended on it just locked), so we name the steepest of those.
        if blamed in changes:
            node_id = blamed
        else:
            node_id = min(changes,
                          key=lambda n: (STATE_RANK[changes[n][1]] - STATE_RANK[changes[n][0]], n))
        was, now_ = changes[node_id]
        return tr.t("ui:diagnosis.consequence",
                    default="{{node}} drops from {{before}} to {{after}}.",
                    node=tr.node_title(node_id),
                    before=tr.status_label(was),
                    after=tr.status_label(now_))

    # Nothing visible moved. Say what did happen to the blamed node, which is always visible after
    # a blame because the blame is what puts it on the Blockers list. `apply_blame` collapses its
    # stability, so "comes back tomorrow" is a description of the schedule, not a promise.
    if blamed not in visible:
        return ""
    return tr.t("ui:diagnosis.consequence_stays",
                default="{{node}} stays in needs-work and is scheduled again tomorrow.",
                node=tr.node_title(blamed))


@app.post("/api/solve/commit")
def solve_commit(body: CommitRequest, lang: str = Query("en")) -> dict:
    """Append one row to the log, then recompute everything by replay. Nothing is mutated."""
    session = get_session()
    tr = _tr(session, lang)
    pending = session.pending.get(body.attempt_id)
    if pending is None:
        return {"xp": 0, "node_changes": [], "unlocked": [], "session_done": False,
                "error": f"unknown attempt {body.attempt_id}"}
    if pending.committed:
        return {"xp": 0, "node_changes": [], "unlocked": [], "session_done": False,
                "error": f"attempt {body.attempt_id} was already committed"}

    # `blame_confirmed` is the "was this actually your mistake?" tap: true, false, or null when it
    # was never asked. All three are RECORDED, and none of them currently changes the applied
    # blame.
    #
    # A "no" is the single most valuable signal this product collects: it is a labelled
    # disagreement with the diagnosis, on real work, from the one person who knows the answer.
    # Section 14 put diagnosis accuracy at 83%, and these taps are the only way that number ever
    # gets measured against something other than our own test cases. So it is logged and kept on
    # the attempt's extras rather than being thrown away as a UI event.
    #
    # It does NOT unwind the graph update yet, deliberately. Acting on one tap would let a student
    # dismiss every diagnosis they dislike and quietly opt out of the blame propagation that is
    # the entire product, and we have no data yet on how often a "no" is right. Collect first,
    # act when the log says what acting should do.
    if body.blame_confirmed is False:
        log.warning("blame contested: attempt %s, student rejected blame on %s (item %s). "
                    "Recorded, not applied.",
                    pending.attempt_id, pending.blamed_node, pending.item_id)
    attempt = pending.to_attempt(accept_blame=True)

    before_states = session.states(attempt.ts)
    before = {n: status(n, session.graph, before_states, attempt.ts) for n in session.graph.nodes}
    node_state_before = before_states.get(attempt.node_id) or NodeState(node_id=attempt.node_id)
    was_due = node_state_before.last_seen is None or is_due(node_state_before, attempt.ts)
    prior_on_node = sum(1 for a in session.attempts if a.node_id == attempt.node_id)

    session.append(attempt)
    pending.committed = True
    session.done_items.add(attempt.item_id)
    session.set_extras(attempt.attempt_id,
                       blame_confirmed=body.blame_confirmed,
                       failed_step=pending.failed_step,
                       corrected_step=pending.corrected_step,
                       answer_given=pending.typed_answer)

    after_states = session.states(attempt.ts)
    after = {n: status(n, session.graph, after_states, attempt.ts) for n in session.graph.nodes}

    node_changes = [{"node_id": n, "from": UI_STATE[before[n]], "to": UI_STATE[after[n]]}
                    for n in session.graph.nodes if before[n] != after[n]]
    unlocked = [n for n in session.graph.nodes
                if before[n] == "locked" and after[n] != "locked"]

    xp = xp_for(attempt, session.graph, before[attempt.node_id], was_due,
                prior_attempts_on_node=prior_on_node)
    session.commits.append(CommitRecord(attempt_id=attempt.attempt_id, node_id=attempt.node_id,
                                        xp=xp, node_changes=node_changes, unlocked=unlocked))

    done_ids = {e.item.item_id for e in session.daily_set()}
    return {
        "xp": xp,
        "node_changes": node_changes,
        "unlocked": unlocked,
        "session_done": bool(done_ids) and done_ids <= session.done_items,
    }


@app.get("/api/session/complete")
def session_complete(lang: str = Query("en")) -> dict:
    session = get_session()
    tr = _tr(session, lang)
    now = session.now

    start_states = replay(session.seeded_attempts, session.graph, upto=now)
    end_states = session.states(now)
    started = {n: status(n, session.graph, start_states, now) for n in session.graph.nodes}
    ended = {n: status(n, session.graph, end_states, now) for n in session.graph.nodes}

    touched: list[str] = []
    for record in session.commits:
        for node_id in [record.node_id] + [c["node_id"] for c in record.node_changes]:
            if node_id not in touched:
                touched.append(node_id)

    nodes = [{
        "node_id": n,
        "name": tr.node_title(n),
        "state": UI_STATE[ended[n]],
        "just_lit": ended[n] == "mastered" and started[n] != "mastered",
    } for n in touched]

    unlocked = {n for record in session.commits for n in record.unlocked}
    blockers = _blockers_payload(session, end_states, tr, now)
    if blockers:
        focus = blockers[0]["name"]
    else:
        tomorrow = session.tomorrow_set()
        focus = tr.node_title(tomorrow[0].item.node_id) if tomorrow else tr.node_title(
            session.graph.target_node)

    return {
        "xp_total": sum(record.xp for record in session.commits),
        "unlocked_count": len(unlocked),
        "nodes": nodes,
        "tomorrow": tr.t("ui:session.tomorrow", default="Tomorrow starts with {{focus}}.",
                         focus=_lower_first(focus)),
    }


@app.post("/api/reset")
def reset(lang: str = Query("en"), full: int = Query(0)) -> dict:
    """Back to the seeded 5-day history. Returns exactly what GET /api/state returns.

    The DEFAULT keeps the course already selected, so `flow` comes back `ready` and a demo
    rehearsal does not walk the two onboarding screens on every run. That is the button you press
    between takes.

    `?full=1` clears the session instead: no sign up, no course, an empty log, and `flow` comes
    back `signup`. That is the button you press when the flow itself is the thing being shown.
    """
    session = get_session()
    session.reset(full=bool(full))
    return _state_payload(session, _tr(session, lang))


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Anything that got past a handler. 500 with an `error` string, per the contract, so the
    frontend can keep showing the last good state instead of a stack trace."""
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


# --------------------------------------------------------------------------- static frontend

_STATIC = Path(__file__).resolve().parent / "static"
if _STATIC.is_dir():
    # Mounted AFTER the API routes so it can never shadow /api. Owned by another agent; if the
    # directory is not there yet the server still runs.
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
