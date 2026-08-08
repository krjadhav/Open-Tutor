# Open Tutor: learning design and idea evaluation

> **Reasoning and results, in the order we learned them.** For a plain
> description of the engine as built, read [engine.md](engine.md). Sections 14
> to 17 are the measured experiments and remain the evidence base.

Status: pre-MVP evaluation. No code decisions here, only the learning model and what is worth building.

---

## 1. Verdict first

The loop you wrote is correct and it is the right loop. My concerns are not with the loop, they are with three things:

1. **It is all assessment and no teaching.** "Curate a problem set" assumes the student can already attempt the problems. Math Academy's atomic unit is not a problem set, it is a *lesson*: explicit instruction on one tiny topic, then worked examples, then scaffolded practice. If a student who does not know calculus is handed calculus problems, you have built a diagnostic, not a tutor. The loop needs a TEACH stage.

2. **The knowledge graph is the moat, and you cannot hand-build it.** Math Academy spent roughly a decade building thousands of atomic topics with calibrated prerequisite edges and item banks. That is the actual product. In a weekend you have two options: hand-author a tiny graph, or make *graph compilation from a textbook* one of the things you demo. I strongly recommend the second, because it is the thing that is newly possible and it is the only honest answer to "why now, and why is this not just Math Academy".

3. **The interesting part of your loop is under-specified.** "Evaluate → update graph" is written as if it were bookkeeping. It is the entire intellectual content of the product. Everything below is mostly about that one arrow.

Overall: this is a good hackathon idea with a real differentiator available to it, and a real risk of collapsing into "GPT wrapper that grades homework photos". The difference between those two outcomes is section 3.

---

## 2. What Math Academy actually does, compressed

From their write-up, the parts that matter to us:

- **Graph of atomic topics**, thousands of them, 4th grade to university. Edges are prerequisites. A separate relation, *encompassing*, marks when topic A is a component skill exercised inside topic B.
- **FIRe (Fractional Implicit Repetition)**: doing a hard problem gives *partial, discounted* spaced-repetition credit to the component skills used inside it. Credit ripples down the graph.
- **Repetition compression**: when many reviews are due, pick the single task whose component skills knock out the most of them at once. This is a set-cover problem and it is why their sessions are short.
- **Adaptive diagnostic**: locate the *knowledge frontier* (the boundary between known and unknown) with an order of magnitude fewer questions than testing every topic, by picking maximum-information questions and discounting slow-but-correct answers.
- **Per-student-per-topic learning speed** multiplier (2x means easy, review less often; 0.5x means hard, review more often).
- **Target ~80% accuracy.** Not 100%, not 50%. Difficulty is tuned to sit in the desirable-difficulty band.
- **Errors trigger immediate remediation**, and repeated failure halts the lesson and sends the student to foundations rather than letting them grind.

Two design principles worth stealing verbatim: *progress is demonstrated competence, not coverage*, and *never let a student practice a topic whose prerequisites are not yet solid*.

---

## 3. The elegant core: credit and blame on a graph

Here is the one idea I would build the whole product around.

> Solving a problem is a **forward pass** through the prerequisite graph. Diagnosing an error is a **backward pass**. Learning is credit assignment; tutoring is blame assignment.

Math Academy does the forward half well: credit ripples *down* from a hard topic to the component skills it exercised. What they cannot do cheaply, and what an LLM makes newly possible, is the backward half at *step* granularity:

A student gets an integration-by-parts problem wrong. The classical system records `integration_by_parts: incorrect` and schedules more integration by parts. That is usually the wrong response, because most calculus errors are not calculus errors. They are algebra errors, sign errors, fraction errors, trig-identity errors, wearing a calculus costume.

The right response is to read the student's actual work, find the step where it went wrong, and attribute the failure to the *specific node* responsible, which is frequently a prerequisite the student was already marked as having mastered. Then push the negative evidence *down* the graph and re-open that node.

That single capability is:
- the reason the photo modality matters (you cannot do step-level blame from a final answer, or from an MCQ)
- the reason you need a graph at all (blame needs somewhere to land)
- the reason you need a strong LLM (mapping a scrawled step to a misconception is not something a rule engine does)
- the most emotionally powerful thing you can show a judge, because it is the experience of being *seen*: "You do understand integration by parts. You are dropping the negative sign when you distribute across a difference. That has now cost you 4 problems this week."

Everything else in the product is scaffolding around that sentence. If the demo produces that sentence, you win. If it does not, you have built a homework scanner.

**Naming**: internally I would call the backward pass *blame propagation*. Externally, the student-facing artifact is a short list called "what is actually holding you back".

---

## 4. The learning model, concretely

### 4.1 Graph

```
Node {
  id, title, kind: skill | concept,
  prereqs:     [{ id, weight }]      # weight = how load-bearing, 0..1
  encompasses: [{ id, credit }]      # component skills, credit ~0.2..0.4
  difficulty:  b                     # Rasch-style item/topic difficulty
  goal_tags:   ["gradient-descent", "jee-mains", ...]
}
```

Hard constraint: the prereq relation must be a DAG. Validate this on ingest, because an LLM-generated graph will produce cycles and you will lose an hour to it at 3am.

`encompasses` is not the same as `prereqs`. Prereqs are what you needed *before*; encompassings are what you actually *exercised during*. Math Academy is explicit that only encompassings earn implicit credit. Most of the time encompassings are a subset of the transitive prereqs, so you can bootstrap them as "direct prereqs of kind=skill" and refine by hand for the demo slice.

### 4.2 Per-student node state

```
State {
  a, b            # Beta posterior over mastery; p = a / (a + b)
  stability S     # days; memory half-life
  last_seen       # timestamp
  speed           # per-student-per-topic multiplier, 0.5 .. 2.0
  open_misconceptions: [tag]
}
```

Use a Beta posterior rather than a scalar. It costs nothing and it buys you two things you will want: an honest "we are not sure yet" state for the diagnostic, and a principled way to weight evidence of different quality.

**Retrievability**: `R(t) = exp(-Δt / S)`. Effective mastery is `p_eff = p · R`. A node is *due* when `R < 0.85`.

**Status** is derived, never stored:
- `locked`: some prereq has `p_eff < 0.7`
- `frontier`: all prereqs `p_eff ≥ 0.7` and own `p < 0.7`  ← this is the only place new learning happens
- `learning`: `0.7 ≤ p < 0.9`
- `mastered`: `p ≥ 0.9` and at least 3 spaced successes

### 4.3 The four update rules

Everything that happens to the graph is one of these four. Keep it to four.

**(1) Direct evidence.** The student attempted a problem tagged to node X.
```
w = quality × (1 - 0.25·hint_level)     # quality: photo w/ full work 1.0, typed answer 0.7, MCQ 0.4
a += w·correct ;  b += w·(1-correct)
```
Evidence quality matters. An MCQ is weak evidence and should move mastery less than a photo of complete correct work. This is also your honest argument for why the camera flow exists: it is the highest-bandwidth evidence channel you have.

**(2) Implicit credit (FIRe, downward).** Correct solution of X gives discounted credit to the component skills *the solution actually used*. You know which ones because the diagnosis step returns them.
```
for each child c in encompasses(X) actually used:
    a_c += credit_c · w · correct
```
Discount hard (credit ≈ 0.25). Implicit reps are worth less than explicit ones and often arrive too early to count as a proper spaced repetition.

**(3) Blame propagation (the backward pass).** The diagnosis returns `{failed_step, blamed_node, misconception_tag, confidence}`. Note that `confidence` turned out to be uninformative in testing (§14), so it stays in the payload for logging but must not gate anything.
```
b_blamed += λ · confidence          # λ ≈ 1.5, blame hits harder than credit
S_blamed  = max(S_blamed · 0.5, 1)  # collapse the review interval
open_misconceptions[blamed] += tag
if blamed ≠ X: X's own evidence is discounted   # do not punish the topic for a prereq failure
```
That last line is the crux and is worth saying out loud in the pitch: **a student is never marked down on a topic they actually understood.** Failure is routed to its true cause.

**(4) Decay and consolidation.** On a successful spaced retrieval:
```
S ← S · (1 + β · speed)      # β ≈ 0.9, so intervals roughly double when things are going well
speed ← EMA of (recent success rate, response time vs expected)
```
On failure, `S ← max(S/2, 1)`.

This is FSRS-shaped and you can defend it in one line: intervals grow multiplicatively with success and collapse on failure, per-topic per-student.

### 4.4 Curating the daily set

Set size N = 6 for the demo (about 12 to 15 minutes on paper). Composition budget, in this order:

| Slot | Count | Rule |
|---|---|---|
| Remediation | 0 to 2 | Any node with an open misconception, highest blame first. These come **first** in the set. |
| Review | 2 | Chosen by *repetition compression*: greedy set cover over due nodes, pick the item whose `encompasses` set covers the most due-weight. |
| New | 1 to 2 | Frontier nodes only. Prefer ones on the goal path. |
| Goal link | 1 | A problem that visibly connects to the stated goal ("this is the derivative you will differentiate through in backprop"). |

Two constraints on top:

- **Interleave.** Never place two items from the same graph neighbourhood adjacently. Maximize mean pairwise graph distance within the set. This is the single cheapest evidence-backed win in the whole design and it is one line of code.
- **Target difficulty.** Pick the item whose predicted success is closest to 0.85: `P(correct) = σ(θ_node − b_item)`, `θ = logit(p_eff)`. Do not serve problems the model expects them to fail.

### 4.5 Teaching a new node (the missing stage)

When a frontier node is introduced, do not open with a problem. Use the worked-example ladder, three touches, roughly four minutes:

1. **Worked example.** Full solution, annotated by step, each step labelled with the node it exercises. You get these free from the textbook's solution manual.
2. **Faded example.** Same structure, last one or two steps blanked. Completion problems are measurably better than pure problem-solving for novices.
3. **Independent problem.** Now it counts as evidence.

Fading is what makes this feel like a tutor rather than a slideshow, and it is nearly free to build.

### 4.6 The hint ladder

Hints must never be blocked and must never cost XP. They cost *evidence weight*, which is the honest currency.

1. "Which idea applies here?" (names the node)
2. "Here is the first step."
3. "Here is the step you got wrong." (only available after an attempt, and this is where the photo pays off)
4. Full worked solution, **immediately followed by an isomorphic twin problem.**

Rung 4 is non-negotiable. Revealing the answer without a re-test is how students fake mastery. The twin problem is what converts a reveal back into evidence.

### 4.7 Cold start

Do not ship a 20-question placement test. Frame it as "Day 0" and make it 6 to 8 items:

- Take the topological spine of the goal slice.
- Binary search it. Ask a mid-depth node. Correct means jump up, incorrect means jump down. Each answer roughly halves the frontier's location.
- Discount correct-but-slow answers, per Math Academy.
- Leave everything untested as a wide Beta prior, explicitly "unknown", and let the first three days sharpen it. Say this to the student: "we will keep calibrating for a few days."

Honesty about uncertainty is a feature. It also covers you when the demo's day-1 estimate is wrong.

---

## 5. Gamification that survives contact with a motivated cheater

The Math Academy note in the research file is right: gamify, but align it with the goal and make it hack-resistant. Concretely:

**XP formula**
```
XP = depth(node) × novelty × quality
novelty: new frontier node 3.0 | due review 1.0 | not-due repractice 0.15
quality: hint-free 1.0 | hinted 0.6 | reveal+twin-correct 0.4
```
Grinding easy problems earns almost nothing (novelty 0.15 and low depth). Since difficulty is auto-targeted at 85% success, you cannot farm easy wins either. That makes a leaderboard safe.

**The board is the graph.** Do not bolt points onto a list of chapters. The primary screen is the goal slice rendered as a map: mastered nodes lit, frontier glowing, locked nodes dim, the target ("Gradient Descent") at the top. The reward moment is a node turning gold *and the frontier visibly expanding*, with "this unlocked 3 new topics". That is a better dopamine hit than a number going up, and it is honest, because it is literally the state of their knowledge.

**Streaks, carefully.** Streak on "days with a completed set", with one free skip per week. Punishing streaks drive quitting, which is the opposite of the goal.

**No hearts, no lives, no blocking.** Anything that stops a motivated student from learning is a bug.

**The one metric to show the student**: percentage of the goal slice mastered, plus estimated days to goal at current pace. "You are 34% of the way to reading the Adam paper, about 18 days at your pace" is a far stronger retention hook than any badge.

---

## 6. What makes this not just a Math Academy clone

Four things, in descending order of how much I believe them:

1. **Goal-conditioned graph slicing.** The student states a target in natural language ("I want to understand backpropagation", "I want to clear JEE Mains calculus", "I want to read this paper"), and the system computes the induced subgraph from their current frontier to that target and prunes everything else. Math Academy sells fixed courses. This answers "why am I learning this" on every single problem, which is the number one reason people quit maths. It is also cheap: an LLM maps the goal to target nodes, and the rest is graph reachability.

2. **Graph compiled from a book, not hand-authored.** Point the pipeline at an open textbook and get a candidate DAG plus a tagged item bank. This is what turns a weekend project into a plausible platform, and it is the India-scale story: many boards, many syllabi, many languages, no way to hand-author them all. Be honest that the output needs human review. "Ten years of curriculum work becomes an afternoon plus a review pass" is the claim.

3. **Paper-first, not MCQ-first.** Indian students do maths on paper. Every existing app forces them into MCQ or typed input, which discards the working, which is exactly where the diagnostic signal lives. Photo-in is not a gimmick here, it is the only way to get step-level evidence. This also makes the Sarvam Vision integration load-bearing rather than decorative.

4. **Instruction in L1, notation universal.** Mathematics is the ideal domain for this: the notation is language-independent and only the prose needs translating. So the marginal cost of 11 languages is close to zero, and the impact is large. Important implementation note: translate the *node explanations, hints and misconception messages* offline and cache them. Do not translate per-request at runtime, it is slow, expensive and inconsistent across a session.

Item 4 plus item 3 is the strongest Sarvam-specific pitch: a Class 12 student in a Tamil-medium school solves on paper, photographs it, and gets step-level diagnosis in Tamil. No one else can serve that student.

---

## 7. Where this breaks, and what to do about it

| Risk | Severity | Mitigation |
|---|---|---|
| Handwriting OCR fails on messy work, fractions, integrals | **Highest** | **Never let grading depend on OCR.** The student types the final answer; that is graded deterministically. The photo feeds *diagnosis only*, which is advisory. If OCR is garbage, the product degrades to "correct/incorrect plus generic coaching" instead of breaking. |
| LLM grades maths wrong | High | LLM is never the grader. Final answers are checked by a CAS (sympy) for symbolic equivalence. **Deterministic where possible, generative where necessary.** The LLM's job is diagnosis and explanation, where being approximately right is acceptable. |
| LLM-generated problems are broken or duplicated | High | Use the textbook's real exercises as the item bank. Generation is only for isomorphic twins after a reveal, and every generated item is validated by CAS before it is shown. |
| LLM-generated graph has cycles or nonsense edges | Medium | DAG validation on ingest, plus a reachability check that every node has a path from the assumed-known roots. Hand-fix the ~40-node demo slice. |
| Blame lands on the wrong node | Medium | **Confidence cannot be used as a gate, this was measured and it failed (§14).** Instead: cap the magnitude of any single blame update, and make blame *visible and contestable* in the UI ("was this the actual mistake? yes / no"), which is both better UX and a data flywheel. |
| Student photographs a solution copied from the internet | Low for MVP | XP design already makes this pointless. Note it and move on. |
| **Thomas' Calculus is copyrighted and that GitHub PDF is a pirated upload** | Medium, real | Switch to [OpenStax Calculus Volume 1](https://openstax.org/books/calculus-volume-1/pages/preface), CC BY-NC-SA 4.0, with published [answer keys](https://openstax.org/books/calculus-volume-1/pages/chapter-1) for odd-numbered exercises. Equivalent coverage, clean provenance, and "we can ingest any open textbook" is a better story than "we scraped a pirated PDF". The NC clause matters only if you commercialize later. |
| Scope | **Highest, honestly** | Section 9. |

The two principles worth putting on a slide:

> **Deterministic where possible, generative where necessary.**
> **The model never decides if you are right. It only explains why you were wrong.**

That framing also pre-empts the most obvious judge question, which is "how do you know the AI is not hallucinating the grade".

---

## 8. Content plan

- **Source**: OpenStax Calculus Vol 1, plus a small hand-written prereq layer for algebra and trig (roughly 10 nodes: sign distribution, fraction arithmetic, exponent rules, factoring, the unit circle, basic identities). Those prereq nodes are where most blame will land, so they matter more than the calculus nodes.
- **Item bank**: tag odd-numbered exercises (which have published answers) to nodes. Target 150 to 200 items for the demo slice. Store `{stem_latex, answer, difficulty_b, node_id, encompasses[]}`.
- **Worked examples**: the book's in-text examples are already step-structured. Use them directly for the teach ladder.
- **Difficulty**: seed `b` from position within the exercise set (textbook exercises are roughly ordered by difficulty), then update online.

---

## 9. What I would cut for Sunday

The design above is a year of work. The hackathon version must be **complete but shallow**: every stage of the loop present, none of them deep. Specifically:

**Keep**
- One goal slice, roughly 30 to 40 nodes: algebra and trig prereqs → limits → continuity → derivative definition → power/product/quotient rules → chain rule → partial derivatives → gradient → gradient descent. This slice tells the "calculus to understand AI" story end to end and it is small enough to hand-verify.
- The full loop for a set of 6, running for real.
- Blame propagation with visible graph updates. This is the demo.
- Photo path, with typed-final-answer as the graded ground truth.
- One second language end to end (pick the one your team can sanity-check), with cached translations.

**Cut or fake**
- Multi-course, multi-goal. One goal, hardcoded target node.
- The graph compiler runs *offline before the demo*, not live. Show the artifact, mention the pipeline, do not run an LLM over a textbook on stage.
- Bulbul TTS: last hour, only if everything else works. It is a genuine accessibility point but it is not what wins.
- Leaderboards and social. XP and the graph map are enough.

**Do this one thing**: seed a fake 5-day history for the demo student. A day-1 empty graph is unimpressive. A graph with mastered regions, a lit frontier, one node flashing red with "sign errors, 4 occurrences this week", and a set curated around it, tells the whole story in one screen. This is the highest-leverage 30 minutes of demo prep available to you.

**Demo script, 90 seconds**
1. "I want to understand how neural networks learn." → the map appears, target at the top, 34% lit.
2. Today's set of 6. Student solves problem 3 on paper, photographs it.
3. Verdict: final answer wrong. Diagnosis: "Your chain rule is correct. You dropped the negative when distributing across `-(3x - 2)`. Same mistake as Tuesday."
4. The graph animates: chain rule stays gold, the algebra node dims and reopens, tomorrow's set rebuilds live with a sign-distribution drill at the top.
5. Language toggle. Same problem, same hint, in Tamil or Hindi. Notation unchanged.

Step 3 is the product. Everything else is framing.

---

## 10. De-risk before you build: three experiments, 30 minutes each

Run these before writing any application code. Each one can kill a pillar, and it is much cheaper to know on Friday.

1. **Vision**: photograph 5 real handwritten calculus solutions, varying neatness. Push through Sarvam Vision. Question: *can you recover step structure*, not perfect LaTeX. If steps are recoverable, the photo pillar is real. If not, fall back to "type your steps" and keep the camera as a stretch feature.
2. **Diagnosis**: hand-write 10 wrong solutions with known error causes. Give Sarvam-105B the solution text plus the list of candidate node IDs, ask for `{failed_step, blamed_node, misconception_tag, confidence}` as structured output. Question: *does it pick the right node?* This measures the core claim. Below roughly 7 out of 10 and the pitch needs reframing.
3. **Graph**: give 105B one OpenStax chapter and ask for atomic nodes plus prereq edges. Question: *is the output a sane DAG?* Determines whether the compiler is a demo or a slide.

Experiment 2 is the one that matters. If it works, build this. If it does not, the honest pivot is a well-executed spaced-repetition tutor with good gamification, which is a weaker but still viable submission.

---

## 11. Decisions taken

| Decision | Choice | Consequence |
|---|---|---|
| Goal slice | **Calculus for AI**, target node = gradient descent | ~30 nodes: algebra and trig prereqs → limits → continuity → derivative definition → power/product/quotient rules → chain rule → partial derivatives → gradient → gradient descent. Every problem can show its position on the path to the target. |
| Frontend | **Responsive web, camera via file input** | Opens on a phone, looks identical to native in a demo, and the saved hours go into the graph and diagnosis, which is where the demo is won. |
| Graph source | **Hand-author the demo slice, mention the compiler** | ~30 to 40 hand-verified nodes so blame propagation lands on the right node every time. The compiler is a slide plus, if experiment 3 passes, an offline artifact to show. Nothing is generated live on stage. |
| Second language | **Hindi** | Devanagari plus LaTeX renders fine. See §13 for the one translation trap. |
| Persistence | **Real database, not JSON files** | Intent is to sell to early users, so build for that from the start. See §12. |

### Still open

- **Stack**: framework for the responsive web app. Anything you can ship fast is fine; the engine is where the risk lives.

---

## 12. Data model, given that you intend to sell this

Selling changes two things: persistence, and content licensing.

### 12.1 Store attempts as an immutable event log, derive mastery from it

This is the one architectural decision that matters and it is easy to get wrong. Do **not** make `node_state` the source of truth with in-place updates.

```
students          (id, goal_node, lang, created_at)
nodes             (id, title, kind, difficulty_b, goal_tags)      # static per course
edges             (from, to, kind: prereq|encompass, weight)
items             (id, node_id, stem_latex, answer, difficulty_b, source, encompasses[])
attempts          (id, student_id, item_id, node_id, answer_given, correct,
                   hint_level, photo_url, ocr_text, diagnosis_json,
                   latency_ms, created_at)                        # append only, never updated
node_state        (student_id, node_id, a, b, S, last_seen, speed, misconceptions[])  # DERIVED
daily_sets        (id, student_id, date, item_ids[], rationale_json)
translations      (entity_type, entity_id, lang, text)
```

`node_state` is a materialized view over `attempts`. Rebuildable by replaying the log.

Why this matters commercially: you are going to tune the blame weight λ, the consolidation rate β, the implicit-credit discount and the mastery thresholds *many* times, and your only real evidence about whether a change helped will be your early users' data. If mastery is derived, you can replay the entire user base under new parameters and compare offline. If you mutate state in place, that history is gone forever and you are tuning blind. This costs nothing extra to build now.

Store `rationale_json` on each daily set too (why each item was picked). It is your debugging tool, and it is also a genuinely nice user-facing feature: "here is why we gave you this."

**Suggested stack**: Postgres via Supabase. You get auth, row-level security, and object storage for the handwriting photos in one place, which otherwise costs you an afternoon. Nothing here needs anything exotic.

### 12.2 Content licensing is now a real problem, not a footnote

Both credible open calculus sources restrict commercial use:

- [OpenStax Calculus Vol 1](https://openstax.org/books/calculus-volume-1/pages/preface): **CC BY-NC-SA 4.0**. The NC clause blocks a paid product.
- [Active Calculus](https://activecalculus.org/): CC BY-SA, but the authors attach an explicit no-selling-for-profit condition.

So: **OpenStax is fine for the hackathon** (non-commercial, attributed) **and cannot ship in a paid product.** Plan the migration now rather than discovering it after your first invoice.

What is safe to carry across:

- **The graph itself.** Node names, prerequisite edges and the skill taxonomy are ideas and facts, not protected expression. Derive freely.
- **Problems you generate yourself**, CAS-validated and human-reviewed. Mathematical content is not copyrightable, only the specific wording of an exercise. "Differentiate x·sin(x)" belongs to nobody. This is what commercial edtech does, and you already need generation for the isomorphic-twin feature, so the machinery exists.
- **Worked examples you write yourself.** Do not copy the book's prose.
- **Past exam papers**, with the usual caution about verifying reproduction rights.

Concrete step that costs you nothing today: put a `source` column on `items` (`openstax` | `generated` | `original`) from the first migration. When you commercialize, you delete one class of rows and regenerate, instead of re-architecting.

---

## 13. Hindi: the one trap

Do not hand LaTeX to a translation model. It will reflow, re-order or silently mangle the math, and you will not notice until a judge does.

Template the math out before translating and substitute back after:

```
"Differentiate $f(x) = x\sin x$ using the product rule"
  → "Differentiate {{m1}} using the product rule"     → translate prose only
  → "{{m1}} का अवकलन गुणनफल नियम से कीजिए"            → substitute {{m1}} back
```

Other notes:

- Translate **offline and cache into the `translations` table**: node titles, explanations, hint rungs, misconception messages, UI strings. These are a fixed, small set. Do not translate per request; it is slow, costs money on every view, and produces different wording for the same string across a session, which reads as broken.
- Diagnosis output is the exception, since it is generated per attempt. Either ask Sarvam-105B to answer in Hindi directly, or generate in English and translate the prose. Prefer the former if quality holds, since it is one call instead of two.
- Keep mathematical notation and variable names untouched in every language. That is the whole reason maths is cheap to localize.
- Have your Hindi reader check the *hint rungs* specifically. A mistranslated hint is worse than no hint, and hints are what the demo will show.

---

## 14. Experiment 2 result: GO

Run on 2026-08-07 against `sarvam-105b`. 12 cases (4 prereq-blame, 6 topic-blame, 2 control),
three independent passes at temperature 0. Harness and cases in `experiments/exp2_diagnosis/`.

**Aggregate across 30 error-case trials: 25/30, 83%.**

| Case | Type | 3 runs | Notes |
|---|---|---|---|
| c01 sign distribution | prereq | 3/3 | The headline case. Correctly ignored the quotient rule and blamed the algebra. |
| c03 product of derivatives | topic | 3/3 | |
| c05 unit circle value | prereq | 3/3 | |
| c07 quotient order | topic | 3/3 | |
| c08 partial not held constant | topic | 3/3 | |
| c09 ascent not descent | topic | 3/3 | |
| c10 fraction subtraction | prereq | 3/3 | Looked past `der.definition` to `alg.fraction-arithmetic`. |
| c11 control, correct | control | 3/3 | |
| c02 missing inner derivative | topic | 2/3 | |
| c04 negative exponent | prereq | 1/3 | Blamed `alg.sign-distribution` instead of `alg.exponent-rules`. Arguably defensible; ground truth may be too strict. |
| c06 indeterminate as DNE | topic | 1/3 | Blamed `lim.concept` twice. See finding 4. |
| c12 control, unsimplified | control | 1/3 | The most damaging failure mode. See finding 3. |

**The thesis holds.** Eight of twelve cases are stable across every run, including all four that
directly test the product claim. The model reliably separates "you misapplied the rule" from
"the rule was fine, your algebra failed", and it writes the diagnosis in usable language without
prompting for tone:

> "You correctly set up the limit definition of the derivative. The error occurred when you
> combined the fractions in the numerator. You cannot subtract fractions by subtracting their
> denominators."

That is the sentence the product exists to produce, and the model produces it unassisted.

### Findings that change the build

**1. Diagnosis must be asynchronous. This is now a hard constraint.**
Mean latency 21 to 24s, worst observed 73s. `reasoning_effort: low` saves only about 3 seconds
and costs real accuracy (7/10 vs 9/10), so it is not a useful trade. The API rejects
`reasoning_effort: none` outright, so thinking cannot be switched off. Design consequence: the
CAS returns correct/incorrect **instantly**, the student moves on, and the diagnosis arrives a
moment later. This is better UX than blocking anyway, but it is no longer optional.

**2. Never ask the model whether the student is right. This is now empirically justified, not
just a principle.** Error cases scored 83%; controls scored 4/6. The single worst outcome in the
whole experiment was the model inventing `alg.sign-distribution` as a fault in `x - 5 + x + 2`,
a correct answer left unsimplified. Telling a student who was right that they were wrong is far
more damaging than any missed diagnosis. Fix: **the CAS decides correctness and the model is only
invoked when the CAS says the answer is wrong.** That deletes the entire false-positive class,
and it means the control cases would never reach the model in production.

**3. Confidence is not usable as a gate.** Mean confidence 0.97 when right, 0.93 to 0.95 when
wrong. Not separable. The §7 mitigation "only act above a confidence threshold" does not work.
Replace it with: cap the size of any single blame update, and let the student contest the blame
in the UI.

**4. Vague node titles are blame magnets.** `lim.concept` ("Informal idea of a limit") pulled
blame twice away from the correct, more specific `lim.indeterminate-factoring`. Any node whose
title is broad enough to describe many errors will over-attract. Two consequences for the graph:
keep titles narrow and mutually exclusive, and give each node a one-line discriminator in the
prompt ("responsible for errors where the student ..."). This matters much more as the graph
grows past 36 nodes.

**5. temperature 0 is not deterministic.** The same case flipped verdict across runs (c02, c04,
c06, c12 all changed). Two operational consequences: never trust a single-run evaluation, and
**pre-compute and cache the diagnosis for anything shown on stage.** Do not let the demo depend
on a live coin flip.

**6. Sarvam transport quirks worth knowing before Sunday.**
- Thinking mode is ON by default and `reasoning_content` is billed against `max_tokens`. Under-budget it and you get `finish_reason: "length"` with **empty `content` and no error**. This silently looks like a model failure and is not one.
- Starter tier caps `max_tokens` at 4096, so you cannot buy your way out. The working pattern is a fallback: on truncation, retry once at `reasoning_effort: low`, trading reasoning depth for output budget. Implemented in `run.py`.
- There is no `response_format` or JSON mode. Structured output is prompt-enforced. Ship the fence-stripping plus balanced-brace parser, and the truncated-object salvage regex, both in `run.py`. They recovered 3 of 4 truncated responses in the first run.

### Not yet tested

Everything here used clean transcriptions. **Experiment 1 (Sarvam Vision on real handwriting) is
now the remaining risk**, and it is the one that decides whether the input to this pipeline is
trustworthy. Diagnosis quality on noisy OCR output is unmeasured.

---

## 15. Experiment 1 result: the photo pillar holds

Run 2026-08-07. One phone photo of a lined page carrying four handwritten solutions (c01 to c04),
through Sarvam Vision `doc_ai.digitise(content_type="handwritten")`, then through the identical
experiment 2 diagnosis path. Harness in `experiments/exp1_vision/`.

**Sample size is 4. Treat the headline percentage as noise and the attributions as the finding.**

### Transcription quality is high

| | similarity | token recall | minus signs |
|---|---|---|---|
| c01 | 0.91 | 0.95 | 8/8 |
| c02 | 1.00 | 1.00 | 0/0 |
| c03 | 0.94 | 0.97 | 0/0 |
| c04 | 0.97 | 1.00 | 7/5, two spurious |
| **mean** | **0.95** | **0.98** | |

Vision read `f'(x) = [(2)(x-3) - (2x+1)] / (x-3)²` off lined paper, at an angle, in ordinary
indoor light, including superscripts. Latency was about 7s for the page, three times faster than
the diagnosis call. The photo pillar is real.

### Neither failure was caused by OCR

End-to-end scored 2/4, against an 83% clean-text baseline. That gap looks alarming and it is not
what happened. Both failures were traced:

**c01, scored `BLAMED_SURFACE_TOPIC`.** OCR was faithful. The *writer* omitted the `(1)` from
`(2x+1)(1)` when copying the worksheet. Without that factor the numerator genuinely does look
like the `v'` term is missing, and the model said so: *"You correctly identified the parts of the
quotient rule, but you didn't include the v' term."* Given the input, that diagnosis is correct.
The ground truth assumed a factor the student never wrote.

**c04, scored `PARSE_FAIL` then `WRONG_NODE`.** OCR inserted a spurious `^-`, turning `x^(-3-1)`
into `x^-(-3-1)`, which changes the mathematics. So this looked like the clean OCR-caused failure.
It is not: re-running the diagnosis with the typo repaired returns **the same answer**,
`alg.sign-distribution`. c04 was already the weakest case in experiment 2 at 1/3, and the model
consistently prefers `alg.sign-distribution` for "computed -3 - 1 as -2". That reading is
defensible and the ground truth is probably too strict.

So: **zero of four failures trace to Vision.** Transcription was good enough that the diagnosis
model was robust even to the corruption that did occur.

### What this changes

**1. Students write more tersely than worksheets, and terse work is ambiguous.** c01 is the whole
lesson. Real work omits the steps a textbook prints, and each omission widens the space of
defensible diagnoses. This is inherent to diagnosing from partial work, not a model defect. Two
mitigations, both cheap: tell the diagnosis prompt that students routinely omit trivial factors
and identity steps, and prefer the blame that assumes competence; and rank two hypotheses instead
of one when the work is short.

**2. Show the transcription back to the student.** "Here is what we read, is this right?" with a
tap to correct. It costs one screen and it buys three things: it prevents a wrong diagnosis built
on a misread, it makes the system's reasoning legible instead of magical, and every correction is
labelled training data. Given that OCR inserted a spurious `^-` in one of four samples, this is
not optional politeness, it is the error boundary.

**3. Handwritten labels do not survive and must not be relied on.** Every `c01:` / `C02:` / `(03:`
marker came back as `Col:`, zero reading as o and one as l. Irrelevant for the product, because
the app knows which problem the student is on, but it means the page is not self-describing.
Anything that needs to associate work with a problem must carry that association out of band.

**4. Vision's response shape is undocumented and unintuitive.** Text is at
`documents[].pages[].blocks[].text` with a `reading_order` field to sort by; page-level `content`
comes back `null`. A naive walk of the payload picks up the job UUID and silently returns a
36-character "transcription". Parser in `exp1_vision/run.py:extract_digitise_text`.

### Standing risk

Four samples, all from one writer, one page, one lighting condition, English only. Before relying
on this in front of judges, run the remaining eight cases and get a second person's handwriting in
the set. The pillar looks sound; the evidence for it is still thin.

---

## 16. Experiment 2b: tool-calling changes the architecture

Run 2026-08-07. Same 12 cases, three passes, forced tool call with `blamed_node` constrained to
an enum of real node ids. Harness in `experiments/exp2b_tools/`.

**This supersedes findings 1 and 6 of §14.**

| | §14 prompt-enforced JSON | §16 forced tool call |
|---|---|---|
| error cases | 25/30, 83% | **27/30, 90%** |
| controls | 4/6 | **6/6** |
| parse failures | 1 to 8 per 12 calls | **0 / 36** |
| hallucinated node ids | possible | **0 / 36, structurally impossible** |
| mean latency | 23s | **3.2s** |
| worst latency | 73s | 7.9s |

Every metric improved and one of them by seven times. Adopt tool-calling everywhere the model
returns structured data, not just here.

**Diagnosis no longer has to be asynchronous.** §14 finding 1 said blocking the UI was impossible
at 23s. At 3.2s it is comfortable. Keep the CAS verdict instant regardless, since it is free and
correct, but the diagnosis can now land in the same interaction rather than arriving later. The
speedup appears to come from the tool schema suppressing the long free-text reasoning that was
consuming the token budget. This also removes the truncation-retry fallback: nothing truncated.

**Controls went to 6/6.** The false-positive case that most worried us in §14, inventing an error
in a correct-but-unsimplified answer, did not recur once in three passes. The rule from §14
finding 2 still stands (gate on the CAS, only diagnose confirmed-wrong answers) because it costs
nothing and removes the class entirely, but the model is no longer fragile here.

### Node titles are load-bearing prompt engineering

§14 finding 4 predicted that vague nodes attract blame. This run gave a clean, measured
demonstration and a fix.

c09 (`w_new = w + α·∇L`, gradient ascent instead of descent) scored **0/3**, consistently blamed
on `alg.sign-distribution`. The student message was pedagogically perfect every time: *"gradient
descent requires subtracting the learning rate times the gradient; using a plus sign moves you in
the wrong direction."* The diagnosis was right and the routing was wrong. `alg.sign-distribution`
was being read as a generic "sign error" bucket because its title said so.

Sharpening two titles moved c09 from **0/3 to 3/3**, and overall accuracy from 83% to 90%.

The fix is now in the graph as a `blame_hint` field on 12 of 36 nodes: a prompt-only one-line
discriminator, kept separate from `title` so the UI still shows "Power rule" while the diagnosis
prompt sees "Power rule [applying the rule itself; exponent arithmetic errors belong to
alg.exponent-rules]". Rendered by `exp2b_tools/run.py:render_nodes`.

**Every node added to the graph from here needs a blame_hint if its title could plausibly describe
someone else's error.** This is the highest-leverage half hour available when the graph grows.

### Still true, still unfixed

**`seed` does not pin the output.** Two identical passes at the same seed and temperature 0
agreed on 11 of 12 cases. §14 finding 5 stands unchanged: **pre-compute and cache the diagnosis
for anything shown on stage.**

**c08 remains weak, 1/3.** Partial derivative where the student differentiated both variables. It
scatters across `mv.partial-derivative`, `der.product-rule` and `der.definition`. The messages are
correct each time; only the routing wanders. A sharper `blame_hint` on `der.product-rule` and
`der.definition` is the obvious next attempt, but it was not fixed by this round.

Note the pattern in both c08 and c09: **the model's explanation is reliably right even when its
node choice is wrong.** The teaching output is more trustworthy than the routing. Where the two
disagree, show the student the message and treat the graph update as the lower-confidence half.

---

## 17. The item bank: textbook conversion, built

Built 2026-08-07. `pipeline/` produces `data/items/items.json`: **376 CAS-checkable items across
all 36 nodes**, none empty, none thin.

| source | items | licence |
|---|---|---|
| `openstax` | 244 | CC BY-NC-SA, hackathon only |
| `generated` | 132 | ours, ships in a paid product |

This is the half of "textbook conversion" the product actually consumes. Graph extraction stays a
pitch slide (§11); item extraction was never optional, because nothing downstream runs without
items bound to nodes.

### Scraping OpenStax (`fetch_openstax.py`, `mathml.py`)

13 sections, 660 exercises, 282 usable. Four things were load-bearing:

- **MathML needs a real converter.** OpenStax ships Presentation MathML with no TeX annotation.
  Naive tag-stripping turns `x^3 - 7x^2` into `x 3 - 7 x 2` and collapses fractions entirely,
  which is precisely the structure a calculus exercise consists of.
- **The encoding is undeclared.** OpenStax serves UTF-8 without saying so, `requests` falls back
  to ISO-8859-1, and every prime, minus and partial-derivative sign becomes mojibake.
- **Answer keys must be cross-checked.** Matching an answer by element id is not enough: **7
  answers had an exercise number disagreeing with their question's.** A silently wrong answer
  would poison the CAS grader, which is the component everything else trusts.
- **Stems are often bare.** OpenStax puts "find the derivative" in a shared group header, so a
  scraped stem is frequently just `$h(x)=x^{3}f(x)$` with no task. The group instruction has to be
  reattached or the item is unusable in front of a student.

### Tagging (`tag_items.py`)

281/282 tagged in 163s, zero low-confidence, using the enum-constrained forced tool call from §16.
Coverage on the calculus core is strong: chain rule 31, partial derivatives 28, multivariable
chain rule 22, derivative definition 19, critical points 17.

### The gap that mattered

**15 nodes had zero items, and they were exactly the nodes blame propagation routes errors to.**
A calculus textbook assumes sign distribution, fraction arithmetic, exponent rules and the unit
circle rather than drilling them, and no textbook contains the AI layer at all. Left unfixed, the
loop dead-ends at its most important step: the student is told what is blocking them and then
handed nothing to practise. The demo's climax, a sign-distribution drill appearing in tomorrow's
set, had no drill to serve.

### Generating the rest (`generate_drills.py`, `drill_tasks.py`)

132 drills for those 18 nodes, in 38s. **The model never writes an answer.** It emits a spec (task
plus sympy-syntax parameters); sympy computes the answer and the stem is rendered from the same
spec. A wrong answer is therefore impossible by construction rather than validated after the fact,
and question and answer cannot drift apart, which is the usual failure when an LLM writes both.

12 drills were rejected by the CAS for being no-ops ("expand `x+1`") or degenerate. All 132
survivors reload and verify through the production grader.

Two bugs worth remembering, both silent:

- **`implicit_multiplication_application` includes `split_symbols`**, which shreds multi-character
  names: `w1` becomes `w*1` and `w2` becomes `2*w`. Every backprop drill was differentiating with
  respect to a variable that no longer existed, and every answer came out `0`, plausibly. Use
  plain `implicit_multiplication`.
- **sympy answers do not round-trip through `str()`.** A solution set prints as `{2, 3}` and a
  gradient as `Matrix([[...]])`, neither of which parses back, so stored answers were unloadable
  at grading time. Answers now carry an `answer_kind` and are serialised deliberately.

### The grader

`drill_tasks.check_student_answer` is the production correctness check, and it is deliberately
generous about form: `x - 5 + x + 2` is accepted for `2x - 3`. Per §16, marking a correct student
wrong is the most damaging error the system can make.
