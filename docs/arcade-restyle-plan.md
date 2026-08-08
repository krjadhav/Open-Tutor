# Arcade restyle plan

Applying the Brawl Stars style mock (`BrawlStars_Katex_UI/Open Tutor Arcade.dc.html`) to the running
app. This is a **styling pass over a working product**, not a rebuild: the twelve screens, the API
contract and the engine behind them do not change.

---

## 1. What the mock actually gives us

**Five screens of twelve**: welcome, courses, today, path, solve.

**Not in the mock, so they have to be extrapolated**: sign up, check, result, complete, blockers,
you, and the correct and unsimplified result states. That is seven screens, and it includes
**Result**, which is the most important screen in the product.

**No Hindi anywhere in the mock.** Not one Devanagari character.

### The design vocabulary, extracted

| | |
|---|---|
| type | **Lilita One**, one weight, everything |
| maths | **KaTeX**, white on dark |
| page | `#15171f` behind a `#272C3D` device, radial gradient `#3B4364 -> #1D2130` |
| card | `#353C52`, inset `0 -7px 0 #272C3D` for the bevel |
| primary | `#EFC609` gold, inset `0 4px 0 #FBEF44` top light, `0 -7px 0 #A6552D` bottom shadow |
| accents | `#68C1E0` cyan, `#9747FF` purple, `#FDE45E` yellow, `#8EE0F6` pale cyan |
| chips | blocker `#8538CE`, goal `#3C8FAE`, review `#4A5270`, new `#CE4F0A` |
| depth | `drop-shadow(0 4px 0 #000) drop-shadow(0 8px 0 rgba(0,0,0,.5))` |
| shape | `clip-path` skew, 2px black inner border, `border:3px solid #000` on the device |
| text | `-webkit-text-stroke: 3.5px to 6px #000` with `paint-order: stroke fill` |
| motion | `arrive`, `pulse`, `goldin`, `riseline`, `bob` |

---

## 2. The four problems to solve before any pixels move

**KaTeX and the font are loaded from a CDN.** Our app is served from `app/static` and must run on
venue wifi. Both get vendored locally: `katex.min.css`, `katex.min.js` and the KaTeX font files,
plus Lilita One as woff2. No external request at runtime, which is also what the design brief has
required from the start.

**Lilita One has no Devanagari glyphs.** Every Hindi string would silently fall back to a system
font and sit next to arcade type looking broken. Hindi is a first-class path here, not a toggle, so
it needs a display-weight Devanagari face chosen deliberately. This is the single biggest risk in
the restyle and the mock gives no guidance because it contains no Hindi.

**Heavy strokes destroy prose readability.** A 6px black stroke on a 38px headline reads as a game
title. The same treatment on the Result diagnosis, which is two lines of ordinary sentence at 20px
and is the emotional payload of the whole product, would be unreadable. Rule for this pass:
**strokes on headlines, numbers, chips and buttons; plain white on dark for any sentence a student
has to actually read.**

**The style is dark only.** There is no light variant in the mock, and a light Brawl Stars is not a
thing. The app currently ships a working light theme and a toggle.

---

## 3. Phases

**Phase 1, foundations.** Vendor KaTeX and the fonts. Replace the hand-rolled LaTeX renderer in
`app.js` with KaTeX, keeping a plain-text fallback if it fails to load. This alone fixes the
renderer limitation that has already caused two bugs (`\mathbf` and `\begin{matrix}` printing as
words), and it means the item bank no longer has to avoid commands.

**Phase 2, design system.** Replace the token block in `styles.css` with the arcade palette, and add
the reusable pieces: bevelled card, bevelled button, chip, stroked heading, the five keyframes. Done
as classes, not per-element inline styles, because twelve screens share them.

**Phase 3, the five mocked screens.** Courses, Today, Path, Solve, plus the welcome layout applied
to Sign up. Highest fidelity to the mock here since we have it.

**Phase 4, the seven extrapolated screens.** Sign up, Check, Result and its three states, Complete,
Blockers, You. Built from the Phase 2 vocabulary. Every invented decision gets recorded, because
these have not been through design review.

**Phase 5, verification.** Headless browser over the full path in both languages, zero page errors,
no horizontal overflow at 390px, KaTeX rendering every stem in the bank, and Devanagari legible
beside the arcade type.

---

## 4. What does not change

The API contract, the twelve-screen flow, the engine, the item bank, the seeded student, and the
rule that the server owns all copy and flow state. If a screen needs different data to look right,
that is a contract change and gets raised, not worked around in the frontend.
