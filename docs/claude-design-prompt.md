# Claude design brief

Paste everything between the rules into Claude to generate and iterate on the UI. It is
self-contained: real node names, real diagnosis text and real numbers from our experiments, so the
output is grounded rather than lorem ipsum. Iteration prompts are at the bottom.

---

## THE BRIEF

I'm building **Open Tutor**, an adaptive maths tutor, and I need you to design the interface.
Produce a single self-contained HTML artifact (inline CSS/JS, no external requests) that is
phone-first but works on desktop.

### What the product does, in one sentence

**Open Tutor finds the one thing actually blocking you and fixes it.**

A student states what they want to understand ("how neural networks learn"). The app maps their
knowledge onto a prerequisite graph, gives them six problems a day, and when they get one wrong it
reads their handwritten working and identifies *which specific skill* failed. Crucially, that is
usually not the topic of the problem. Most calculus errors are algebra, sign or trig errors wearing
a calculus costume. The product exists to say:

> "Your chain rule is correct. You dropped the negative when you distributed across -(3x-2).
> That's the same mistake as Tuesday."

That sentence is the entire product. Design around it.

### Who it's for

Adults and older students who want to genuinely understand something (usually machine learning),
who work problems on paper and photograph them. Not children. They are motivated and impatient.

### Tone and aesthetic

The right metaphor is **training with a good coach**, not a game show. Think a serious training
log or a climbing route map. Precise, calm, a little austere, with real typographic craft.

- **Avoid:** cartoon mascots, gems, hearts, lives, confetti for trivial actions, rounded pastel
  everything, "Great job!!" copy. The moment this looks like Duolingo it reads as a toy and the
  audience leaves.
- **Aim for:** dense but calm, information-rich, restrained colour used to mean something specific,
  generous type hierarchy, one clear focal point per screen.
- Dark and light themes both, real support, not an afterthought.

### Screens

Seven. Each does exactly one job. Design all seven, and let me navigate between them.

**1. Goal.** A single input: "What do you want to understand?" Pre-filled with "How neural networks
learn". Feels like the start of something serious, not a signup form.

**2. The Path** (the main screen, and the game board). The prerequisite graph rendered as a map,
with the target pinned at the top. Node states, each visually distinct:
- `mastered` (lit / gold)
- `learning` (partially lit)
- `edge` (glowing, this is where new learning happens)
- `locked` (dim)

Real nodes to use, roughly bottom to top:
`Distributing a negative across a sum or difference` · `Laws of exponents` ·
`Adding and simplifying rational expressions` · `Exact values of sine and cosine at standard angles` ·
`Informal idea of a limit` · `Resolving 0/0 limits by factoring` ·
`The derivative as a limit of the difference quotient` · `Power rule` · `Product rule` ·
`Quotient rule` · `Chain rule` · `Partial derivatives` · `The gradient vector` ·
`Directional derivatives and steepest descent` · **`The gradient descent update step`** (target)

Also on this screen:
- **34% of the way there · about 18 days at your current pace**
- A **Blockers** panel: open misconceptions in plain language. Currently:
  *"Dropping negatives across brackets — 4 times this week"* and
  *"Subtracting fractions by subtracting denominators — twice"*

**3. Today's Set.** Six problems. Each shows *why it was chosen*, which is unusual and worth making
a feature of. Reasons in order: `Blocker · sign distribution`, `Blocker · fractions`,
`Review · due today`, `Review · due today`, `New · Chain rule`, `On your path to gradient descent`.
Roughly 15 minutes of work.

**4. Solve.** One problem: *"Differentiate f(x) = (2x + 1) / (x − 3)"*. A typed answer box (maths
notation, so it needs to render nicely), a camera/upload button for a photo of paper working, and a
**Hint** control with four escalating levels. Hints must never feel punitive or gated.

**5. Check.** After the photo: *"Here's what we read. Is this right?"* showing the transcription:
```
f'(x) = [(2)(x-3) - (2x+1)(1)] / (x-3)²
      = [2x - 6 - 2x + 1] / (x-3)²
      = -5 / (x-3)²
```
with a confirm and an easy correct-it path. This screen exists because OCR is imperfect, and it
should make the system feel honest rather than magical. Do not make it feel like an obstacle.

**6. Result.** The most important screen. Two-stage:
- **Instantly:** the verdict. Incorrect. Expected `-7/(x-3)²`.
- **About 3 seconds later:** the diagnosis arrives. Show that arrival, don't just pop it in.

The diagnosis, verbatim:
> **Your quotient rule is correct.** You dropped the negative when distributing across
> `-(2x+1)`, so `-2x-1` became `-2x+1`. This is the same mistake as Tuesday.

Highlight the exact failing line in their work: `= [2x - 6 - 2x + 1] / (x-3)²`.
Then show the consequence: the skill **Distributing a negative across a sum or difference** drops
from mastered to needs-work. And a small, non-defensive **"Was this actually your mistake?"** with
yes/no.

The emotional beat here is **being seen**. Give it the most space and the most craft.

**7. Session complete.** XP earned (+120), nodes lit today, **"3 topics unlocked"**, and one line
naming tomorrow's focus: *"Tomorrow starts with sign distribution."* The reward moment is a node
turning gold **and the frontier visibly expanding** on the map, so show that, not just a number.

### Constraints

- Maths must render legibly. Use clean HTML/CSS for notation (superscripts, fractions, `f'(x)`);
  no external maths libraries since the artifact must be self-contained.
- A language toggle in the header (English / हिंदी). Devanagari for prose, mathematical notation
  unchanged in both. Show at least one screen in Hindi so I can check it holds up.
- Phone-first. Assume one-handed use, thumb-reachable primary actions.
- Every colour that carries meaning must also be distinguishable without colour.

### What I want from you

1. All seven screens, navigable.
2. A real design point of view. Make choices and tell me why, especially about how The Path is laid
   out and how the Result screen's two-stage reveal should feel.
3. Do not design a generic dashboard. This should look like it was made by someone with taste and
   an opinion about learning.

---

## ITERATION PROMPTS

After the first pass, go one at a time. Changing one thing per round keeps the good parts.

- "The Path is the game board and it isn't carrying enough weight. Redesign just that screen three
  different ways: a vertical climbing route, a constellation, and a subway map. Show all three."
- "The Result screen's diagnosis should feel like a person noticed something. Right now it reads
  like a form validation error. Rework the typography and pacing of the reveal."
- "Show the state transition on The Path when a mastered node drops back to needs-work. It should
  feel consequential but not punishing."
- "Design the four hint levels. They must never feel like a penalty, but level 4 reveals the full
  solution and then immediately gives a fresh twin problem, and that has to feel deliberate."
- "The Blockers panel is the most valuable thing in the app and currently looks like a to-do list.
  Give it the weight it deserves."
- "Make the whole thing feel 20% more austere and confident. Less UI, more content."
- "Show me the empty state: day one, nothing known, everything locked. It must feel like the start
  of something, not an error."

## WHAT NOT TO ACCEPT

- Purple-to-blue gradients, glassmorphism cards, generic SaaS dashboard layouts.
- A Result screen where the diagnosis is a small grey box under a big red X.
- Progress bars as the primary progress metaphor. The graph is the progress metaphor.
- Any screen where the most prominent element is a number rather than a sentence.
