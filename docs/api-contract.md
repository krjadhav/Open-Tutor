# Open Tutor API contract

The single source of truth between `app/server.py` and `app/static/`. Both are built against this
document; neither may invent a field.

Design rule: **the server returns UI-ready values.** The frontend renders, it does not compute.
Reason chips, stage grouping, "N skills away", dot states and human-readable misconception names are
all decided server-side, where the graph and the engine live. A frontend that recomputes any of it
will drift from the engine the first time a constant changes.

Base URL `/api`. All responses JSON. All requests JSON except the photo upload.
Every endpoint accepts `?lang=en|hi`; strings come back already translated from
`data/i18n/<lang>.json`, so the frontend never holds a translation table.

---

## GET /api/state

Everything the four tabs need, in one call. Called on load and after every `commit`.

```jsonc
{
  "student_id": "demo",
  "lang": "en",
  "goal": { "node_id": "ai.gradient-descent-step", "title": "Gradient descent",
            "skills_away": 7,              // unmastered ancestors of the target
            "pace_line": "about 18 days at your pace" },

  "today": {
    "headline": "6 problems · 15 min",
    "problems": [
      { "item_id": "gen-alg.sign-distribution-1",
        "math": "Expand and simplify $5x - (3x - 2)$",   // stem_latex, already localised
        "chip": "Blocker · signs",                        // SetEntry.reason
        "slot": "blocker",                                // blocker|review|new|goal_link
        "is_blocker": true,                               // drives the accent dot
        "done": false }
    ]
  },

  "path": {
    "stages": [                                  // ordered TOP first (target stage first)
      { "id": "ai", "name": "Gradient descent", "count": "0/1",
        "state": "locked",                       // mastered|learning|now|locked
        "skills": [
          { "node_id": "ai.gradient-descent-step",
            "name": "The gradient descent update step",
            "state": "locked",
            "needs": "needs: The gradient vector" }   // "" when nothing is unmet
        ] }
    ],
    "legend": [ { "state": "mastered", "label": "mastered" } ]
  },

  "blockers": [
    { "node_id": "alg.sign-distribution",
      "rank": "Blocker 1",
      "name": "Dropping negatives across brackets",     // human readable, never the raw tag
      "freq": "4 times this week",
      "wrong": "= [2x - 6 - 2x + 1] / (x-3)^2",         // the student's own failed_step
      "right": "= [2x - 6 - 2x - 1] / (x-3)^2",         // corrected_step
      "item_count": 7 }
  ],

  "you": { "streak_line": "12 day streak", "mastered": 9, "accuracy": "78%" }
}
```

`skills_away` counts ancestors of `goal.node_id` whose status is not `mastered`.
Stage `state` is the least advanced state among its skills. Stage `count` is `mastered/total`
**within the goal slice**. A node outside the slice appears only if it currently holds a blocker.

---

## POST /api/solve/start
`{ "item_id": "..." }` ->
```jsonc
{ "item_id": "...", "index": 5, "total": 6,
  "problem_label": "Problem 5 of 6",
  "math": "Differentiate $f(x) = (2x + 1)/(x - 3)$",
  "hints": [ { "n": 1, "text": "A fraction of two functions. Which rule is that?", "is_math": false } ]
}
```
All four hints are returned at once; the frontend reveals them one at a time. `is_math` true means
render in the serif maths face.

## POST /api/solve/transcribe
Multipart, field `image`, plus `item_id`. Photo path only.
```jsonc
{ "lines": ["f'(x) = [(2)(x-3) - (2x+1)(1)] / (x-3)^2", "= [2x - 6 - 2x + 1] / (x-3)^2"],
  "ocr_seconds": 6.8, "source": "vision" }        // source: vision|cache
```
On Vision failure return HTTP 200 with `"lines": []` and `"error"`, so the UI can fall back to the
typed path rather than dead-ending.

## POST /api/solve/grade
`{ "item_id", "typed_answer", "hint_level", "channel": "typed"|"photo", "work_lines": [] }`
```jsonc
{ "attempt_id": "a-7", "correct": false,
  "verdict": "Incorrect",                  // localised
  "expected_latex": "-7/(x-3)^{2}",
  "unsimplified": false,                   // correct, but not in simplest form
  "needs_diagnosis": true }                // false whenever correct; the model is never asked
```
**The CAS decides correctness. `needs_diagnosis` is false for every correct answer**, which is what
removes the false-positive class measured in learning-design section 14.

## POST /api/solve/diagnose
`{ "attempt_id", "item_id", "work_text" }` -> only call when `needs_diagnosis` is true.
```jsonc
{ "blamed_node": "alg.sign-distribution",
  "headline": "Your quotient rule is correct.",
  "body": "You dropped the negative when distributing across −(2x+1), so −2x−1 became −2x+1.",
  "recurrence": "Same mistake as Tuesday.",       // "" when first occurrence
  "failed_line_index": 1,                          // index into work_lines, -1 if unknown
  "consequence": "Distributing a negative drops from mastered to needs-work.",
  "from_cache": true, "latency_s": 0.0 }
```
Served from `data/demo/diagnosis_cache.json` when the work matches, live otherwise. `seed` does not
pin model output (section 16), so anything shown on stage must be cached.

## POST /api/solve/commit
`{ "attempt_id", "blame_confirmed": true|null }` -> applies the attempt to the graph.
```jsonc
{ "xp": 40,
  "node_changes": [ { "node_id": "alg.sign-distribution", "from": "mastered", "to": "learning" } ],
  "unlocked": ["der.chain-rule"],
  "session_done": false }
```
Appends to the attempt log and recomputes derived state by replay. Nothing is mutated in place.

## GET /api/session/complete
```jsonc
{ "xp_total": 120, "unlocked_count": 3,
  "nodes": [ { "node_id": "...", "name": "Quotient rule", "state": "mastered", "just_lit": true } ],
  "tomorrow": "Tomorrow starts with sign distribution." }
```

## POST /api/reset
Restores the seeded 5-day history. Returns `GET /api/state`.

---

## Errors
Any handled failure returns HTTP 200 with an `error` string alongside a usable payload. The demo
must degrade, never blank. Unhandled exceptions return 500 with `{"error": "..."}` and the frontend
shows the last good state.

---

# Addendum: onboarding, sign up, course selection

Added 2026-08-08. Revised the same day: the Welcome screen is gone, so the flow before the tabs is
two screens, not three.

```
Sign up ──→ Course selection ──→ [Today]
(placeholder,  (one playable,
 no real auth)  the rest disabled)
```

**Why the goal question went.** It asked "What do you want to understand?" and then the very next
screen asked the student to choose a course, which is the same decision twice. Worse, the goal they
typed was never sent anywhere: no endpoint accepted it, and `goal` on `GET /api/state` is derived
from `graph.target_node`, which the selected course determines. A field that implies it shapes the
experience while something else actually determines it is worse than one screen fewer, so the
screen and its four i18n strings (`ui:welcome.subtitle`, `ui:goal.prompt`, `ui:goal.example`,
`ui:action.get_started`) were removed together rather than left as translated copy nothing renders.

Sign up is deliberately a placeholder: we are not building auth for a hackathon, and pretending to
would be worse than admitting it.

## GET /api/courses

```jsonc
{ "courses": [
    { "id": "calculus-for-ai", "title": "Calculus for AI",
      "subtitle": "From algebra to gradient descent",
      "ends_at": "The gradient descent update step",
      "state": "active",                    // active | coming_soon
      "skills": 37,
      "detail": "about 18 days at 15 min a day",   // active courses only
      "selectable": true },
    { "id": "jee-calculus", "title": "JEE Mains Calculus", "subtitle": "The full calculus syllabus",
      "ends_at": "Definite integrals and areas", "state": "coming_soon",
      "skills": 63, "detail": "Coming soon", "selectable": false }
  ],
  "selected": "calculus-for-ai" | null }
```

Strings are localised like everything else. `selectable` is computed server-side, not inferred by
the frontend from `state`, so the rule for what is playable lives in one place.

## POST /api/session/signup

`{ "name": "Avinash" }`, name optional. Placeholder: it creates or resets the demo session and
records a display name. **No password, no email verification, no persistence beyond the process.**
Returns `{ "student_id": "demo", "name": "Avinash", "next": "courses" }`.

Do not add a password field, even a fake one. A form that looks like it takes a credential and
does not is worse than an honest placeholder.

## POST /api/courses/{course_id}/select

Selects a playable course and installs its seeded history. Returns the full `GET /api/state` body,
so the frontend can go straight to Today with one call.

A `coming_soon` id returns HTTP 200 with `{ "error": "not available yet", "selected": <unchanged> }`.
The card should already be non-interactive; the endpoint refusing is the backstop, not the gate.

## Flow rules

`GET /api/state` carries `flow`, one of exactly three values. They are **facts about the student,
not screen names**, so renaming a screen is not a change to this contract:

| `flow` | means |
|---|---|
| `signup` | the student has not signed up |
| `courses` | the student has signed up but has chosen no course |
| `ready` | the student has both, so there is a course to work in |

There is no `welcome` value and no back-compatible alias for one. One name, one meaning.

- The frontend asks the server how far the student has got; it does not track this itself, because
  the answer changes under it on calls it did not make.
- An unknown or missing `flow` must land the frontend on **Sign up**, never on nothing: a value it
  cannot read is a student it knows nothing about, and a blank frame behind a lifted veil is the
  one outcome the degrade-never-blank rule forbids.
- `POST /api/session/signout` clears the session and returns
  `{ "ok": true, "next": "signup", "flow": "signup" }`.
- `POST /api/reset` returns to the seeded demo student WITH its course already selected, so demo
  rehearsals do not walk the onboarding every time. Add `?full=1` to clear the session entirely, so
  `flow` comes back `signup`, which is what you want when showing the flow itself.
