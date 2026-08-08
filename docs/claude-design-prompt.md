# Claude design brief (v2)

Paste everything between the rules into Claude to generate the UI. v1 produced something dense,
text-heavy, and with an unreadable knowledge graph. v2 fixes that by imposing a tab structure, a
hard text budget, and a specific way to render the graph. Iteration prompts are at the bottom.

v1 is in git history (commit `babbcc7`) if you want to diff the two.

---

## THE BRIEF

I'm building **Open Tutor**, an adaptive maths tutor. Design the interface as a single
self-contained HTML artifact (inline CSS/JS, no external requests). Phone-first: design it inside a
390x844 frame. On desktop, show the same phone layout centred, not a dashboard variant.

### What the product does, in one sentence

**Open Tutor finds the one thing actually blocking you and fixes it.**

A student says what they want to understand ("how neural networks learn"). The app maps their
knowledge onto a prerequisite graph, gives them six problems a day, and when they get one wrong it
reads their handwritten working and names *which specific skill* failed. Usually that is not the
topic of the problem. Most calculus errors are algebra, sign or trig errors in a calculus costume.

> "Your chain rule is correct. You dropped the negative when you distributed across -(3x-2).
> Same mistake as Tuesday."

That sentence is the product. Everything else is scaffolding around it.

### What was wrong with the last attempt

Be aware of these, they are the brief:

1. **Too much text.** Screens read like documentation. Explanation everywhere, content nowhere.
2. **The knowledge graph was unreadable.** Fifteen nodes with crossing edges is a hairball on a
   phone. I could not tell where I was or what was next.
3. **No navigation model.** Seven sibling screens with no spine. I never knew where I was.

### Reference points

- Navigation and calm: **wondering.app**. Bite-size, big type, generous whitespace, large rounded
  cards, one bold accent colour, plain-English microcopy, a light streak layer that never nags.
- Feeling: **training with a good coach**, not a game show. Precise and warm. Learning should feel
  fun because it is *clear* and because progress is visible, not because something cheered.
- Still forbidden: mascots, gems, hearts, lives, confetti on trivial actions, "Great job!!",
  purple-to-blue gradients, glassmorphism.

Who it's for: motivated, impatient adults (usually learning ML) who work problems on paper and
photograph them. Not children.

### Navigation: four bottom tabs, thumb-reachable

Fixed bottom tab bar, always visible except inside the solve flow.

1. **Today** (default) - the day's six problems
2. **Path** - the map
3. **Blockers** - what keeps tripping you up
4. **You** - streak, stats, language, settings

**Solve -> Check -> Result** is a full-screen flow pushed from Today. It hides the tab bar, shows a
step indicator, and has one exit. Onboarding (the goal question) happens before the tabs exist.

Make the tab bar actually work in the artifact, and make every screen reachable.

### Hard text budget (this is a constraint, not a suggestion)

- Each screen has **one headline of eight words or fewer** and at most **one supporting sentence**.
- No paragraph longer than two lines on a 390px-wide phone.
- Max **three kinds of information** visible per screen. If there is a fourth, it goes behind a tap.
- Prefer a label or a number with a unit over a sentence, everywhere **except the diagnosis**, which
  is allowed to be prose because it is the emotional payload.
- No tooltips-as-paragraphs, no onboarding coach marks, no "here's how this works" copy. If a
  screen needs explaining, redesign the screen.

If you hit the budget and something still feels unexplained, delete it instead of shrinking the
type.

### How to render the graph so it is actually clear

This is the hardest part and the main thing I want you to solve. Rules:

- **Do not draw the DAG.** Collapse it to a **single vertical spine of 6 stages**, bottom to top,
  target pinned at the top. One column, no crossing lines, ever.
- Stages, bottom to top:
  `Algebra moves` · `Trig values` · `Limits` · `Derivative rules` · `Multivariable` ·
  **`Gradient descent`** (target)
- **Only the current stage is expanded.** Everything else is a single collapsed row: stage name, a
  small state indicator, and a count like `3/4`. Tapping expands one and collapses the rest.
- An expanded stage shows its 3 to 4 skills as nodes with their real names:
  - Algebra moves: `Distributing a negative across a sum or difference`, `Laws of exponents`,
    `Adding and simplifying rational expressions`
  - Trig values: `Exact values of sine and cosine at standard angles`
  - Limits: `Informal idea of a limit`, `Resolving 0/0 limits by factoring`,
    `The derivative as a limit of the difference quotient`
  - Derivative rules: `Power rule`, `Product rule`, `Quotient rule`, `Chain rule`
  - Multivariable: `Partial derivatives`, `The gradient vector`,
    `Directional derivatives and steepest descent`
  - Gradient descent: `The gradient descent update step`
- **Four states, each distinguishable by shape as well as colour** (filled dot = mastered, half dot
  = learning, pulsing ring = the edge where learning happens now, hollow grey = locked).
- **One accent colour** in the whole app, reserved for "here is where you are now". Gold only for
  mastered. Nothing else is coloured.
- Prerequisites are shown as **a chip on the node** ("needs: Chain rule"), never as a drawn edge.
- Pin the target at the top with distance in skills, not percent: **"4 skills away"**.
- Secondary, small, one line: `about 18 days at your pace`.

Show me the collapsed default and one expanded stage.

### The screens

Keep copy at or under the counts given. These are the real strings, use them.

**Onboarding - Goal.** One input, "What do you want to understand?", pre-filled with
`How neural networks learn`. One button. Nothing else on the screen.

**Tab 1 - Today.** Six problem cards, roughly 15 minutes. Each card is **one line of maths plus one
short reason chip**, nothing more. Reasons in order: `Blocker · signs`, `Blocker · fractions`,
`Review`, `Review`, `New · Chain rule`, `On your path`. Header: one line, e.g. `6 problems · 15 min`.
Primary button is thumb-reachable.

**Tab 2 - Path.** As specified above.

**Tab 3 - Blockers.** Two open misconceptions, each stated in plain language with a frequency and a
"fix this" action:
- `Dropping negatives across brackets` · 4 times this week
- `Subtracting fractions by subtracting denominators` · twice

This tab is the most valuable thing in the app. Make each blocker feel like a named opponent you
can beat, not a to-do item. One tap starts a targeted set.

**Tab 4 - You.** Streak, skills mastered, accuracy, language toggle (English / हिंदी). Small,
honest numbers. No badges.

**Flow: Solve.** One problem: `Differentiate f(x) = (2x + 1) / (x - 3)`. A typed maths answer box, a
camera button for a photo of paper working, and a **Hint** control with four escalating levels.
Hints must never read as a penalty.

**Flow: Check.** After the photo: `Here's what we read.` showing the transcription:

```
f'(x) = [(2)(x-3) - (2x+1)(1)] / (x-3)²
      = [2x - 6 - 2x + 1] / (x-3)²
      = -5 / (x-3)²
```

Confirm, or tap any line to correct it. This screen exists because OCR is imperfect and honesty
beats magic. It must take one second, not feel like a checkpoint.

**Flow: Result.** The most important screen, and the one place text is allowed to breathe.
Two-stage:

- **Instantly:** the verdict. Incorrect. Expected `-7/(x-3)²`.
- **About 3 seconds later:** the diagnosis arrives. Show the arrival. Do not pop it in.

The diagnosis, verbatim:

> **Your quotient rule is correct.** You dropped the negative when distributing across `-(2x+1)`,
> so `-2x-1` became `-2x+1`. Same mistake as Tuesday.

Highlight the failing line in their own work: `= [2x - 6 - 2x + 1] / (x-3)²`. Then one consequence
line: `Distributing a negative` drops from mastered to needs-work. Then a small, non-defensive
`Was this your mistake?` with yes/no.

The emotional beat is **being seen**. Most space, most craft, least chrome.

**Flow: Session complete.** Not a stats dump. The reward is **a node turning gold and the frontier
visibly expanding on the Path**, animated, then one line: `Tomorrow starts with sign distribution.`
Numbers (+120 XP, 3 unlocked) are secondary and small.

### Constraints

- Maths must render legibly with plain HTML/CSS (superscripts, fractions, `f'(x)`). No external
  maths libraries, the artifact is self-contained.
- Language toggle in **You**, English / हिंदी. Devanagari for prose, maths notation unchanged. Show
  at least one screen in Hindi so I can check it holds up.
- Dark and light, both real.
- Every colour that carries meaning must also be readable without colour.
- Motion is the fun: node lighting, frontier expanding, the diagnosis arriving. Everything else
  stays still.

### What I want back

1. All tabs and the full solve flow, navigable, in one artifact.
2. A real point of view, stated in two or three sentences: how you made the graph legible, and how
   the Result reveal is paced.
3. Not a generic dashboard. It should look like someone with taste made it.

---

## ITERATION PROMPTS

One change per round. That keeps the good parts.

- "The Path still reads as a diagram. Show me three versions of the collapsed spine: a route card
  stack, a metro line with stops, and a stacked progress ladder."
- "Cut 30% of the words from every screen without removing any function."
- "The Result diagnosis reads like a validation error. Make it feel like a person noticed
  something. Rework the typography and the pacing of the reveal."
- "Animate a mastered node dropping back to needs-work. Consequential, not punishing."
- "Design the four hint levels. Level 4 reveals the full solution and immediately serves a fresh
  twin problem. That has to feel deliberate, not like a penalty."
- "Give Blockers the weight it deserves. Right now it is a to-do list."
- "Show day one: nothing known, everything locked. It must feel like a start, not an error."
- "Show the Today tab with everything done."

## WHAT NOT TO ACCEPT

- Any screen where I have to read a paragraph to know what to do.
- A graph with crossing edges, or more than one expanded stage at a time.
- More than one accent colour, or colour used decoratively.
- Progress bars as the main progress metaphor. The spine is the metaphor.
- A screen whose largest element is a number rather than a sentence or the maths.
- Duolingo tells: mascots, gems, hearts, lives, streak nagging.
- Purple-to-blue gradients, glassmorphism, generic SaaS dashboard layouts.
