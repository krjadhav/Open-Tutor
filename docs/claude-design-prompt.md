# Claude design brief (v3)

Paste everything between the rules into Claude to iterate on the UI.

**What changed since v2.** v2 designed nine screens and they were built. Three things then happened:
the flow grew a real front door (welcome, sign up, course selection), the data turned out to
disagree with several strings v2 invented, and the whole thing now runs on a live engine. This
version describes what actually exists so the next pass improves it rather than re-inventing it.

v2 is in git history (commit `babbcc7`) if you want to diff.

**The next goal is to make it feel more like a game.** That is not in this brief. Get the flow and
the content right first; the styling pass comes after.

---

## THE BRIEF

I have a working adaptive maths tutor with twelve screens, built and running against a real engine.
I want you to improve it. Produce a single self-contained HTML artifact (inline CSS/JS, no external
requests). Phone first: design inside a 390x844 frame. On desktop, centre the same phone frame, do
not make a dashboard variant.

### What the product does, in one sentence

**Open Tutor finds the one thing actually blocking you and fixes it.**

A student picks a course. The app gives them six problems a day, and when one is wrong it reads
their handwritten working and names *which specific skill* failed. Usually that is not the topic of
the problem: most calculus errors are algebra, sign or trig errors in a calculus costume.

> "Your quotient rule is correct. You dropped the negative when distributing across -(2x+1),
> so -2x-1 became -2x+1."

That sentence is the product. Everything else is scaffolding around it.

Audience: motivated, impatient adults, usually learning ML, who work problems on paper and
photograph them. Not children.

### The flow, as it exists now

```
Welcome ──→ Sign up ──→ Course selection ──→ [Today] [Path] [Blockers] [You]
                                                 │
                                                 └─→ Solve ─┬─ typed ─────────→ Result ─→ next
                                                            └─ photo → Check ─→ Result
                                                                                    │
                                                                              Complete
```

Twelve screens. The first three are the front door and have no tab bar. The four tabs are the app.
Solve, Check, Result and Complete are a full-screen flow with one exit.

### Reference points

- Navigation and calm: **wondering.app**. Bite-size, big type, generous whitespace, large rounded
  cards, one bold accent, plain-English microcopy.
- Feeling: **training with a good coach**, not a game show. Precise and warm. Learning should feel
  good because progress is *visible*, not because something cheered.
- Forbidden: mascots, gems, hearts, lives, confetti on trivial actions, "Great job!!",
  purple-to-blue gradients, glassmorphism.

### The design system already in place, keep it

```
--page #E8E5DE   --bg #F5F3EE   --card #FFFFFF  --ink #15130F   --muted #6B6459
--line #E3DED4   --accent #0E8C8C  --gold #B5820E  --sunk #EDEAE3
dark: --page #08090A  --bg #101112  --card #191B1C  --ink #F2F0EB
      --muted #98948B  --line #292C2D  --accent #3FD8C8  --gold #E0AE3C  --sunk #151718
```

Schibsted Grotesk for UI, **STIX Two Text for every piece of mathematics**, Noto Sans Devanagari for
Hindi. One accent colour, used only for "where you are now". **Gold means mastered and nothing
else.** Four node states distinguishable by shape as well as colour: filled disc, half disc, pulsing
ring, dashed ring. Keyframes: `pulse`, `arrive`, `goldin`, `riseline`, `blink`.

### Hard text budget

- One headline of eight words or fewer per screen, at most one supporting sentence.
- No paragraph longer than two lines at 390px.
- At most three kinds of information visible per screen.
- Prefer a label or a number with a unit over a sentence, **except the diagnosis**, which is the
  emotional payload and is allowed to breathe.
- If a screen needs explaining, redesign the screen.

---

## The screens

Real strings and real numbers throughout. These come from the live app; do not invent replacements.

**1. Welcome.** One question, "What do you want to understand?", prefilled `How neural networks
learn`, one button. Nothing else. This is the strongest thing the product says.

**2. Sign up.** Deliberately a placeholder: a name field, a continue button, and one short line
saying no account is created. **Never add a password or email field, not even a disabled or
decorative one.** A form that looks like it takes a credential and does not is worse than an honest
placeholder.

**3. Course selection.** Five cards, one playable and four not.

| course | ends at | state |
|---|---|---|
| **Calculus for AI** · From algebra to gradient descent | The gradient descent update step | active, 37 skills, about 18 days at 15 min a day |
| Linear Algebra for AI · Vectors to eigenvalues | Principal component analysis | Coming soon |
| JEE Mains Calculus · The full calculus syllabus | Definite integrals and areas | Coming soon |
| Class 12 Calculus · CBSE board, in your language | Applications of integrals | Coming soon |
| Probability for ML · Counting to Bayes | Maximum likelihood estimation | Coming soon |

The disabled four must read as a **roadmap, not as broken buttons**: reduced emphasis, not
interactive, not keyboard focusable. This screen is where the platform story lands, so give the
locked cards enough dignity to be believable and enough restraint that nobody taps them twice.

**4. Today.** Header `6 problems · 15 min`. Six cards, each one line of maths plus one short reason
chip. The real chips, in order: `Blocker · fractions`, `Blocker · signs`, `On your path`, `Review`,
`New · Vector notation`, `Review`. An accent dot marks blocker rows only. Showing *why* each problem
is there is unusual and worth making a feature of.

**5. Path.** Target card: **Gradient descent**, `18 skills away`, `about 16 days at your pace`.

Eight stages, collapsed to a vertical spine, target at the top, one expanded at a time. **Do not
draw the DAG.** Stages top to bottom: `Gradient descent`, `Optimisation`, `Multivariable`,
`Derivative rules`, `Limits`, `Trig values`, `Algebra moves`, `Exponentials and logs`.

Each stage shows a state and a count like `3/7`. An expanded stage lists its skills with their real
names and a `needs: <skill>` chip where a prerequisite is unmet. **Every one of those strings is
generated from the graph.** v2 hand-wrote "Chain rule needs: Quotient rule" and it was factually
wrong. Treat all Path content as server data.

**6. Blockers.** Two open misconceptions, ranked. Each card shows the name, the frequency, and
**the student's own wrong line struck through above the corrected one**:

```
Dropping negatives across brackets            4 times this week
   = [2x - 6 - 2x + 1] / (x-3)²      (struck through)
   = [2x - 6 - 2x - 1] / (x-3)²
```

Second blocker: `Combining fractions without a common denominator`, twice.

This is the most valuable screen in the app. A blocker should feel like a named opponent you can
beat, not a to-do item. One tap starts a targeted set of three drills on that skill alone.

**7. You.** Streak, skills mastered, accuracy this week, language toggle (English / हिंदी),
appearance toggle. Small, honest numbers. No badges. Current real values: 5 day streak, 10 mastered,
78% accuracy.

**8. Solve.** One problem: `Differentiate f(x) = (2x + 1)/(x - 3)`. A typed maths answer box, a
camera button, and a four-rung hint ladder. Hints must never read as a penalty. Rung 2 is the actual
rule in mathematics; rung 4 reveals the method and offers a twin problem with new numbers.

**Step indicator: 2 dots on the typed path, 3 on the photo path.** Not a fixed 3.

**9. Check** (photo path only). `Here's what we read.` The transcription, with any line tappable to
correct. Confirm. This screen exists because OCR is imperfect and honesty beats magic; it must take
one second and not feel like a checkpoint.

**10. Result.** The most important screen, and the one place text may breathe. **Three states, not
one:**

- **Correct.** Verdict only. No diagnosis is requested at all.
- **Correct but unsimplified.** Treated as correct, with a light note that it can be simplified.
  Never marked wrong.
- **Incorrect.** Two stages. The verdict lands instantly. About three seconds later the diagnosis
  arrives with the `arrive` animation: headline `Your quotient rule is correct.`, then the body,
  then `Same mistake as yesterday.` The failing line highlights in the working. Then a consequence
  line, and a quiet `Was this your mistake?` with yes and no.

**11. Complete.** Spine rises, newly mastered nodes turn gold, `Tomorrow starts with: dropping
negatives across brackets`, `+120 XP · 3 unlocked`.

### Constraints

- Mathematics must render legibly with plain HTML and CSS. No external maths libraries.
- Language toggle switches prose only; **mathematics is byte-identical in both languages**. Show at
  least one screen in Hindi. Real Hindi from the app:
  `आपका भागफल नियम सही है।` and `कल की शुरुआत: कोष्ठक पर ऋण चिह्न छूट जाना`
- Every colour that carries meaning must also be distinguishable without colour.
- Dark theme must genuinely work, driven by `data-theme` on body.
- Handle loading and error states. It must degrade, never blank.

### What I want back

1. All twelve screens, navigable, tab bar on the four tabs only.
2. A real point of view, and tell me why. Especially on the three-screen front door, which is new
   and currently the weakest part, and on the Path spine at eight stages.
3. Not a generic dashboard. This should look like someone with taste made it.

---

## ITERATION PROMPTS

One change per round.

- "The three-screen front door is three taps before anything happens. Show me two alternatives: one
  that merges welcome and sign up, and one that puts course selection first."
- "Course selection is where the platform story lands. Redesign just that screen so the four locked
  courses read as a roadmap I want rather than four things I cannot have."
- "The Path spine now has eight stages and that is a lot on a phone. Show me three ways to handle
  it: strict one-open accordion, a two-tier grouping, and a scrollable spine with a sticky target."
- "The Result diagnosis should feel like a person noticed something. Right now it reads like form
  validation. Rework the typography and the pacing of the reveal."
- "Give the Blockers card the weight it deserves. It currently looks like a to-do item."
- "Show me the empty state: day one, nothing known, everything locked. It must feel like the start
  of something, not an error."
- "Make the whole thing 20% more austere and confident. Less UI, more content."

## WHAT NOT TO ACCEPT

- Purple-to-blue gradients, glassmorphism, generic SaaS dashboard layouts.
- A Result screen where the diagnosis is a small grey box under a big red X.
- A progress bar as the primary progress metaphor. The graph is the progress metaphor.
- Any screen whose most prominent element is a number rather than a sentence.
- Invented skill names, counts or prerequisites. Every one of those is real data and wrong values
  will not match the running app.
