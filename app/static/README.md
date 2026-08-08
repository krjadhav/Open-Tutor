# Open Tutor frontend

Plain HTML, CSS and vanilla JS. No build step, no framework, no bundler. The only external request
is the Google Fonts link the design mock already uses (Schibsted Grotesk, STIX Two Text, Noto Sans
Devanagari).

Three files do everything:

| file | holds |
|---|---|
| `index.html` | the markup for all eleven screens, one `<section class="screen">` each |
| `styles.css` | the design system: tokens, both palettes, the five keyframes, every component |
| `app.js` | routing, the API layer, the maths renderer and `MOCK_STATE` |

## Running it

FastAPI serves this directory as static files, so the normal way is to start the server and open
its root:

```
uvicorn app.server:app --reload      # then http://127.0.0.1:8000/
```

Any static server works if you only want to look at the UI, because the frontend falls back to mock
data when the API is unreachable:

```
python3 -m http.server 8080 --directory app/static
```

### URL switches

| query | effect |
|---|---|
| *(none)* | LIVE. Calls the API, falls back to the mock per call if one fails |
| `?mock=1` | Never calls the API. Renders entirely from `MOCK_STATE` |
| `?fast=1` | Marks the first four problems done, so Complete is two taps away in a demo |

The single switch in code is `CONFIG.DATA_SOURCE` at the top of `app.js`. It ships as `LIVE`.

## Screen map

```
Welcome  -->  Sign up  -->  Course selection            tab bar hidden throughout
(the goal     (a name,      (one playable card,
 question)     no auth)      the rest disabled)
                                    |
   +--------------------------------+
   |
   +--> [ Today ] <-> [ Path ] <-> [ Blockers ] <-> [ You ]      tab bar visible
           |               |
           |               +-- "Fix this" opens that blocker's problem
           |
           +--> Solve --+-- typed ------------------> Result --> next problem, or Complete
                        |                               ^
                        +-- photo --> Check ------------+
```

**The server decides which of the first three screens you land on.** `GET /api/state`
returns `flow`: `welcome`, `courses` or `ready`, and `routeFromFlow` maps those to the
onboarding, courses and Today screens. Nothing about the flow is computed in the frontend
and nothing about it is kept in `localStorage`; only the theme and the language are
remembered locally, as before.

`GET /api/state` is always the first call, before Welcome is painted, because these three
screens have no chrome source of their own: `GET /api/courses` carries no `ui` block, and the
translated copy for all three arrives in `state.ui`, which is populated even when `flow` is
`welcome`. The loading veil covers that first call as it always has.

Welcome goes to Sign up. Sign up `POST`s `/api/session/signup` and follows the `next` field
in the reply. Selecting a course `POST`s `/api/courses/{id}/select`, which returns the full
`GET /api/state` body, so Today is rendered straight from that payload with no second call.
Whether a course can be selected is read from **`selectable` only**, never from `state`.

The solve flow is full screen, hides the tab bar and has one exit (the ✕, which returns to Today).
The step indicator is **2 bars on the typed path and 3 on the photo path**, per `docs/ui-spec.md`.

Result has three states, driven by `/api/solve/grade`:

1. `correct` and not `unsimplified`: the verdict alone. No diagnosis call is made at all.
2. `correct` and `unsimplified`: the verdict plus one light note and the simplest form.
3. `correct: false`: expected answer, the work block, the blinking "Reading your working" indicator,
   then the diagnosis arriving with the `arrive` animation and the line at `failed_line_index`
   highlighted.

## Where the data comes from

Everything on Path, Today, Blockers and You is read from `GET /api/state`. No skill name, count,
`needs:` chip or stage is hardcoded. The language toggle re-requests every endpoint with `?lang=`
and re-renders; the server returns already translated strings.

That now includes the chrome. `state.ui` supplies the tab labels, the eyebrows, "Start today's set",
"Take a photo", "Check answer", the step labels, "Was this actually your mistake?", Yes, No and the
rest, so **`data/i18n/hi.json` is the one place a Hindi string is fixed**. See the chrome bullet
under "Faked, stubbed or deviating" for the 29 strings the server does not own yet.

Mathematics is rendered by a small LaTeX-to-HTML pass in `app.js` (`mathBody`). It is language
independent by construction, so maths is byte-identical in English and Hindi and only prose changes.
Fields named `math` are treated as all maths unless they contain `$...$`; prose fields such as the
diagnosis body are treated as prose and only render `$...$` spans as maths.

## Faked, stubbed or deviating

Stated plainly, since none of this is in the contract:

- **The goal typed on Welcome is not sent anywhere.** The contract has no endpoint that accepts
  it, and the goal on Path comes back from `GET /api/state` already chosen. The field is the
  question the product asks, not an input the engine reads. It has always been this way; it is
  written down here now that a second screen follows it.
- **Sign up is a placeholder and says so on the screen.** One name field, one button, and one line
  admitting it: the server's `signup.subtitle` ("No password and no email. This is a demo and we
  are not pretending otherwise."), or "No account is created; this is a placeholder." if the server
  sends nothing. There is no password field and no email field, not even a disabled or decorative
  one, because a form that looks like it takes a credential and does not is worse than an honest
  placeholder. The name is optional, so an empty field is allowed through; the reply's `name` is
  not displayed anywhere yet. The placeholder in the field is `signup.name_label` ("Your name") and
  not `signup.name_placeholder` ("Optional"), because with no visible label the placeholder is the
  only thing naming the field, and it is also set as the field's `aria-label`.
- **The "Coming soon" marker prefers the server's own words.** A non-selectable course renders its
  `detail` string as the marker, falling back to the local `comingSoon` chrome only if `detail` is
  missing. That keeps the marker localised and stops the same words appearing twice on one card.
  Non-selectable cards drop the detail line at the foot of the card for the same reason.
- **`skills_line` is rendered, not `skills`.** The server sends a finished localised string
  ("37 skills", "37 कौशल"); the addendum only promises the bare integer, so composing that with a
  local word is kept as the fallback. The string is rendered verbatim with one typographic
  exception: a leading run of digits is set in the maths face, which is how every other number in
  the app is set. The characters are unchanged.
- **A `GET /api/state` with no `flow` field lands on Welcome.** That is the fallback, not a
  computation: it is what an older server without the addendum implies, and it is the screen a
  student with no session should see anyway.
- **"Replay onboarding" now calls `POST /api/reset?full=1`**, which the addendum defines as
  clearing the session entirely, then routes on the `flow` that comes back. Plain `POST /api/reset`
  keeps the seeded course and is what a demo rehearsal wants; the button's label promises the
  longer walk, so it asks for the longer walk.
- **The server owns the chrome. `CHROME` in `app.js` is the fallback for when it cannot be
  reached.** `GET /api/state` returns a `ui` object keyed by the server's own `<group>.<name>` i18n
  keys, `CHROME` is flat camelCase, and `SERVER_UI_ALIASES` bridges the two. **32 of the 61 local
  keys resolve to a server string; the other 29 are listed in `LOCAL_ONLY_CHROME` with a reason
  each.** A key naming nothing local is ignored rather than added, so the table cannot fill up with
  strings nothing renders, and only an existing *string* can be replaced, which is what keeps
  `blockersHead` (a function of the blocker count) from being clobbered.
  On every load `applyServerUI` logs two lines: how many of the server's `ui` keys render as chrome
  and which do not, and the local-only list with its reasons. The first list is expected to be
  non-empty, because the server keeps chrome and content in one i18n file and `stage.*`, `slot.*`,
  `node.*`, `verdict.*`, `day.*`, `hint.level_*` and friends arrive already baked into the payload.
  What it catches is the other kind: a string translated on the server, expected on screen, landing
  nowhere. If you fix a Hindi string on the server and see no change, that log names the reason.

  Three server keys are **deliberately not** taken, and they are the only ones where the server has
  a key and the frontend still wins: `goal.prompt`, `goal.example` and `action.get_started`. Those
  are the welcome question, its prefill and its button, and the server's wording for all three
  ("What do you want to be able to do?", "I want to understand how neural networks learn", "Get
  started") is different copy rather than a translation of the copy that was signed off with the
  design. Point them at the server the moment the two agree on the words. `offline` is also local
  by necessity: it is the message shown when the server is gone, so it cannot come from the server.
- **The Blockers headline** ("Two things keep tripping you up") is generated from the number of
  blockers, because the contract returns no headline for that tab.
- **"N skills away"** renders `goal.skills_away` plus the words "skills away". The number is the
  server's; the two words are chrome.
- **"Fix this"** opens the matching blocker problem already in today's set. The contract has no
  endpoint that composes a fresh set from a `node_id`.
- **The twin problem** offered by hint rung 4 is not wired. There is no endpoint for it, so the hint
  ladder simply ends after the last rung rather than offering something that cannot be served.
- **`blame_confirmed`** is sent as `true` for Yes and `false` for No. The contract writes the type as
  `true|null`; `false` is the honest encoding of a rejected blame and needs the server to accept it.
- **`MOCK_STATE` is illustrative, not authoritative.** It is shaped to the contract exactly and its
  node ids, titles and prerequisites are taken from `data/graph/nodes.json`, but the mastery states,
  counts and the stage list are invented for the demo. The server is the source of truth. The mock
  grades by `item_id` rather than by running a CAS, chosen so all three Result states can be seen
  with the backend down.
- **The mock covers the three new endpoints too.** `MOCK_COURSES` carries the five courses from
  `data/courses.json` with their real ids, titles and skill counts; the Hindi strings and the
  `detail` lines are written here rather than translated by the server. A small `mockSession`
  object moves `welcome -> courses -> ready` as you walk the screens, so the whole flow is
  developable with the API down and `?mock=1` walks it from the start. The mock also refuses a
  non-selectable id, the same way the endpoint is specified to.
- **The status bar** ("9:41", "OPEN TUTOR") is decoration from the mock, not live.

## Degradation

The demo must never blank. A loading veil covers the first paint; every API call that fails falls
back to mock data and raises a small banner reading "Server not responding. Showing demo data.",
which clears on the next successful call. If the photo endpoint returns no lines, the UI stays on
Solve and shows a plain message pointing at the typed path rather than dead-ending on Check.

## Accessibility and theming

A course that cannot be selected is a `<div>`, not a `<button>`: there is nothing in the tab order
to reach, nothing to press, and no `tabindex` juggling to get wrong. It carries `aria-disabled` and
a visible "Coming soon" pill placed directly after the title, so a screenreader hears the title and
then hears that it is not available. Visually it drops to a dashed border and muted type, which is
the same vocabulary the locked node dot already uses, so the group reads as a roadmap rather than
as five broken buttons. Only the playable card takes a hover state.

The whole flow is operable from the keyboard: on Welcome and Sign up, Enter in the field advances,
and on Course selection the single playable card is the only tab stop on the screen. A refusal from
`POST /api/courses/{id}/select` stays put and writes to the console: its `error` is a machine string
and is not localised, so it is never put on screen, and it cannot be reached anyway because the
disabled card is not clickable.

The four node states are distinguishable by shape as well as colour: filled gold disc (mastered),
half-filled disc (learning), pulsing accent ring (now), dashed grey ring (locked). Dark theme is
driven by `data-theme` on `<body>` and is remembered in `localStorage`, defaulting to the system
preference. `prefers-reduced-motion` disables the animations.

Phone first at 390x844. On desktop the same phone frame is centred. Below 460px wide the frame goes
full bleed. There is no dashboard variant.
