# Open Tutor frontend

Plain HTML, CSS and vanilla JS. No build step, no framework, no bundler. **No external request at
all**: KaTeX and both display faces are vendored under `vendor/`, because the demo runs on venue
wifi and a CDN is a single point of failure.

Three files do everything:

| file | holds |
|---|---|
| `index.html` | the markup for all ten screens, one `<section class="screen">` each |
| `styles.css` | the design system: tokens, the arcade components, the six keyframes |
| `app.js` | routing, the API layer, the KaTeX wrapper and `MOCK_STATE` |

| vendored | why |
|---|---|
| `vendor/katex/` | `katex.min.js`, `katex.min.css` and its woff2 fonts |
| `vendor/display-fonts.css` | **Lilita One** for Latin, **Baloo 2** for Devanagari, sliced by unicode-range |

One font stack, `'Lilita One', 'Baloo 2', system-ui, sans-serif`, and the browser picks per glyph.
Lilita One has no Devanagari at all, so every Hindi string would otherwise fall back to a system
face and sit next to arcade type looking broken. Weight stays 400 everywhere: Lilita One ships one
weight, and Baloo 2 resolves 400 to its own 700 without a synthetic bold.

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
Sign up  -->  Course selection            tab bar hidden throughout
(a name,      (one playable card,
 no auth)      the rest disabled)
                     |
   +-----------------+
   |
   +--> [ Today ] <-> [ Path ] <-> [ Blockers ] <-> [ You ]      tab bar visible
           |               |
           |               +-- "Fix this" opens that blocker's problem
           |
           +--> Solve --+-- typed ------------------> Result --> next problem, or Complete
                        |                               ^
                        +-- photo --> Check ------------+
```

### One layout rule: a scroll area ends above its footer

Every screen with a primary action at the bottom is a flex column of **head, scroll, anchored
footer**, and nothing is ever floated over a list. There are two footer classes and they differ
only in padding: `.flowfoot` closes a full-screen solve flow (Solve, Check, Result, Complete),
`.tabfoot` closes a screen under the tab bar (Today).

The tab bar is a *sibling* of the screens pinned to the bottom of the frame, so it covers the last
`--tabbar-h` of whatever is beneath it. `.screen--tabbed` reserves exactly that as padding and lays
itself out in what is left, which is what lets Today anchor its own footer above the bar.
**`--tabbar-h` is a fixed 80px and `.tabbar` is given that height**, because Baloo 2's line box is
taller than Lilita One's: left to size itself the bar grew 6px in Hindi and swallowed the bottom of
the footer Today had anchored above it.

This replaced a `position: absolute` gold button on Today that sat over the problem list and cut the
fifth card in half. Path, Blockers and You had the same reservation expressed as `padding-bottom`
on the scroller and now share the screen-level one; Result's "Next problem" used to scroll away with
the content and is now anchored too, hidden entirely (footer and all) while the diagnosis is being
read, because the yes/no under it is the only action at that moment.

**Sign up is the entry point.** A Welcome screen used to stand in front of it asking "What do
you want to understand?" and it is gone. It duplicated the course choice on the very next
screen, and what the student typed into it was never sent anywhere: no endpoint accepted it and
the goal on Path came back from `GET /api/state` already chosen. A field that implies it shapes
the experience while the course selection actually determines it is worse than one screen fewer.

**The server decides which of the first two screens you land on.** `GET /api/state` returns
`flow`: `signup`, `courses` or `ready`. Those are facts about the student, not screen names
("has not signed up", "signed up but no course", "ready"), and `SCREEN_FOR_FLOW` in `app.js` is
the one place each becomes a screen. Nothing about the flow is computed in the frontend and
nothing about it is kept in `localStorage`; the language is the only thing remembered locally.

`GET /api/state` is always the first call, before Sign up is painted, because these two screens
have no chrome source of their own: `GET /api/courses` carries no `ui` block, and the translated
copy for both arrives in `state.ui`, which is populated even when `flow` is `signup`. The loading
veil covers that first call as it always has.

Sign up `POST`s `/api/session/signup` and follows the `next` field in the reply. Selecting a
course `POST`s `/api/courses/{id}/select`, which returns the full `GET /api/state` body, so Today
is rendered straight from that payload with no second call. Whether a course can be selected is
read from **`selectable` only**, never from `state`.

The solve flow is full screen, hides the tab bar and has one exit (the ✕, which returns to Today).
The step indicator is **2 bars on the typed path and 3 on the photo path**, per `docs/ui-spec.md`.

Result has three states, driven by `/api/solve/grade`:

1. `correct` and not `unsimplified`: the verdict alone. No diagnosis call is made at all.
2. `correct` and `unsimplified`: the verdict plus one light note and the simplest form.
3. `correct: false`: expected answer, the work block, the blinking "Reading your working" indicator,
   then the diagnosis arriving with the `arrive` animation and the line at `failed_line_index`
   highlighted.

**Colour carries the Result state, and none of it is red.** Gold cap and a star for correct, amber
cap and the *same* star for correct-but-unsimplified, purple cap for incorrect. Purple is the
blocker colour everywhere else in the app, which is exactly what an incorrect answer has just found;
red would be the only alarm colour in a palette that has none. Drawing the star on both correct
states is deliberate: a glanced-at unsimplified screen must never read as a failure.

A correct answer puts nothing under the verdict card, so `renderVerdict` adds `is-lean` to the
screen and the card centres itself and sets larger, rather than sitting alone at the top of an empty
frame looking unfinished.

## Where the data comes from

Everything on Path, Today, Blockers and You is read from `GET /api/state`. No skill name, count,
`needs:` chip or stage is hardcoded. The language toggle re-requests every endpoint with `?lang=`
and re-renders; the server returns already translated strings.

That now includes the chrome. `state.ui` supplies the tab labels, the eyebrows, "Start today's set",
"Take a photo", "Check answer", the step labels, "Was this actually your mistake?", Yes, No and the
rest, so **`data/i18n/hi.json` is the one place a Hindi string is fixed**. See the chrome bullet
under "Faked, stubbed or deviating" for the 29 strings the server does not own yet.

Mathematics is rendered by **KaTeX**, vendored locally, with `throwOnError: false` so a stem it
cannot parse is drawn in its error colour instead of taking the screen down. It is language
independent by construction, so maths is byte-identical in English and Hindi and only prose changes.
Fields named `math` are treated as all maths unless they contain `$...$`; prose fields such as the
diagnosis body are treated as prose and only render `$...$` spans as maths.

This replaced a hand-rolled LaTeX-to-HTML pass that knew about twenty commands and printed the rest
as literal words: `\mathbf{u}` drew "mathbfu" and a sympy matrix drew "beginmatrix ... endmatrix",
and both of those shipped as visible bugs. `RENDERABLE_COMMANDS` in `pipeline/drill_tasks.py` was a
model of that renderer and is now only a conservative constraint on generated content. If KaTeX
itself fails to load, `app.js` degrades to readable plain text **and says so with `console.error`**,
because a silent fallback is how a broken build reaches a demo.

## Faked, stubbed or deviating

Stated plainly, since none of this is in the contract:

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
  local word is kept as the fallback. On the course card it becomes the corner badge, split at the
  first run of digits so the number sets large over its word. The characters are unchanged, and a
  string that does not start with a number is printed whole.
- **An unknown or missing `flow` lands on Sign up.** `FALLBACK_SCREEN` in `app.js`, and it is a
  fallback rather than a computation: a `flow` the frontend cannot read is a student we know
  nothing about, and the thing to do with a student we know nothing about is ask who they are.
  It is also the cheapest screen to be wrong about, because the student types a name and the
  server answers again. Falling through to no screen at all would leave the frame blank behind a
  lifted veil, which is the one outcome the degrade-never-blank rule forbids.
- **"Replay onboarding" now calls `POST /api/reset?full=1`**, which the addendum defines as
  clearing the session entirely, then routes on the `flow` that comes back. Plain `POST /api/reset`
  keeps the seeded course and is what a demo rehearsal wants; the button's label promises the
  longer walk, so it asks for the longer walk.
- **The server owns the chrome. `CHROME` in `app.js` is the fallback for when it cannot be
  reached.** `GET /api/state` returns a `ui` object keyed by the server's own `<group>.<name>` i18n
  keys, `CHROME` is flat camelCase, and `SERVER_UI_ALIASES` bridges the two. **33 of the 56 local
  keys resolve to a server string; the other 23 are listed in `LOCAL_ONLY_CHROME` with a reason
  each.** (It was 59 and 26: `appearance`, `light` and `dark` went with the theme toggle.) A key naming nothing local is ignored rather than added, so the table cannot fill up with
  strings nothing renders, and only an existing *string* can be replaced, which is what keeps
  `blockersHead` (a function of the blocker count) from being clobbered.
  On every load `applyServerUI` logs two lines: how many of the server's `ui` keys render as chrome
  and which do not, and the local-only list with its reasons. The first list is expected to be
  non-empty, because the server keeps chrome and content in one i18n file and `stage.*`, `slot.*`,
  `node.*`, `verdict.*`, `day.*`, `hint.level_*` and friends arrive already baked into the payload.
  What it catches is the other kind: a string translated on the server, expected on screen, landing
  nowhere. If you fix a Hindi string on the server and see no change, that log names the reason.

  There is no longer any server key the frontend deliberately overrides. The three that used to be
  on that list, `goal.prompt`, `goal.example` and `action.get_started`, were the welcome question,
  its prefill and its button; they left `data/i18n/*.json` with the screen. `offline` is local by
  necessity: it is the message shown when the server is gone, so it cannot come from the server.
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
  object moves `signup -> courses -> ready` as you walk the screens, so the whole flow is
  developable with the API down and `?mock=1` walks it from the start. The mock also refuses a
  non-selectable id, the same way the endpoint is specified to.
- **The status bar** ("9:41", "OPEN TUTOR") is decoration from the mock, not live. The wordmark
  hides itself on Sign up, which carries the full gold lockup.
- **Deliberately not copied from the arcade mock.** The mock is five screens of a twelve screen
  product and it has no Hindi, no prose and no real data, so some of it does not survive contact:
  - *The SVG icon kit* (`kit/star-fill.svg`, `kit/back-arrow.svg`, `kit/status-1.svg`). Those files
    are not in this repo. The star, the padlock, the camera and the close cross are CSS shapes.
  - *The "THE ROADMAP" caption* on the divider between the playable course and the rest. The server
    sends no such string and inventing an untranslated one for a rule line is not worth it, so the
    divider is a plain rule.
  - *The dimmed Blockers and You tabs.* They are dimmed in the mock because it does not draw those
    screens. Ours work, so they stay live.
  - *A back arrow on Course selection.* The mock goes back to a screen we deleted. Sign up is
    forward only and there is no endpoint to unselect a course.
  - *A PLAY button inside the course card.* The whole card is already the button; a button inside a
    button is neither valid nor clickable, so the gold plate is a plate.
  - *The heavy stroke on everything.* See the readability rule above.
  - *The mock's own copy* ("PICK YOUR COURSE", "18 DAYS", "1/2"). Every string on screen is still the
    server's, in the student's language.
- **Today's header shows the streak line** from `you.streak_line`, which is the same field the You
  tab renders. It is where the mock puts it, and it is data already in the payload, so no contract
  change was needed.

### Invented for the seven screens the mock never drew

Check, Result and its three states, Complete, Blockers and You are extrapolated from the phase 2
vocabulary. None of them has been through design review, so every decision is written down here.

- **The strike-through on a Blockers card is drawn, not declared.** `text-decoration: line-through`
  does not cross an atomic inline box, and KaTeX renders one, so on a rendered expression the line
  silently disappears: the "wrong" line was only ever a grey line. `.blockercard-strike` is an
  inline-block sized to the mathematics carrying the rule as a `::after`.
- **A blocker's rank is a purple plate and its "Fix this" is a gold button.** Gold is otherwise
  reserved for the one primary action per screen; on Blockers, going and beating the misconception
  *is* the primary action, and there are only ever two cards.
- **The wrong/right pair carries a marker as well as the strike**, grey square and gold square, so
  the pair is distinguishable by shape and not by the strike alone. Same rule as the four node
  states.
- **Complete splits `+N XP · M unlocked` into two plates.** The words are `progress.xp_earned` and
  `progress.unlocked`, both the server's; only the layout is new. The numbers are the reward, so
  they are set as numbers.
- **Complete draws full node shapes, not dots.** The `.stage-node` star, half-disc, ring and padlock
  from Path, with `--inline` so they sit in the flow. A node earned *today* is held grey while the
  spine rises past it and then takes the gold with `goldin`; a node that was already mastered is
  simply gold and does not animate. A gold node that was gold on arrival is nothing to watch.
- **The spine fill is measured in pixels against the rail**, not as a percentage of the panel, so it
  cannot run past the last node.
- **Check is deliberately the plainest screen in the app**: no cap strip, no heavy shadow, no colour
  until a line is touched, and then only that line lights. It is a one-second confirmation, not a
  checkpoint, and dressing it up would make it feel like one.
- **You gets no third number and no badges.** The mastered count takes the yellow because gold means
  mastery everywhere else in this app; accuracy stays white because it is not a mastery claim.
- **The diagnosis reveal is still `CONFIG.DIAGNOSIS_MIN_WAIT_MS = 700`.** `docs/demo-runbook.md`
  describes the diagnosis arriving "about three seconds" after the verdict, which is what a *live*
  call costs (the cache records latencies of 3.07s to 10.66s). Served from cache it lands in 700ms,
  which is enough to see the indicator but is not the three-beat pause the screen was designed
  around. Left alone on purpose: it is a behaviour constant, not a style, and this pass changed no
  behaviour. It is a one-line change if the pause is wanted on stage.

## Degradation

The demo must never blank. A loading veil covers the first paint; every API call that fails falls
back to mock data and raises a small banner reading "Server not responding. Showing demo data.",
which clears on the next successful call. If the photo endpoint returns no lines, the UI stays on
Solve and shows a plain message pointing at the typed path rather than dead-ending on Check.

## Accessibility and appearance

A course that cannot be selected is a `<div>`, not a `<button>`: there is nothing in the tab order
to reach, nothing to press, and no `tabindex` juggling to get wrong. It carries `aria-disabled` and
a visible "Coming soon" pill placed directly after the title, so a screenreader hears the title and
then hears that it is not available. Visually it drops to a dimmer face, muted type and a flatter
shadow, and the playable card is the only one that takes a press state, so the group reads as a
roadmap rather than as five broken buttons.

**Readability rule, written into `styles.css` as well:** the black text-stroke goes on headlines,
numbers, chips and buttons only. Any sentence a student actually has to read is plain white or
`#C4CBE2` on dark with no stroke. The mock strokes everything because it contains no prose; a 6px
stroke on the Result diagnosis, which is two lines of ordinary sentence and the emotional payload of
the whole product, would be unreadable. Devanagari also drops the wide arcade tracking, under
`html[lang="hi"]`, because spacing out a connected script pulls matras away from their letters.

The whole flow is operable from the keyboard: on Sign up, Enter in the field advances, and on
Course selection the single playable card is the only tab stop on the screen. A refusal from
`POST /api/courses/{id}/select` stays put and writes to the console: its `error` is a machine string
and is not localised, so it is never put on screen, and it cannot be reached anyway because the
disabled card is not clickable.

The four node states are distinguishable by shape as well as colour: gold star (mastered),
half-filled disc (learning), pulsing white dot (now), padlock (locked). The node icons are CSS
shapes, because the mock's SVG icon kit is not part of this repo.

**Dark only, and there is no appearance toggle.** The arcade style has no light variant in the mock
and a light Brawl Stars is not a thing, so the light palette, the `data-theme` attribute and the
Light/Dark segmented control on You were all removed together rather than left as a second palette
nobody designed. The language toggle stays. `prefers-reduced-motion` disables the animations.

Phone first at 390x844. On desktop the same phone frame is centred. Below 460px wide the frame goes
full bleed. There is no dashboard variant.
