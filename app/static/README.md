# Open Tutor frontend

Plain HTML, CSS and vanilla JS. No build step, no framework, no bundler. The only external request
is the Google Fonts link the design mock already uses (Schibsted Grotesk, STIX Two Text, Noto Sans
Devanagari).

Three files do everything:

| file | holds |
|---|---|
| `index.html` | the markup for all nine screens, one `<section class="screen">` each |
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
Onboarding
   |
   +--> [ Today ] <-> [ Path ] <-> [ Blockers ] <-> [ You ]      tab bar visible
           |               |
           |               +-- "Fix this" opens that blocker's problem
           |
           +--> Solve --+-- typed ------------------> Result --> next problem, or Complete
                        |                               ^
                        +-- photo --> Check ------------+
```

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

Mathematics is rendered by a small LaTeX-to-HTML pass in `app.js` (`mathBody`). It is language
independent by construction, so maths is byte-identical in English and Hindi and only prose changes.
Fields named `math` are treated as all maths unless they contain `$...$`; prose fields such as the
diagnosis body are treated as prose and only render `$...$` spans as maths.

## Faked, stubbed or deviating

Stated plainly, since none of this is in the contract:

- **Chrome strings are in the frontend.** `CHROME` in `app.js` holds the fixed furniture only: tab
  labels, button labels, "Expected", "Was this your mistake?" and so on. The contract exposes no
  endpoint for UI chrome, and `data/i18n/en.json` is not reachable from any documented route. All
  *content* still comes from the server. If `GET /api/state` ever returns a `ui` object of
  `{key: string}`, `applyServerUI` copies it over the local table and the server wins with no
  frontend change needed.
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
- **The status bar** ("9:41", "OPEN TUTOR") is decoration from the mock, not live.

## Degradation

The demo must never blank. A loading veil covers the first paint; every API call that fails falls
back to mock data and raises a small banner reading "Server not responding. Showing demo data.",
which clears on the next successful call. If the photo endpoint returns no lines, the UI stays on
Solve and shows a plain message pointing at the typed path rather than dead-ending on Check.

## Accessibility and theming

The four node states are distinguishable by shape as well as colour: filled gold disc (mastered),
half-filled disc (learning), pulsing accent ring (now), dashed grey ring (locked). Dark theme is
driven by `data-theme` on `<body>` and is remembered in `localStorage`, defaulting to the system
preference. `prefers-reduced-motion` disables the animations.

Phone first at 390x844. On desktop the same phone frame is centred. Below 460px wide the frame goes
full bleed. There is no dashboard variant.
