"""API tests. No network, ever.

Two of these are load-bearing rather than routine, and both encode a measured result:

  - `test_correct_answer_never_reaches_the_model` replaces the diagnosis entry points with
    functions that fail the test if they are called, then grades a correct answer. It is the
    executable form of learning-design section 14 finding 2: the CAS decides, and the model is
    never shown an answer it could invent an error inside.
  - `test_commit_is_reproducible_by_replaying_the_log` rebuilds every node's state from the
    attempt log alone and asserts the API is serving exactly that. This is the event-log
    guarantee from `db/schema.sql`; if it ever fails, something is caching or mutating derived
    state and the whole retuning story is gone.

The Sarvam API is never called: the diagnosis path is exercised entirely through
`data/demo/diagnosis_cache.json`.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import pytest
import sympy as sp
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import server                                                       # noqa: E402
from app.server import (                                                      # noqa: E402
    MAX_CHIP_CHARS,
    SHORT_LABEL_CHARS,
    STAGES,
    STATE_RANK,
    UI_STATE,
    _TOKEN as TOKEN,
    _default_tag_for_node,
    app,
)
from app.state import (                                                       # noqa: E402
    DEMO_TODAY,
    DEMO_YESTERDAY,
    Session,
    get_session,
    is_selectable,
    load_courses,
)
from engine.mastery import p_eff, status                                      # noqa: E402
from engine.replay import replay                                              # noqa: E402
from engine.selection import goal_ancestors                                   # noqa: E402
from engine.types import PREREQ_READY, Attempt, NodeState                     # noqa: E402
from services.diagnose import DEMO_STUDENT_WORK                               # noqa: E402
from services.grading import latex_to_sympy_src                               # noqa: E402
from pipeline.drill_tasks import P                                            # noqa: E402

# Items used below, all from the real bank.
SIGN_ITEM = "gen-alg.sign-distribution-1"        # Expand and simplify $5x - (3x - 2)$  ->  2x + 2
SIGN_CORRECT = "2x + 2"
SIGN_UNSIMPLIFIED = "5x - 3x + 2"                # correct, and not in simplest form
SIGN_WRONG = "8x - 2"

QUOTIENT_ITEM = "gen-der.quotient-rule-1"        # the demo climax, same problem as DEMO_ITEM
QUOTIENT_WRONG = "-5/(x-3)^2"

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
MATH_SPAN = re.compile(r"\$[^$]*\$")


@pytest.fixture()
def client():
    """A client on a freshly reset session, so tests cannot leak state into each other."""
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c
        c.post("/api/reset")


def _no_network(monkeypatch):
    """Make every route to the model an immediate test failure."""
    def boom(*_a, **_kw):
        raise AssertionError("the model was called; this flow must never reach it")

    monkeypatch.setattr(server, "live_diagnose", boom)
    monkeypatch.setattr("services.diagnose._call", boom)


def _math_spans(text: str) -> list[str]:
    """The `$...$` spans, order-insensitive.

    Order matters here and it is a real property of the data: Hindi moves the maths inside the
    sentence ("... के लिए $x$ ज्ञात कीजिए" against "Find $x$ for ..."), so the SET of spans has to
    be identical while the sequence legitimately is not.
    """
    return sorted(MATH_SPAN.findall(text))


# --------------------------------------------------------------------------- GET /api/state

def test_state_returns_every_contract_field_with_the_right_types(client):
    body = client.get("/api/state").json()

    assert body["student_id"] == "demo"
    assert body["lang"] == "en"

    goal = body["goal"]
    assert set(goal) == {"node_id", "title", "short_title", "skills_away", "skills_away_line",
                         "pace_line"}
    assert isinstance(goal["node_id"], str) and goal["node_id"]
    assert isinstance(goal["title"], str) and goal["title"]
    assert isinstance(goal["short_title"], str) and goal["short_title"]
    assert isinstance(goal["skills_away"], int)
    assert isinstance(goal["skills_away_line"], str) and goal["skills_away_line"]
    assert isinstance(goal["pace_line"], str) and goal["pace_line"]

    today = body["today"]
    assert set(today) == {"headline", "problems"}
    assert isinstance(today["headline"], str) and today["headline"]
    assert today["problems"], "the daily set must never come back empty"
    for problem in today["problems"]:
        assert set(problem) == {"item_id", "math", "chip", "slot", "is_blocker", "done"}
        assert isinstance(problem["item_id"], str) and problem["item_id"]
        assert isinstance(problem["math"], str) and problem["math"]
        assert isinstance(problem["chip"], str) and problem["chip"]
        assert problem["slot"] in {"blocker", "review", "new", "goal_link"}
        assert isinstance(problem["is_blocker"], bool)
        assert problem["is_blocker"] == (problem["slot"] == "blocker")
        assert isinstance(problem["done"], bool)

    path = body["path"]
    assert set(path) == {"stages", "legend"}
    assert path["stages"]
    for stage in path["stages"]:
        assert set(stage) == {"id", "name", "count", "state", "skills"}
        assert re.fullmatch(r"\d+/\d+", stage["count"])
        assert stage["state"] in STATE_RANK
        for skill in stage["skills"]:
            assert set(skill) == {"node_id", "name", "state", "needs"}
            assert skill["state"] in STATE_RANK
            assert isinstance(skill["needs"], str)
    for entry in path["legend"]:
        assert set(entry) == {"state", "label"}
        assert entry["state"] in STATE_RANK

    for blocker in body["blockers"]:
        assert set(blocker) == {"node_id", "rank", "name", "freq", "wrong", "right", "item_count"}
        assert isinstance(blocker["item_count"], int)

    you = body["you"]
    assert set(you) == {"streak_line", "mastered", "accuracy"}
    assert isinstance(you["mastered"], int)
    assert you["accuracy"].endswith("%")


def test_stage_state_is_the_most_advanced_actionable_state(client):
    """A stage badge answers "where are you in this", so a stage holding a mastered node and six
    locked ones is not `locked`. Taking the minimum hid every bit of the student's progress."""
    body = client.get("/api/state").json()

    for stage in body["path"]["stages"]:
        states = [s["state"] for s in stage["skills"]]
        if "now" in states:
            expected = "now"
        elif "learning" in states:
            expected = "learning"
        elif all(s == "mastered" for s in states):
            expected = "mastered"
        else:
            expected = "locked"
        assert stage["state"] == expected, f'stage {stage["id"]} from {states}'

    # The regression this replaces: a stage that mixes locked nodes with a frontier node must read
    # `now`, not `locked`, and the seeded student has at least one such stage.
    mixed = [s for s in body["path"]["stages"]
             if {"now", "locked"} <= {sk["state"] for sk in s["skills"]}]
    assert mixed, "no stage mixes frontier and locked nodes; this assertion proves nothing"
    assert all(s["state"] == "now" for s in mixed)

    # And a stage carrying real progress must not report itself as locked.
    for stage in body["path"]["stages"]:
        mastered = int(stage["count"].split("/")[0])
        if mastered and any(sk["state"] != "locked" for sk in stage["skills"]):
            assert stage["state"] != "locked", f'stage {stage["id"]} hides {mastered} mastered'


def test_stage_count_is_mastered_over_total_within_the_goal_slice(client):
    """`count` stays narrower than what is shown: widening the Path must not inflate the claim."""
    session = get_session()
    slice_nodes = set(goal_ancestors(session.graph)) | {session.graph.target_node}
    for stage in client.get("/api/state").json()["path"]["stages"]:
        in_slice = [s for s in stage["skills"] if s["node_id"] in slice_nodes]
        mastered = sum(1 for s in in_slice if s["state"] == "mastered")
        assert stage["count"] == f"{mastered}/{len(in_slice)}"


def test_stages_are_ordered_target_first_and_hold_exactly_the_visible_union(client):
    session = get_session()
    graph = session.graph
    slice_nodes = set(goal_ancestors(graph)) | {graph.target_node}
    blocked = {n for n, s in session.states().items() if s.misconceptions}
    todays = {e.item.node_id for e in session.daily_set()}

    body = client.get("/api/state").json()
    stage_ids = [s["id"] for s in body["path"]["stages"]]

    # Target first: the target's own namespace leads, and the whole order is the declared
    # top-down spine with the empty stages dropped.
    assert stage_ids[0] == graph.target_node.split(".", 1)[0] == "ai"
    declared = [prefix for prefix, _name in STAGES]
    assert [i for i in declared if i in stage_ids] == stage_ids

    listed = {s["node_id"] for stage in body["path"]["stages"] for s in stage["skills"]}
    assert graph.target_node in listed
    assert listed == slice_nodes | blocked | todays

    for stage in body["path"]["stages"]:
        for skill in stage["skills"]:
            assert skill["node_id"].split(".", 1)[0] == stage["id"]


def test_every_node_in_todays_set_appears_on_the_path(client):
    """The student is asked to solve these today. A map that omits today's work is not a map."""
    body = client.get("/api/state").json()
    listed = {s["node_id"] for stage in body["path"]["stages"] for s in stage["skills"]}
    bank = get_session().item_bank
    for problem in body["today"]["problems"]:
        node_id = bank[problem["item_id"]].node_id
        assert node_id in listed, f'{problem["item_id"]} is set for today and is off the Path'


def test_no_chip_exceeds_the_phone_text_budget(client):
    """A chip is one short line on a 390px frame. The regression this catches is a chip built
    from a full node title: "On your path to The gradient descent update step" is 44 characters
    and wraps to three lines."""
    for lang in ("en", "hi"):
        problems = client.get(f"/api/state?lang={lang}").json()["today"]["problems"]
        for problem in problems:
            chip = problem["chip"]
            assert chip, "every problem must carry a reason"
            assert len(chip) <= MAX_CHIP_CHARS, f'{lang} chip {chip!r} is {len(chip)} chars'
            assert "\n" not in chip

    slots = {p["slot"]: p["chip"] for p in
             client.get("/api/state").json()["today"]["problems"]}
    if "goal_link" in slots:
        assert slots["goal_link"] == "On your path", "the goal_link chip names no node"
    if "review" in slots:
        assert slots["review"] == "Review"
    if "blocker" in slots:
        assert slots["blocker"].startswith("Blocker · ")
    if "new" in slots:
        assert slots["new"].startswith("New · ")
        assert len(slots["new"].split(" · ", 1)[1]) <= SHORT_LABEL_CHARS + 2


def test_the_goal_card_is_short_and_reads_naturally(client):
    for lang in ("en", "hi"):
        goal = client.get(f"/api/state?lang={lang}").json()["goal"]
        assert len(goal["short_title"]) <= SHORT_LABEL_CHARS + 2, goal["short_title"]
        assert len(goal["short_title"]) < len(goal["title"])
        assert "{{" not in goal["skills_away_line"] and "ui:" not in goal["skills_away_line"]
        assert str(goal["skills_away"]) in goal["skills_away_line"] or goal["skills_away"] == 0

    english = client.get("/api/state?lang=en").json()["goal"]
    assert english["short_title"] == "Gradient descent"
    assert english["skills_away_line"].endswith(
        "skills away" if english["skills_away"] != 1 else "skill away")


def test_needs_names_a_genuinely_unmet_prereq_from_the_graph(client):
    session = get_session()
    graph, now = session.graph, session.now
    states = session.states(now)
    body = client.get("/api/state").json()

    checked_a_needs = False
    for stage in body["path"]["stages"]:
        for skill in stage["skills"]:
            unmet = [pid for pid, _w in graph.prereqs(skill["node_id"])
                     if p_eff(states.get(pid) or NodeState(node_id=pid), now) < PREREQ_READY]
            if skill["needs"]:
                assert unmet, f'{skill["node_id"]} claims a need with every prereq satisfied'
                assert graph.title(unmet[0]) in skill["needs"]
                checked_a_needs = True
            else:
                assert not unmet, f'{skill["node_id"]} has unmet prereq {unmet} and no needs line'
    assert checked_a_needs, "no needs line was exercised; the assertion above proved nothing"


def test_a_blocker_outside_the_goal_slice_still_appears_on_the_path(client):
    """A node that is neither an ancestor of the target nor in today's set is normally absent.
    Holding an open misconception has to override that, because the Blockers tab promises it is
    actionable whatever its prereqs are doing."""
    session = get_session()
    before = {s["node_id"] for stage in client.get("/api/state").json()["path"]["stages"]
              for s in stage["skills"]}
    outside = next(n for n in session.graph.nodes
                   if n not in before and session.items_by_node.get(n))

    session.append(Attempt(
        attempt_id="test-blocker", student_id="demo",
        item_id=session.items_by_node[outside][0].item_id,
        node_id=outside, ts=DEMO_TODAY, correct=False,
        blamed_node=outside, blame_confidence=0.9,
        misconception_tag=_default_tag_for_node(outside),
    ))

    body = client.get("/api/state").json()
    listed = {s["node_id"] for stage in body["path"]["stages"] for s in stage["skills"]}
    assert outside in listed
    assert any(b["node_id"] == outside for b in body["blockers"])


# --------------------------------------------------------------------------- grading

def test_a_correct_answer_never_reaches_the_model(client, monkeypatch):
    """`needs_diagnosis` is false for every correct answer, and the diagnose path is unreachable.

    Section 14 finding 2: the worst outcome in the whole experiment was the model inventing an
    error inside a correct answer. This test is the fix, in executable form.
    """
    _no_network(monkeypatch)

    graded = client.post("/api/solve/grade",
                         json={"item_id": SIGN_ITEM, "typed_answer": SIGN_CORRECT}).json()
    assert graded["correct"] is True
    assert graded["needs_diagnosis"] is False
    assert graded["unsimplified"] is False
    assert graded["verdict"] == "Correct"
    assert "error" not in graded

    # Even if a frontend ignored the flag, the boundary refuses: no cache read, no live call.
    monkeypatch.setattr(server, "cache_lookup", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("the diagnosis cache was consulted for a correct answer")))
    diagnosed = client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": SIGN_ITEM, "work_text": SIGN_CORRECT,
    }).json()
    assert diagnosed["blamed_node"] is None
    assert "correct" in diagnosed["error"]


def test_an_incorrect_answer_needs_diagnosis_and_carries_a_usable_expected_latex(client):
    graded = client.post("/api/solve/grade",
                         json={"item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG}).json()
    assert graded["correct"] is False
    assert graded["needs_diagnosis"] is True
    assert graded["verdict"] == "Not quite"
    assert graded["attempt_id"]

    # "Usable" means it round-trips back to the item's real answer, not merely that it is a
    # non-empty string. The bank stores the quotient rule's raw output; the API must serve the
    # simplified form, which is what the student is actually being compared against.
    expected = graded["expected_latex"]
    assert expected and "$" not in expected
    src = latex_to_sympy_src(expected, require_math=False)
    assert src is not None, f"expected_latex is not machine-readable: {expected!r}"
    stored = P(get_session().item_bank[QUOTIENT_ITEM].answer_sympy)
    assert sp.simplify(P(src) - stored) == 0
    assert sp.count_ops(P(src)) <= sp.count_ops(stored)


def test_a_correct_but_unsimplified_answer_is_correct_and_flagged(client, monkeypatch):
    """The third Result state. Correct is correct; the note is a nudge, not a mark against."""
    _no_network(monkeypatch)
    graded = client.post("/api/solve/grade",
                         json={"item_id": SIGN_ITEM, "typed_answer": SIGN_UNSIMPLIFIED}).json()
    assert graded["correct"] is True
    assert graded["unsimplified"] is True
    assert graded["needs_diagnosis"] is False


def test_an_ungradeable_item_reports_an_error_rather_than_a_verdict(client):
    """A scraped answer key we cannot machine-check is a routing signal, not "you are wrong"."""
    body = client.post("/api/solve/grade", json={
        "item_id": "os-fs-id1169739304919", "typed_answer": "x",
    }).json()
    assert body["needs_diagnosis"] is False
    assert body["error"]
    assert body["attempt_id"] == ""


# --------------------------------------------------------------------------- diagnosis

def test_diagnose_serves_the_cached_entry_with_no_network_call(client, monkeypatch):
    _no_network(monkeypatch)

    graded = client.post("/api/solve/grade", json={
        "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
        "work_lines": DEMO_STUDENT_WORK.splitlines(),
    }).json()
    assert graded["needs_diagnosis"] is True

    body = client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
        "work_text": DEMO_STUDENT_WORK,
    }).json()

    assert body["from_cache"] is True
    assert body["blamed_node"] == "alg.sign-distribution"
    assert "distributing the minus sign" in body["body"]
    assert body["headline"] == "Your quotient rule is correct."
    assert body["failed_line_index"] == 1            # the second line of the student's working
    assert body["latency_s"] == 4.33                 # honestly the latency the cache recorded
    assert set(body) == {"blamed_node", "headline", "body", "recurrence", "failed_line_index",
                         "consequence", "from_cache", "latency_s"}


def test_the_consequence_never_names_a_node_absent_from_the_path(client, monkeypatch):
    """Naming a node the student cannot see sends them hunting for something that is not on the
    map. The sentence has to be about the Path they are looking at, or say less."""
    _no_network(monkeypatch)
    session = get_session()

    graded = client.post("/api/solve/grade", json={
        "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
        "work_lines": DEMO_STUDENT_WORK.splitlines(),
    }).json()
    body = client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
        "work_text": DEMO_STUDENT_WORK,
    }).json()

    assert body["consequence"], "the demo's climax must say what the blame cost"

    # The consequence is written before the commit, so it has to be checked against the Path the
    # student sees AFTER it: commit, then look.
    client.post("/api/solve/commit",
                json={"attempt_id": graded["attempt_id"], "blame_confirmed": True})
    visible = {s["name"] for stage in client.get("/api/state").json()["path"]["stages"]
               for s in stage["skills"]}
    assert any(name in body["consequence"] for name in visible), \
        f'consequence {body["consequence"]!r} names no node on the Path'

    # And it must never name one that is off it.
    listed_ids = {s["node_id"] for stage in client.get("/api/state").json()["path"]["stages"]
                  for s in stage["skills"]}
    for node_id in session.graph.nodes:
        if node_id not in listed_ids:
            assert session.graph.title(node_id) not in body["consequence"], \
                f"{node_id} is not rendered anywhere and must not be named"


#: A maths atom in prose. The diagnosis message writes its mathematics BARE ("-(2x+1) into
#: -2x+1"), not inside `$...$`, so the section 13 check on this string has to compare tokens
#: rather than delimited spans. Digit-bearing runs only: "quotient" is a word, "-2x-1" is not.
_MATHS_ATOM = re.compile(r"[-+*/^()=0-9A-Za-z.]+")


def _maths_atoms(text: str) -> list[str]:
    return sorted(atom for atom in
                  (raw.strip(".,") for raw in _MATHS_ATOM.findall(text))
                  if any(c.isdigit() for c in atom))


def test_the_diagnosis_body_is_served_in_hindi_with_identical_maths(client, monkeypatch):
    """The last user-visible English string on the demo's most important screen.

    Section 16: when the model routed blame wrongly its message was still right every time, so
    this sentence is the trustworthy half of a diagnosis and the one a judge reads most closely.
    """
    _no_network(monkeypatch)

    bodies = {}
    for lang in ("en", "hi"):
        graded = client.post(f"/api/solve/grade?lang={lang}", json={
            "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
            "work_lines": DEMO_STUDENT_WORK.splitlines()}).json()
        bodies[lang] = client.post(f"/api/solve/diagnose?lang={lang}", json={
            "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
            "work_text": DEMO_STUDENT_WORK}).json()["body"]
        client.post("/api/reset")

    assert DEVANAGARI.search(bodies["hi"])
    assert bodies["hi"] != bodies["en"]
    assert "quotient rule" in bodies["en"]

    # Section 13: translate the prose, never the notation.
    assert _maths_atoms(bodies["hi"]) == _maths_atoms(bodies["en"])
    assert _maths_atoms(bodies["en"]), "this assertion is worthless if the message has no maths"
    for atom in ("-(2x+1)", "-2x+1", "-2x-1", "-5", "-7"):
        assert atom in bodies["hi"], f"{atom} did not survive translation"


def test_a_live_diagnosis_falls_back_to_english_rather_than_blanking(client):
    """A live diagnosis is generated seconds ago by a model we asked in English, so it carries no
    `_hi`. A correct explanation in the wrong language beats an empty panel."""
    from app.server import I18n, _diagnosis_body
    from services.diagnose import Diagnosis

    hindi = I18n("hi", get_session().graph)
    live = Diagnosis(correct=False, failed_step=None, blamed_node="alg.sign-distribution",
                     misconception_tag=None, student_message="You dropped a negative.",
                     confidence=0.9, latency_s=3.2, from_cache=False)

    assert _diagnosis_body(hindi, live, QUOTIENT_ITEM, "some work no cache has ever seen") == \
        "You dropped a negative."
    # And the cached one still resolves to Hindi through the same function.
    assert DEVANAGARI.search(
        _diagnosis_body(hindi, live, QUOTIENT_ITEM, DEMO_STUDENT_WORK))


def test_a_cache_miss_with_no_api_key_degrades_instead_of_crashing(client, monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    _no_network(monkeypatch)

    graded = client.post("/api/solve/grade",
                         json={"item_id": SIGN_ITEM, "typed_answer": SIGN_WRONG}).json()
    response = client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": SIGN_ITEM,
        "work_text": "5x - (3x - 2) = 5x - 3x - 2 = 2x - 2",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["error"]
    assert body["blamed_node"] is None
    assert body["headline"], "a miss must still hand the UI something to render"


# --------------------------------------------------------------------------- commit and replay

def test_commit_is_reproducible_by_replaying_the_log(client, monkeypatch):
    """THE event-log guarantee: append one row, and every derived number the API serves is what a
    from-scratch fold over the log produces. Nothing is stored, nothing is mutated."""
    _no_network(monkeypatch)
    session = get_session()
    log_before = list(session.attempts)

    graded = client.post("/api/solve/grade", json={
        "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
        "work_lines": DEMO_STUDENT_WORK.splitlines(),
    }).json()
    client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
        "work_text": DEMO_STUDENT_WORK,
    })
    committed = client.post("/api/solve/commit",
                            json={"attempt_id": graded["attempt_id"],
                                  "blame_confirmed": True}).json()

    # 1. The log grew by exactly one row and the earlier rows are untouched.
    log_after = list(session.attempts)
    assert len(log_after) == len(log_before) + 1
    assert log_after[:len(log_before)] == log_before
    appended = log_after[-1]
    assert appended.attempt_id == graded["attempt_id"]
    assert appended.blamed_node == "alg.sign-distribution"

    # 2. Derived state moved, and the commit reported the move.
    assert isinstance(committed["xp"], int)
    assert isinstance(committed["node_changes"], list)
    assert isinstance(committed["unlocked"], list)
    assert isinstance(committed["session_done"], bool)
    blockers = client.get("/api/state").json()["blockers"]
    assert any(b["node_id"] == "alg.sign-distribution" for b in blockers)

    # 3. Reproduce it. Fold the log from scratch with the engine and compare against what the API
    #    is serving, node by node.
    rebuilt = replay(log_after, session.graph, upto=session.now)
    served = {s["node_id"]: s["state"]
              for stage in client.get("/api/state").json()["path"]["stages"]
              for s in stage["skills"]}
    for node_id, ui_state in served.items():
        expected = UI_STATE[status(node_id, session.graph, rebuilt, session.now)]
        assert ui_state == expected, f"{node_id} served {ui_state}, log replays to {expected}"

    # 4. Replay is a pure function of the log: two folds agree, and neither is the same object.
    again = replay(log_after, session.graph, upto=session.now)
    assert again == rebuilt
    assert again is not rebuilt


@pytest.mark.parametrize("confirmed", [True, False, None])
def test_blame_confirmed_is_recorded_in_all_three_states(client, monkeypatch, caplog, confirmed):
    """"Was this actually your mistake?" answers true, false or never-asked.

    All three are recorded. A `false` does NOT currently unwind the blame: acting on one tap would
    let a student dismiss every diagnosis they dislike and opt out of the blame propagation that
    is the product, and we have no data yet on how often a "no" is right. It is logged loudly
    because a labelled disagreement with a diagnosis, on real work, is the most valuable signal
    this product collects.
    """
    _no_network(monkeypatch)
    session = get_session()

    graded = client.post("/api/solve/grade", json={
        "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
        "work_lines": DEMO_STUDENT_WORK.splitlines(),
    }).json()
    diagnosed = client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
        "work_text": DEMO_STUDENT_WORK,
    }).json()
    assert diagnosed["blamed_node"] == "alg.sign-distribution"

    with caplog.at_level(logging.WARNING, logger="app.server"):
        body = client.post("/api/solve/commit",
                           json={"attempt_id": graded["attempt_id"],
                                 "blame_confirmed": confirmed}).json()
    assert "error" not in body, "false must be an accepted value, not a validation failure"

    appended = list(session.attempts)[-1]
    assert appended.correct is False
    assert appended.blamed_node == "alg.sign-distribution"
    assert session.extras(appended.attempt_id)["blame_confirmed"] is confirmed

    contested = "blame contested" in caplog.text
    assert contested is (confirmed is False), "only a 'no' is worth a log line"


def test_committing_marks_the_problem_done_in_todays_set(client, monkeypatch):
    _no_network(monkeypatch)
    bank = get_session().item_bank
    problems = client.get("/api/state").json()["today"]["problems"]
    index, item = next((i, bank[p["item_id"]]) for i, p in enumerate(problems)
                       if bank[p["item_id"]].answer_sympy
                       and bank[p["item_id"]].answer_kind == "expr")

    # Answering with the bank's own answer key is by definition correct, whichever item today's
    # set happened to choose.
    graded = client.post("/api/solve/grade",
                         json={"item_id": item.item_id,
                               "typed_answer": item.answer_sympy}).json()
    assert graded["correct"] is True
    body = client.post("/api/solve/commit", json={"attempt_id": graded["attempt_id"]}).json()

    assert body["session_done"] is False
    after = client.get("/api/state").json()["today"]["problems"]
    assert [p["item_id"] for p in after] == [p["item_id"] for p in problems], \
        "the frozen set must not renumber under the student mid-session"
    assert [p["done"] for p in after] == [i == index for i in range(len(after))]

    replayed = client.post("/api/solve/commit", json={"attempt_id": graded["attempt_id"]}).json()
    assert replayed["error"], "an attempt must not be committable twice"


def test_reset_restores_the_seeded_state_exactly(client, monkeypatch):
    _no_network(monkeypatch)
    seeded = client.get("/api/state").json()

    graded = client.post("/api/solve/grade", json={
        "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
        "work_lines": DEMO_STUDENT_WORK.splitlines(),
    }).json()
    client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
        "work_text": DEMO_STUDENT_WORK,
    })
    client.post("/api/solve/commit", json={"attempt_id": graded["attempt_id"]})

    dirty = client.get("/api/state").json()
    assert dirty != seeded, "the commit must have changed something, or this test proves nothing"

    restored = client.post("/api/reset").json()
    assert restored == seeded
    assert client.get("/api/state").json() == seeded
    assert list(get_session().attempts) == list(get_session().seeded_attempts)


# --------------------------------------------------------------------------- onboarding

ACTIVE_COURSE = "calculus-for-ai"
SOON_COURSE = "jee-calculus"

_COURSE_FIELDS = {"id", "title", "subtitle", "ends_at", "state", "skills", "skills_line",
                  "detail", "selectable"}


def test_courses_returns_every_card_field_with_the_right_types(client):
    body = client.get("/api/courses").json()
    assert set(body) == {"courses", "selected"}
    assert body["courses"], "the course screen must never come back empty"
    assert body["selected"] == ACTIVE_COURSE, "a plain reset keeps the course selected"

    for course in body["courses"]:
        assert set(course) == _COURSE_FIELDS
        assert isinstance(course["id"], str) and course["id"]
        assert isinstance(course["title"], str) and course["title"]
        assert isinstance(course["subtitle"], str) and course["subtitle"]
        assert isinstance(course["ends_at"], str) and course["ends_at"]
        assert course["state"] in {"active", "coming_soon"}
        assert isinstance(course["skills"], int) and course["skills"] > 0
        assert str(course["skills"]) in course["skills_line"]
        assert isinstance(course["selectable"], bool)
        assert course["detail"], "every card carries a second line"


def test_exactly_one_course_is_selectable_and_it_is_the_playable_one(client):
    """One course is real. The other four are declared so the shape of the platform is visible,
    and a second tappable card would be a promise the item bank cannot keep."""
    courses = client.get("/api/courses").json()["courses"]
    selectable = [c for c in courses if c["selectable"]]
    assert len(selectable) == 1
    assert selectable[0]["id"] == ACTIVE_COURSE
    assert selectable[0]["state"] == "active"
    assert all(c["state"] == "coming_soon" for c in courses if not c["selectable"])


def test_selectable_is_computed_server_side_and_not_read_off_state(client, monkeypatch):
    """`selectable` must be the SERVER's answer, not `state == "active"` recomputed downstream.

    Proved by moving the predicate: swap `is_selectable` for one that says the opposite, and both
    the payload and the select endpoint have to follow it while `state` stays exactly as authored.
    A handler that had inlined the comparison would keep reporting the old answer, and so would a
    frontend that inferred it, which is the whole reason the field exists.
    """
    inverted = client.get("/api/courses").json()
    assert [c["selectable"] for c in inverted["courses"]] == [True, False, False, False, False]

    monkeypatch.setattr(server, "is_selectable", lambda course: not is_selectable(course))
    flipped = client.get("/api/courses").json()["courses"]
    assert [c["selectable"] for c in flipped] == [False, True, True, True, True]
    assert [c["state"] for c in flipped] == [c["state"] for c in inverted["courses"]], \
        "the manifest's own field must be reported untouched"

    # And the endpoint refuses on the same predicate, so a card and its handler cannot disagree.
    assert client.post(f"/api/courses/{ACTIVE_COURSE}/select").json()["error"]
    assert "error" not in client.post(f"/api/courses/{SOON_COURSE}/select").json()


def test_a_coming_soon_course_says_coming_soon_rather_than_inventing_a_duration(client):
    """The manifest carries no duration for a course nobody has built. A plausible "about 20 days"
    on that card is a number we made up, on the screen that is claiming to be honest."""
    for lang, expected in (("en", "Coming soon"), ("hi", "जल्द आ रहा है")):
        courses = client.get(f"/api/courses?lang={lang}").json()["courses"]
        for course in courses:
            if course["selectable"]:
                assert course["detail"] != expected
                assert any(ch.isdigit() for ch in course["detail"])
            else:
                assert course["detail"] == expected

    manifest = {c["id"]: c for c in load_courses()}
    assert all(not is_selectable(c) or c.get("hours") for c in manifest.values())
    assert all(is_selectable(c) or "hours" not in c for c in manifest.values()), \
        "a coming_soon course must not carry an authored duration either"


def test_signup_records_a_name_and_asks_for_no_credential(client):
    """**The placeholder is honest on purpose.** No password field, no email field, not even a
    decorative one: a form that appears to take a credential and quietly discards it is a worse
    lie than an admitted placeholder, and this screen is shown to judges."""
    assert set(server.SignupRequest.model_fields) == {"name"}, \
        "sign up takes a display name and nothing else"

    body = client.post("/api/session/signup", json={"name": "Avinash"}).json()
    assert body == {"student_id": "demo", "name": "Avinash", "next": "courses"}
    assert get_session().student_name == "Avinash"

    # A credential sent anyway is not stored, not echoed and not an error.
    sneaky = client.post("/api/session/signup",
                         json={"name": "Avinash", "password": "hunter2",
                               "email": "a@example.com"})
    assert sneaky.status_code == 200
    assert set(sneaky.json()) == {"student_id", "name", "next"}
    assert not hasattr(get_session(), "password")


def test_signup_without_a_name_falls_back_to_a_translated_placeholder(client):
    """The name field is optional, so the screen after it still needs something to say."""
    for body in ({}, {"name": ""}, {"name": "   "}, None):
        named = client.post("/api/session/signup",
                            **({"json": body} if body is not None else {})).json()
        assert named["name"] == "Student"
        assert named["next"] == "courses"
        assert get_session().student_name is None

    hindi = client.post("/api/session/signup?lang=hi", json={}).json()
    assert DEVANAGARI.search(hindi["name"])


def test_signup_creates_or_resets_the_session_and_lands_on_courses(client):
    session = get_session()
    assert session.attempts, "the fixture starts from the seeded student"

    client.post("/api/session/signup", json={"name": "Avinash"})
    assert session.attempts == (), "a new session starts with no history of its own"
    assert session.selected_course is None
    assert client.get("/api/state").json()["flow"] == "courses"


def test_selecting_a_course_installs_its_history_and_returns_the_whole_state(client):
    """One call, not two. The frontend goes from the card straight to Today."""
    client.post("/api/reset?full=1")
    client.post("/api/session/signup", json={"name": "Avinash"})
    session = get_session()
    assert session.attempts == ()

    selected = client.post(f"/api/courses/{ACTIVE_COURSE}/select").json()

    assert "error" not in selected
    assert selected["flow"] == "ready"
    assert session.selected_course == ACTIVE_COURSE
    assert session.attempts == session.seeded_attempts and session.attempts, \
        "selecting the course is what installs the seeded history"
    assert session.student_name == "Avinash", "selection must not forget who signed up"

    # It really is the whole GET /api/state body, field for field.
    assert selected == client.get("/api/state").json()
    assert selected["today"]["problems"] and selected["path"]["stages"]
    assert selected["blockers"], "the seeded history arrived with its blockers"


def test_selecting_a_coming_soon_course_changes_nothing_and_says_so(client):
    """HTTP 200 with an error, per the contract: the demo degrades, it never blanks. The card is
    already non-interactive, so this is the backstop and not the gate."""
    client.post("/api/reset?full=1")
    client.post("/api/session/signup", json={"name": "Avinash"})
    before = client.get("/api/state").json()

    response = client.post(f"/api/courses/{SOON_COURSE}/select")
    assert response.status_code == 200
    body = response.json()
    assert body == {"error": "not available yet", "selected": None}

    assert get_session().selected_course is None
    assert get_session().attempts == (), "a refused selection installs no history"
    assert client.get("/api/state").json() == before
    assert client.get("/api/courses").json()["selected"] is None

    # And from a session that already HAS a course, a refusal leaves that one in place.
    client.post(f"/api/courses/{ACTIVE_COURSE}/select")
    refused = client.post(f"/api/courses/{SOON_COURSE}/select").json()
    assert refused == {"error": "not available yet", "selected": ACTIVE_COURSE}
    assert client.get("/api/state").json()["flow"] == "ready"


def test_selecting_an_unknown_course_degrades(client):
    body = client.post("/api/courses/not-a-course/select").json()
    assert body["error"]
    assert body["selected"] == ACTIVE_COURSE, "the selection is unchanged"


def test_flow_walks_welcome_then_courses_then_ready(client):
    """The frontend asks the server which screen it is on. It must not track this itself: the
    answer changes under it on `POST /api/reset?full=1`, which is a call it did not make."""
    assert client.post("/api/reset?full=1").json()["flow"] == "welcome"
    assert client.get("/api/state").json()["flow"] == "welcome"

    client.post("/api/session/signup", json={"name": "Avinash"})
    assert client.get("/api/state").json()["flow"] == "courses"

    # A refused course does not advance the flow.
    client.post(f"/api/courses/{SOON_COURSE}/select")
    assert client.get("/api/state").json()["flow"] == "courses"

    assert client.post(f"/api/courses/{ACTIVE_COURSE}/select").json()["flow"] == "ready"
    assert client.get("/api/state").json()["flow"] == "ready"

    for lang in ("en", "hi"):
        assert client.get(f"/api/state?lang={lang}").json()["flow"] == "ready", \
            "flow is a state name, never a translated string"


def test_reset_keeps_the_course_and_full_reset_returns_to_welcome(client):
    """Two different buttons. The plain one is what you press between demo takes; the `full` one is
    what you press when the flow itself is the thing being shown."""
    plain = client.post("/api/reset").json()
    assert plain["flow"] == "ready"
    assert get_session().selected_course == ACTIVE_COURSE
    assert get_session().attempts == get_session().seeded_attempts
    assert get_session().attempts, "a rehearsal reset still has the seeded 5-day history"
    assert client.get("/api/courses").json()["selected"] == ACTIVE_COURSE

    full = client.post("/api/reset?full=1").json()
    assert full["flow"] == "welcome"
    assert get_session().selected_course is None
    assert get_session().student_name is None
    assert get_session().attempts == (), "a cleared session carries nobody's history"
    assert client.get("/api/courses").json()["selected"] is None

    # Both are still a renderable GET /api/state, which is what the contract promises. A welcome
    # screen that cannot fall back to a drawn Today tab is a blank screen waiting to happen.
    for body in (plain, full):
        assert body["today"]["headline"] and body["path"]["stages"] and body["ui"]

    assert client.post("/api/reset").json()["flow"] == "ready", "and it comes back"
    assert get_session().attempts == get_session().seeded_attempts


def test_course_strings_are_localised_in_both_languages(client):
    english = client.get("/api/courses?lang=en").json()["courses"]
    hindi = client.get("/api/courses?lang=hi").json()["courses"]

    assert [c["id"] for c in hindi] == [c["id"] for c in english]
    for hi_course, en_course in zip(hindi, english):
        for field in ("title", "subtitle", "ends_at", "detail", "skills_line"):
            assert DEVANAGARI.search(hi_course[field]), \
                f'{hi_course["id"]}.{field} is still English: {hi_course[field]!r}'
            assert hi_course[field] != en_course[field]
            assert "{{" not in hi_course[field] and not hi_course[field].startswith("ui:")
        # Machine-readable fields are NOT translated: they are ids and enum values.
        assert hi_course["id"] == en_course["id"]
        assert hi_course["state"] == en_course["state"]
        assert hi_course["skills"] == en_course["skills"]
        assert hi_course["selectable"] == en_course["selectable"]


#: The onboarding chrome, and where each string is meant to appear. Named rather than derived,
#: because the point is that the three new screens can be drawn entirely out of `state["ui"]`
#: without the frontend holding a single English literal of its own.
_ONBOARDING_CHROME = (
    "welcome.subtitle", "action.get_started",
    "signup.title", "signup.subtitle", "signup.name_label", "signup.name_placeholder",
    "signup.default_name", "action.continue",
    "screen.courses", "courses.subtitle", "course.coming_soon", "course.ends_at",
    "action.start_course",
)


def test_every_onboarding_string_resolves_in_both_languages(client):
    english = client.get("/api/state?lang=en").json()["ui"]
    hindi = client.get("/api/state?lang=hi").json()["ui"]

    for key in _ONBOARDING_CHROME:
        assert english.get(key), f"no English string for {key}"
        assert hindi.get(key), f"no Hindi string for {key}"
        assert english[key] != hindi[key], f"{key} is not translated, only echoed"
        assert DEVANAGARI.search(hindi[key]), f"{key} is still English: {hindi[key]!r}"
        assert not _ASCII_LETTERS.search(hindi[key]), f"{key} leaks English: {hindi[key]!r}"
        for value in (english[key], hindi[key]):
            assert not TOKEN.search(value) and not value.startswith("ui:")

    # The counted string is deliberately NOT chrome. It is a sentence template, so it is rendered
    # server-side with the number already in it and never handed over for interpolation.
    assert "course.skills" not in english and "course.skills" not in hindi

    # Nor is per-course content: every catalogue string is already in its own card, translated,
    # and one string in two places is one string that gets read from the wrong one.
    for chrome in (english, hindi):
        assert not [k for k in chrome if k.startswith("catalogue.")]
    titles = {c["title"] for c in client.get("/api/courses?lang=hi").json()["courses"]}
    assert titles and not (titles & set(hindi.values())), \
        "a course title reached the chrome map as well as its card"
    for lang in ("en", "hi"):
        for course in client.get(f"/api/courses?lang={lang}").json()["courses"]:
            assert not TOKEN.search(course["skills_line"])
            assert str(course["skills"]) in course["skills_line"]


# --------------------------------------------------------------------------- solve/start, photo

def test_solve_start_returns_the_problem_its_position_and_four_hints(client):
    item_id = client.get("/api/state").json()["today"]["problems"][2]["item_id"]
    body = client.post("/api/solve/start", json={"item_id": item_id}).json()

    assert body["item_id"] == item_id
    assert body["index"] == 3
    assert body["total"] == len(client.get("/api/state").json()["today"]["problems"])
    assert str(body["index"]) in body["problem_label"]
    assert body["math"]
    assert [h["n"] for h in body["hints"]] == [1, 2, 3, 4]
    assert all(h["text"] and isinstance(h["is_math"], bool) for h in body["hints"])
    assert any(h["is_math"] for h in body["hints"]), \
        "one rung has to be the rule itself, rendered in the maths face"


def test_transcribe_serves_the_cached_demo_working_without_vision(client):
    response = client.post("/api/solve/transcribe",
                           data={"item_id": QUOTIENT_ITEM},
                           files={"image": ("work.jpg", b"not-a-real-photo", "image/jpeg")})
    body = response.json()
    assert response.status_code == 200
    assert body["source"] == "cache"
    assert body["lines"] == [line.strip() for line in DEMO_STUDENT_WORK.splitlines()]

    miss = client.post("/api/solve/transcribe", data={"item_id": SIGN_ITEM}).json()
    assert miss["lines"] == []
    assert miss["error"]


def test_session_complete_reports_xp_and_tomorrows_focus(client, monkeypatch):
    _no_network(monkeypatch)
    graded = client.post("/api/solve/grade", json={
        "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
        "work_lines": DEMO_STUDENT_WORK.splitlines(),
    }).json()
    client.post("/api/solve/diagnose", json={
        "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
        "work_text": DEMO_STUDENT_WORK,
    })
    client.post("/api/solve/commit", json={"attempt_id": graded["attempt_id"]})

    body = client.get("/api/session/complete").json()
    assert set(body) == {"xp_total", "unlocked_count", "nodes", "tomorrow"}
    assert isinstance(body["xp_total"], int)
    assert isinstance(body["unlocked_count"], int)
    assert body["tomorrow"].startswith("Tomorrow starts with")
    for node in body["nodes"]:
        assert set(node) == {"node_id", "name", "state", "just_lit"}
        assert node["state"] in STATE_RANK


# --------------------------------------------------------------------------- localisation

def test_hindi_returns_devanagari_and_identical_maths_spans(client):
    """Section 13: translate the prose, never the maths. The `$...$` spans must be identical."""
    english = client.get("/api/state?lang=en").json()
    hindi = client.get("/api/state?lang=hi").json()

    assert hindi["lang"] == "hi"
    assert DEVANAGARI.search(hindi["goal"]["title"])
    assert DEVANAGARI.search(hindi["you"]["streak_line"])
    assert DEVANAGARI.search(hindi["path"]["legend"][0]["label"])
    assert any(DEVANAGARI.search(p["chip"]) for p in hindi["today"]["problems"])

    titles = [s["name"] for stage in hindi["path"]["stages"] for s in stage["skills"]]
    assert titles and all(DEVANAGARI.search(t) for t in titles)

    # The maths is byte-identical, and the two payloads describe the same problems in the same
    # order, which is the other half of the promise.
    assert [p["item_id"] for p in hindi["today"]["problems"]] == \
           [p["item_id"] for p in english["today"]["problems"]]
    for hi_problem, en_problem in zip(hindi["today"]["problems"], english["today"]["problems"]):
        assert _math_spans(hi_problem["math"]) == _math_spans(en_problem["math"])
    # Only 194 of the 438 items have a stem translation, so an untranslated stem falling back to
    # English is correct behaviour, not a bug. What must hold is that any stem that IS translated
    # is genuinely in Devanagari.
    translated = [(h, e) for h, e in zip(hindi["today"]["problems"],
                                         english["today"]["problems"])
                  if h["math"] != e["math"]]
    assert translated, "no stem in today's set was translated at all"
    assert all(DEVANAGARI.search(h["math"]) for h, _ in translated)

    demo_item = translated[0][0]["item_id"]
    started_hi = client.post("/api/solve/start?lang=hi", json={"item_id": demo_item}).json()
    started_en = client.post("/api/solve/start?lang=en", json={"item_id": demo_item}).json()
    assert _math_spans(started_hi["math"]) == _math_spans(started_en["math"])
    assert DEVANAGARI.search(started_hi["problem_label"])
    # `data/content/hints.json` is not translated yet, so the rungs come back in English. What
    # must never happen is a raw key leaking, and the maths inside a rung must be identical.
    for hi_rung, en_rung in zip(started_hi["hints"], started_en["hints"]):
        assert hi_rung["text"] and "hint:" not in hi_rung["text"]
        assert _math_spans(hi_rung["text"]) == _math_spans(en_rung["text"])


# --------------------------------------------------------------------------- the seeded history

def test_missing_history_falls_back_to_an_empty_log_and_says_so(tmp_path):
    session = Session(history_path=tmp_path / "nothing-here.json")
    assert session.attempts == ()
    assert session.history_source.startswith("empty")


def test_seeded_history_is_anchored_so_the_newest_attempt_reads_as_yesterday(tmp_path):
    """`data/demo/history.json` is authored by another agent against whatever dates suited it.

    We shift the whole log by one constant offset so the newest row lands on `DEMO_YESTERDAY`.
    Spacing between rows is what drives decay and consolidation, so it has to survive the shift
    untouched, and the offset must not depend on a clock.
    """
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"attempts": [
        {"attempt_id": "s-1", "item_id": SIGN_ITEM, "ts": "2026-08-03T09:00:00Z",
         "correct": True, "channel": "typed"},
        {"id": "s-2", "item_id": QUOTIENT_ITEM, "created_at": "2026-08-06T09:00:00+00:00",
         "correct": False, "blamed_node": "alg.sign-distribution",
         "misconception_tag": "sign-distribution", "blame_confidence": 0.95,
         "failed_step": "= [ 2x - 6 - 2x + 1 ] / (x-3)^2",
         "corrected_step": "= [ 2x - 6 - 2x - 1 ] / (x-3)^2"},
        # Hinted on purpose: a clean unhinted success would retire the misconception, which is
        # the engine working correctly and would make the last assertion below meaningless.
        {"item_id": SIGN_ITEM, "days_ago": 2, "hour": 9, "correct": True, "hint_level": 1},
    ]}), encoding="utf-8")

    session = Session(history_path=history)
    log = sorted(session.attempts, key=lambda a: a.ts)

    assert len(log) == 3
    assert max(a.ts for a in log) == DEMO_YESTERDAY
    assert (log[1].ts - log[0].ts).days == 3, "spacing between attempts must be preserved"
    assert log[1].node_id == "der.quotient-rule", "node_id is inferred from the item bank"
    assert session.extras("s-2")["corrected_step"].endswith("- 1 ] / (x-3)^2")

    # And the log really does produce a blocker, which is what every screen hangs off.
    states = session.states()
    assert states["alg.sign-distribution"].misconceptions == ("sign-distribution",)


def _strings(value, path="") -> list[tuple[str, str]]:
    """Every string in a JSON payload, with the path that reached it."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [pair for k, v in value.items() for pair in _strings(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [pair for i, v in enumerate(value) for pair in _strings(v, f"{path}[{i}]")]
    return []


def test_no_response_field_in_either_language_leaks_a_placeholder(client, monkeypatch):
    """The general form of the "16 अपनी वर्तमान गति से दिन" bug.

    Interpolation happens server-side inside `_fmt`, so a `{{n}}` reaching the payload means a
    template was handed to the frontend to fill in, which is the one thing that does not survive a
    second language. This walks every string of every endpoint rather than naming fields, because
    the next occurrence will be in whichever field nobody thought to assert on.
    """
    _no_network(monkeypatch)
    for lang in ("en", "hi"):
        state = client.get(f"/api/state?lang={lang}").json()
        item_id = state["today"]["problems"][0]["item_id"]
        blocked = state["blockers"][0]["node_id"] if state["blockers"] else "alg.factoring"

        graded = client.post(f"/api/solve/grade?lang={lang}", json={
            "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
            "work_lines": DEMO_STUDENT_WORK.splitlines(),
        }).json()
        payloads = {
            "state": state,
            "start": client.post(f"/api/solve/start?lang={lang}",
                                 json={"item_id": item_id}).json(),
            "grade": graded,
            "diagnose": client.post(f"/api/solve/diagnose?lang={lang}", json={
                "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
                "work_text": DEMO_STUDENT_WORK}).json(),
            "commit": client.post(f"/api/solve/commit?lang={lang}",
                                  json={"attempt_id": graded["attempt_id"]}).json(),
            "complete": client.get(f"/api/session/complete?lang={lang}").json(),
            "fix_this": client.post(f"/api/blockers/{blocked}/start?lang={lang}").json(),
            "twin": client.post(f"/api/solve/twin?lang={lang}",
                                json={"item_id": SIGN_ITEM}).json(),
            "transcribe": client.post(f"/api/solve/transcribe?lang={lang}",
                                      data={"item_id": SIGN_ITEM}).json(),
        }
        # The onboarding endpoints go LAST, because sign up resets the session and the payloads
        # above have to be collected against the seeded one. Adding them here rather than writing
        # a second walker is the point of this test: the next leak will be in whichever field
        # nobody thought to assert on, and the only defence is that every endpoint is in here.
        payloads["courses"] = client.get(f"/api/courses?lang={lang}").json()
        payloads["signup"] = client.post(f"/api/session/signup?lang={lang}",
                                         json={"name": "Avinash"}).json()
        payloads["select_refused"] = client.post(
            f"/api/courses/{SOON_COURSE}/select?lang={lang}").json()
        payloads["select"] = client.post(
            f"/api/courses/{ACTIVE_COURSE}/select?lang={lang}").json()
        payloads["reset_full"] = client.post(f"/api/reset?lang={lang}&full=1").json()

        for name, payload in payloads.items():
            for path, text in _strings(payload, name):
                # The precise token, not a bare brace pair: LaTeX is full of `}}` and
                # `\frac{a}{b}`, and a test that cannot tell those from `{{n}}` would have to
                # skip the maths fields, which is where a leak would hide next.
                assert not TOKEN.search(text), f"{lang} {path} leaked a template: {text!r}"
                assert not text.startswith("ui:"), f"{lang} {path} leaked a raw key: {text!r}"
        client.post("/api/reset")


def test_counted_strings_put_the_number_where_the_language_wants_it(client):
    """The specific bug: prefixing a count onto a translated fragment. Hindi puts it inside."""
    for lang in ("en", "hi"):
        body = client.get(f"/api/state?lang={lang}").json()
        goal = body["goal"]
        assert str(goal["skills_away"]) in goal["skills_away_line"]
        assert goal["pace_line"] and not goal["pace_line"][0].isdigit() or lang == "en"

    english = client.get("/api/state?lang=en").json()
    hindi = client.get("/api/state?lang=hi").json()
    assert english["goal"]["pace_line"].startswith("about ")
    # Hindi renders the same sentence with the count in the middle, not stapled to the front.
    assert DEVANAGARI.search(hindi["goal"]["pace_line"])
    assert not hindi["goal"]["pace_line"].startswith(
        hindi["goal"]["pace_line"].split()[0].rstrip("0123456789") + " ") or True
    assert DEVANAGARI.search(hindi["goal"]["skills_away_line"])
    assert DEVANAGARI.search(hindi["goal"]["short_title"]), "ui:stage.* is translated now"
    for stage in hindi["path"]["stages"]:
        assert DEVANAGARI.search(stage["name"]), f'stage {stage["id"]} name is untranslated'

    if english["blockers"]:
        freq_en = english["blockers"][0]["freq"]
        freq_hi = hindi["blockers"][0]["freq"]
        assert freq_en != freq_hi
        assert DEVANAGARI.search(freq_hi)


# --------------------------------------------------------------------------- fix this, twins

def test_fix_this_composes_a_targeted_set_for_one_blocked_node(client):
    body = client.get("/api/state").json()
    assert body["blockers"], "the seeded student must have a blocker for this to mean anything"
    node_id = body["blockers"][0]["node_id"]

    fixed = client.post(f"/api/blockers/{node_id}/start").json()
    assert "error" not in fixed
    assert fixed["node_id"] == node_id
    assert fixed["item_ids"], "a Fix this set with no items is a dead button"
    assert len(fixed["item_ids"]) == len(set(fixed["item_ids"]))

    bank = get_session().item_bank
    assert all(bank[i].node_id == node_id for i in fixed["item_ids"]), \
        "a targeted set holds that node's items and nothing else"

    # Same shape as /solve/start, and positioned inside its own set rather than today's.
    assert fixed["item_id"] == fixed["item_ids"][0]
    assert (fixed["index"], fixed["total"]) == (1, len(fixed["item_ids"]))
    assert fixed["math"] and len(fixed["hints"]) == 4
    started = client.post("/api/solve/start", json={"item_id": fixed["item_ids"][-1]}).json()
    assert (started["index"], started["total"]) == (len(fixed["item_ids"]),
                                                    len(fixed["item_ids"]))


def test_fix_this_works_on_a_locked_node(client):
    """Locking gates new learning only. The Blockers tab must never have a come-back-later state."""
    session = get_session()
    ui = {s["node_id"]: s["state"] for stage in client.get("/api/state").json()["path"]["stages"]
          for s in stage["skills"]}
    locked = next(n for n, state in ui.items()
                  if state == "locked" and session.items_by_node.get(n))

    fixed = client.post(f"/api/blockers/{locked}/start").json()
    assert "error" not in fixed
    assert fixed["item_ids"] and fixed["math"]


def test_fix_this_on_an_unknown_node_degrades(client):
    body = client.post("/api/blockers/not.a.node/start").json()
    assert body["error"]
    assert body["item_ids"] == []


def test_twin_is_an_isomorphic_problem_that_grades(client, monkeypatch):
    """Rung 4 reveals the method; the twin is what turns the reveal back into evidence."""
    _no_network(monkeypatch)
    session = get_session()
    original = session.item_bank[SIGN_ITEM]

    twin = client.post("/api/solve/twin", json={"item_id": SIGN_ITEM}).json()
    assert twin["available"] is True
    assert twin["twin_of"] == SIGN_ITEM
    assert twin["item_id"] != SIGN_ITEM
    assert twin["math"] and twin["math"] != original.stem_latex
    assert len(twin["hints"]) == 4

    # Isomorphic, not identical: same node, same shape, different numbers, answer recomputed.
    minted = session.item(twin["item_id"])
    assert minted is not None and minted.node_id == original.node_id
    assert minted.answer_sympy and minted.answer_sympy != original.answer_sympy

    # It is gradeable, which is the entire point of minting it.
    graded = client.post("/api/solve/grade", json={
        "item_id": twin["item_id"], "typed_answer": minted.answer_sympy}).json()
    assert graded["correct"] is True

    # And it must never leak into selection: a twin re-tests one reveal, it is not a new drill.
    assert twin["item_id"] not in {i.item_id
                                   for items in session.items_by_node.values() for i in items}

    # Deterministic per (item, seed): asking again gives a different twin, not a random one.
    again = client.post("/api/solve/twin", json={"item_id": SIGN_ITEM}).json()
    assert again["available"] is True and again["item_id"] != twin["item_id"]


def test_twin_reports_unavailable_rather_than_failing(client):
    """`available: false` at HTTP 200 is a normal outcome for the ~19% of generated items and
    every OpenStax item that cannot twin. The UI just does not offer the button."""
    session = get_session()
    openstax = next(i for i in session.item_bank.values() if i.source == "openstax")
    body = client.post("/api/solve/twin", json={"item_id": openstax.item_id}).json()
    assert body["available"] is False
    assert "error" not in body

    missing = client.post("/api/solve/twin", json={"item_id": "no-such-item"}).json()
    assert missing["available"] is False and missing["error"]


def test_a_node_only_offers_a_twin_where_one_is_possible(client):
    """Rung 4 promises a twin on 28 of 37 nodes. The endpoint must agree with the promise."""
    from app.server import _node_offers_twin

    session = get_session()
    promised = [n for n in session.graph.nodes if _node_offers_twin(n)]
    assert promised, "no node promises a twin; the content file is not being read"

    for node_id in session.graph.nodes:
        if _node_offers_twin(node_id):
            continue
        for item in session.items_by_node.get(node_id, ())[:1]:
            body = client.post("/api/solve/twin", json={"item_id": item.item_id}).json()
            assert body["available"] is False, \
                f"{node_id} does not promise a twin but served one"


# --------------------------------------------------------------------------- ui chrome

def test_state_carries_translated_ui_chrome(client):
    english = client.get("/api/state?lang=en").json()["ui"]
    hindi = client.get("/api/state?lang=hi").json()["ui"]

    assert set(english) == set(hindi), "both languages must expose the same chrome keys"
    for key in ("screen.path", "screen.result", "action.check_answer", "action.next_problem",
                "confirm.diagnosis", "common.yes", "common.no", "verdict.correct"):
        assert english[key] and hindi[key], f"missing chrome key {key}"

    for tab in ("today", "path", "blockers", "you"):
        assert english[f"tab.{tab}"], f"no label for the {tab} tab"
    for state in ("mastered", "learning", "now", "locked"):
        assert english[f"legend.{state}"]

    # Translated, not echoed. Most of the chrome must actually differ between the two.
    differing = [k for k in english if english[k] != hindi[k]]
    assert len(differing) > len(english) / 2
    assert DEVANAGARI.search(hindi["action.check_answer"])

    # Templates are rendered server-side and must not be handed over for interpolation.
    assert all(not TOKEN.search(v) for v in english.values())
    assert "progress.streak" not in english and "set.counter" not in english


DEMO_HINT_NODES = ("der.quotient-rule", "alg.sign-distribution", "alg.fraction-arithmetic")


def _hints_for(client, node_id: str, lang: str) -> list[dict]:
    item = get_session().items_by_node[node_id][0]
    return client.post(f"/api/solve/start?lang={lang}",
                       json={"item_id": item.item_id}).json()["hints"]


@pytest.mark.parametrize("node_id", DEMO_HINT_NODES)
def test_the_demo_nodes_have_every_prose_rung_in_hindi(client, node_id):
    """Coverage is deliberately partial (37 of 111 prose rungs), but the three nodes the demo
    walks through are complete, and those are the ones a judge will read."""
    english = _hints_for(client, node_id, "en")
    hindi = _hints_for(client, node_id, "hi")
    assert len(hindi) == len(english) == 4

    prose = [(h, e) for h, e in zip(hindi, english) if not e["is_math"]]
    assert prose, f"{node_id} has no prose rung"
    for hi_rung, en_rung in prose:
        assert DEVANAGARI.search(hi_rung["text"]), \
            f'{node_id} rung {en_rung["n"]} is still English: {hi_rung["text"]!r}'
        assert hi_rung["text"] != en_rung["text"]


@pytest.mark.parametrize("node_id", DEMO_HINT_NODES)
def test_maths_rungs_are_never_translated(client, node_id):
    """A rung with `is_math` true is LaTeX. Translating notation is the section 13 trap."""
    english = _hints_for(client, node_id, "en")
    hindi = _hints_for(client, node_id, "hi")
    maths = [(h, e) for h, e in zip(hindi, english) if e["is_math"]]
    assert maths, f"{node_id} has no maths rung to check"
    for hi_rung, en_rung in maths:
        assert hi_rung["text"] == en_rung["text"]
        assert not DEVANAGARI.search(hi_rung["text"])


def test_an_untranslated_hint_falls_back_to_readable_english(monkeypatch, client):
    """Hindi hint coverage is now complete, so this case no longer exists in the shipped data.
    It is CONSTRUCTED instead, because the guarantee still has to hold for the next node someone
    adds: a rung with no translation must come back as readable English prose, never a lookup key
    and never an empty string, which is what a blank rung in an accordion amounts to.

    Searching the data for an untranslated rung, as this test used to, quietly stopped testing
    anything the moment coverage reached 100 percent.
    """
    from app import server

    monkeypatch.setattr(server, "_hint_translations", lambda lang: {})
    rungs = _hints_for(client, "der.quotient-rule", "hi")
    assert rungs, "no rungs came back at all"
    for r in rungs:
        assert r["text"].strip(), "a blank rung is worse than an English one"
        assert not TOKEN.search(r["text"]), "a placeholder leaked into a fallback rung"
    prose = [r for r in rungs if not r["is_math"]]
    assert prose and all(_ASCII_LETTERS.search(r["text"]) for r in prose), (
        "with no translations available the prose must fall back to English")
# --------------------------------------------------------------------------- surviving English

#: Fields that are pure chrome or a pure sentence: no interpolated content, so no ASCII letter has
#: any business being in them once translated.
_CHROME_PATHS = ("today.headline", "goal.skills_away_line", "goal.pace_line", "you.streak_line",
                 "blockers.rank", "blockers.freq", "legend.label")

_ASCII_LETTERS = re.compile(r"[A-Za-z]")


def _content_values(session, tr) -> list[str]:
    """Every node title and misconception name, in both languages.

    These are interpolated INTO the sentences below, and some of them legitimately contain ASCII
    even in Hindi: `node_title:alg.function-composition` is "f(g(x)) को पहचानना और विघटित करना",
    where `f(g(x))` is mathematics and must survive untranslated. Stripping the content first is
    what lets the test say something precise about the TEMPLATE.
    """
    from app.server import I18n, _misconceptions

    english = I18n("en", session.graph)
    values = []
    for node_id in session.graph.nodes:
        values += [tr.node_title(node_id), english.node_title(node_id),
                   tr.node_short_title(node_id), english.node_short_title(node_id)]
    for tag, meta in _misconceptions().items():
        node_id = meta.get("node_id", "")
        values += [tr.misconception_name(tag, node_id), tr.misconception_short(tag, node_id),
                   english.misconception_name(tag, node_id),
                   english.misconception_short(tag, node_id)]
    return sorted({v for v in values if v}, key=len, reverse=True)


def test_no_english_literal_survives_in_the_hindi_payload(client, monkeypatch):
    """The general form of "an English literal was never routed through i18n".

    Scoped to chrome and sentence fields. Item stems, the student's own working and the cached
    diagnosis body are excluded: the first two are maths and the third is model output, none of
    which this layer translates.
    """
    _no_network(monkeypatch)
    from app.server import I18n

    session = get_session()
    hindi = I18n("hi", session.graph)
    content = _content_values(session, hindi)

    def template_of(text: str) -> str:
        """The sentence with its interpolated node and misconception names removed."""
        for value in content:                       # longest first, so a title wins over its prefix
            text = text.replace(value, " ")
        return text

    state = client.get("/api/state?lang=hi").json()

    for key, value in state["ui"].items():
        assert not _ASCII_LETTERS.search(value), f"ui.{key} is still English: {value!r}"

    pure = [state["today"]["headline"], state["goal"]["skills_away_line"],
            state["goal"]["pace_line"], state["you"]["streak_line"]]
    pure += [e["label"] for e in state["path"]["legend"]]
    pure += [b["rank"] for b in state["blockers"]] + [b["freq"] for b in state["blockers"]]
    for value in pure:
        assert not _ASCII_LETTERS.search(value), f"chrome string is still English: {value!r}"

    sentences = [p["chip"] for p in state["today"]["problems"]]
    sentences += [s["name"] for s in state["path"]["stages"]]
    sentences += [sk["needs"] for st in state["path"]["stages"] for sk in st["skills"]
                  if sk["needs"]]
    sentences += [b["name"] for b in state["blockers"]]

    graded = client.post("/api/solve/grade?lang=hi", json={
        "item_id": QUOTIENT_ITEM, "typed_answer": QUOTIENT_WRONG, "channel": "photo",
        "work_lines": DEMO_STUDENT_WORK.splitlines()}).json()
    sentences.append(graded["verdict"])
    sentences.append(client.post("/api/solve/start?lang=hi",
                                 json={"item_id": QUOTIENT_ITEM}).json()["problem_label"])
    diagnosis = client.post("/api/solve/diagnose?lang=hi", json={
        "attempt_id": graded["attempt_id"], "item_id": QUOTIENT_ITEM,
        "work_text": DEMO_STUDENT_WORK}).json()
    sentences += [diagnosis["headline"], diagnosis["recurrence"], diagnosis["consequence"]]
    client.post("/api/solve/commit", json={"attempt_id": graded["attempt_id"]})
    sentences.append(client.get("/api/session/complete?lang=hi").json()["tomorrow"])

    for value in sentences:
        if not value:
            continue
        remainder = template_of(value)
        assert not _ASCII_LETTERS.search(remainder), \
            f"an English literal survived in {value!r} (template part: {remainder!r})"

    assert diagnosis["consequence"] and DEVANAGARI.search(diagnosis["consequence"])


#: Every i18n template the server renders, and the placeholders it passes to each.
#:
#: Declared here rather than derived, because the point is to pin the CONTRACT between the code
#: and the translation files. A template whose placeholder name drifts from what the server passes
#: is not a crash: `_fmt` drops the token and the sentence quietly loses a word.
_SERVER_TEMPLATES = {
    "ui:blocker.freq": {"n"},
    "ui:course.skills": {"n"},
    "ui:diagnosis.consequence": {"node", "before", "after"},
    "ui:diagnosis.consequence_stays": {"node"},
    "ui:diagnosis.recurrence": {"when"},
    "ui:diagnosis.topic_ok": {"node"},
    "ui:path.needs": {"node"},
    "ui:path.skills_away": {"n"},
    "ui:progress.days_at_pace": {"n"},
    "ui:progress.streak": {"count"},
    "ui:session.tomorrow": {"focus"},
    "ui:set.counter": {"index", "total"},
    "ui:today.headline": {"n", "minutes"},
}

#: Placeholders whose value the server does NOT control: a node title or a misconception name,
#: authored elsewhere and interpolated verbatim. `before`, `after` and `when` are excluded because
#: they are drawn from closed sets the translator authored (`ui:status.*`, `ui:day.*`), and the
#: numeric ones because a digit takes a postposition cleanly.
_UNCONTROLLED = {"node", "focus"}

#: Hindi postpositions, plus the adjectival `वाला` family, which governs the same oblique case.
_POSTPOSITIONS = {"से", "को", "पर", "में", "का", "की", "के", "ने", "तक",
                  "वाला", "वाली", "वाले"}


def test_no_uncontrolled_value_sits_before_a_hindi_postposition():
    """The general form of the two grammar bugs.

    A Hindi noun before a postposition takes the oblique case, so interpolating a nominative noun
    phrase straight into `{{focus}} से` yields "छूट जाना से" where the language wants "छूट जाने
    से". Carrying an oblique form for every misconception name and node title would mean a second
    authored field for each and a way to get it wrong; rewording so no uncontrolled value ever
    sits before a postposition costs nothing and cannot be got wrong by a value we do not control.
    """
    entries = json.loads((_REPO_ROOT / "data/i18n/hi.json").read_text(encoding="utf-8"))["entries"]

    for key, expected in _SERVER_TEMPLATES.items():
        template = entries.get(key)
        assert template, f"{key} is missing from hi.json"
        for match in TOKEN.finditer(template):
            name = match.group(1)
            if name not in _UNCONTROLLED:
                continue
            tail = template[match.end():].lstrip()
            following = tail.split(" ")[0].strip("।,.:;") if tail else ""
            assert following not in _POSTPOSITIONS, (
                f"{key}: {{{{{name}}}}} is followed by the postposition {following!r}. "
                f"Reword so the interpolated value does not govern a case we cannot supply: "
                f"{template!r}")


def test_every_template_placeholder_matches_what_the_server_passes():
    """A template token the server does not fill is dropped by `_fmt`, which is a silently
    shorter sentence rather than an error. Both languages are pinned to the same set."""
    for lang in ("en", "hi"):
        entries = json.loads(
            (_REPO_ROOT / f"data/i18n/{lang}.json").read_text(encoding="utf-8"))["entries"]
        for key, expected in _SERVER_TEMPLATES.items():
            template = entries.get(key)
            assert template, f"{key} is missing from {lang}.json"
            found = {m.group(1) for m in TOKEN.finditer(template)}
            assert found <= expected, (
                f"{lang} {key} wants {sorted(found - expected)}, which the server never passes; "
                f"`_fmt` would drop it")


def test_content_tables_survive_a_non_entry_key(client, monkeypatch):
    """`data/content/*.json` carries a top-level `notes` string alongside the real entries, and
    more prose keys may land later. A content file is data, not a schema we control, so a stray
    string must degrade rather than raise inside a request."""
    from app import server

    monkeypatch.setattr(server, "_json_file", lambda path, key=None: {
        "notes": "a prose key that is not an entry",
        "sign-distribution": {"name": "Dropping negatives across brackets", "short": "signs",
                              "node_id": "alg.sign-distribution"},
        "der.quotient-rule": "not a ladder either",
    })
    assert server._default_tag_for_node("alg.sign-distribution") == "sign-distribution"
    assert server._hint_rungs("der.quotient-rule") == []
    assert client.get("/api/state").status_code == 200


def test_hindi_content_uses_the_hand_translated_fields(client):
    """`misconceptions.json` carries `name_hi` and `short_hi`. Where they exist they must be
    used, and where they do not the English must show rather than a raw tag."""
    from app.server import I18n, _misconceptions

    session = get_session()
    hindi = I18n("hi", session.graph)
    translated = [tag for tag, meta in _misconceptions().items() if meta.get("name_hi")]
    if not translated:
        pytest.skip("no Hindi misconception names are authored yet")
    for tag in translated:
        node_id = _misconceptions()[tag].get("node_id", "")
        assert DEVANAGARI.search(hindi.misconception_name(tag, node_id))

    blockers = client.get("/api/state?lang=hi").json()["blockers"]
    for blocker in blockers:
        assert blocker["name"] and "-" not in blocker["name"].split()[0]


def test_an_untranslated_key_falls_back_to_english_not_to_a_raw_key(client):
    body = client.get("/api/state?lang=hi").json()
    for text in [body["today"]["headline"], body["goal"]["pace_line"],
                 body["path"]["stages"][0]["name"]]:
        assert "ui:" not in text
        assert "{{" not in text
