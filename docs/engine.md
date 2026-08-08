# The Open Tutor engine

**This is the source of truth for what the engine actually does today.** Every number here was read
out of the code, not remembered. Where a value is a guess, it says so.

Companion documents, and what they are for:

| document | what it is |
|---|---|
| `engine.md` (this) | what is built, and why it works that way |
| `learning-design.md` | the reasoning and the experiment results, in the order we learned them |
| `mvp-plan.md` | the plan we made before building; parts are now historical |
| `core_engine.md` | independent research by another dev, a spec for the same layer at 100x scale |
| `api-contract.md`, `ui-spec.md`, `demo-runbook.md` | the app layer |

---

## 1. The one idea

Solving a problem is a forward pass through a prerequisite graph. Diagnosing an error is a backward
pass. **Tutoring is blame assignment.**

Most calculus errors are not calculus errors. They are algebra, sign, fraction and trig errors
wearing a calculus costume. A classical system records `quotient_rule: incorrect` and schedules more
quotient rule, which is usually the wrong response. This engine reads the student's working, finds
the failing step, and attributes it to the node actually responsible, which is frequently a
prerequisite already marked mastered.

Everything below exists to make that one move correct and safe.

---

## 2. Shapes

Three static, one dynamic. All in `engine/types.py`.

```
Graph      37 nodes. prereqs (a DAG) and encompasses (what a solution actually exercises).
Item       438 in the bank, 250 gradeable. A stem, an answer, a difficulty, a source.
Attempt    One immutable event. This is the ONLY thing we store as truth.
NodeState  Beta posterior (a, b), stability, last_seen, successes, misconceptions. DERIVED.
```

**`attempts` is an append-only log and `NodeState` is recomputed by replaying it.** Nothing is
mutated in place, ever. This is the single most important architectural decision in the engine, and
the reason is not purity: every constant in section 3 is currently a guess, and replaying real
attempts under new constants is the only honest way to find out whether a change helped. Mutating
stored state destroys that history permanently.

Time is always an explicit argument. Nothing reads the clock. Otherwise nothing is testable and the
seeded demo history cannot exist.

---

## 3. The student model

Mastery is a Beta posterior, so `p = a/(a+b)` with uncertainty carried explicitly. A fresh node is
`a = b = 1`, which is an honest "we do not know" rather than a guessed 0.5.

Memory decays: `R = exp(-days_since_seen / stability)`, and effective mastery is `p_eff = p * R`.
A node is **due** when `R < 0.85`.

Status is always derived, never stored:

| status | rule |
|---|---|
| `locked` | any prerequisite has `p_eff < 0.70` |
| `frontier` | all prerequisites ready, own `p < 0.70`. The only place new learning happens. |
| `learning` | `p >= 0.70`, not yet mastered |
| `mastered` | `p >= 0.90` and at least 3 spaced successes |

### The four update rules

Everything that happens to a student is one of these four. Keeping it to four is deliberate.

**1. Direct evidence.** `a += w·correct`, `b += w·(1-correct)`, where `w` is evidence quality:
photo of full working `1.0`, typed answer `0.7`, MCQ `0.4`, each reduced 25% per hint level.
A photo is the richest signal we get and an MCQ the weakest; they must not move mastery equally.

**2. Implicit credit.** A correct solution credits the component skills it actually exercised, at
25% weight. **It also refreshes `last_seen`.** Without that refresh, a student doing chain rule
problems daily watches the power rule decay as though untouched until it locks the very topic they
are practising. No error, no crash, the frontier just quietly closes. Implicit credit does NOT
advance stability or the success counter: it keeps a node warm, it can never carry one to mastered.

**3. Blame propagation.** The diagnosis names a node, a failing step and a misconception tag.
```
b        += min(1.5 · confidence, 2.0)        cap
stability = max(stability · 0.5, 1 day)       comes back soon
successes = 0                                 a node with a live misconception is not mastered
```
and critically, **if the blamed node is not the attempted node, the negative evidence on the
attempted node is discounted to 25%.** A student is never marked down on a topic they actually
understood. This is the property the whole product rests on, and it is the most heavily tested thing
in the engine.

**4. Consolidation and decay.** A genuine spaced success roughly doubles the interval
(`stability × 1.9`, capped at a year). A failure halves it, floored at a day.

---

## 4. Choosing what to serve

Six problems a day, filled in priority order with a budget that sums to exactly six:

| slot | max | rule |
|---|---|---|
| blocker | 2 | open misconceptions, most frequent first. Always first in the set. |
| review | 2 | due nodes, most overdue first |
| new | 1 (up to 2) | frontier nodes only, goal-path preferred. Expands into unused blocker slots. |
| goal_link | 1 | **reserved**, not filled last |

`goal_link` is reserved because a student who never sees why they are doing this is a student who
quits. The trade is explicit: a day with two blockers is a day you advance less, never a day the
goal disappears.

**`locked` gates new learning only.** Reviews and blockers are selected on evidence of prior
learning, not status. Suppressing a due review because a prerequisite decayed is self-reinforcing
and terminal: the node is not served, so it decays further, so it is never served again, and nothing
reports it. Worse, serving the review is exactly what would have repaired the prerequisite, through
rule 2.

**Difficulty targeting.** Pick the item whose predicted success is nearest 85%, using
`sigmoid(logit(p) - difficulty)`. Note `p`, not `p_eff`: retrievability models **forgetting, not
skill loss**. A student who scored 0.90 six weeks ago is rusty, not a beginner, and spaced
repetition works precisely because the retrieval is effortful but achievable.

**Interleaving.** Reorder to maximise graph distance between adjacent items, blockers excepted.
One sort, large perceived quality gain.

**XP** is `10 × depth × novelty × quality`, where novelty is 3.0 for a genuinely new node (paid
once, not per attempt), 1.0 for a due review and **0.15 for practising something not due**. Grinding
earns almost nothing, and since difficulty is auto-targeted at 85% you cannot farm easy wins either.
That is what would make a leaderboard safe later.

---

## 5. Deciding right and wrong

**A computer algebra system decides correctness. The model is never asked.**

This is not a stylistic preference. Measured: with the model judging, controls scored 4/6 and the
worst observed failure was *inventing an error in a correct answer*. Gating on the CAS deletes that
class entirely. The model is only ever invoked on an answer the CAS has already ruled wrong.

The grader is deliberately generous about form: `x - 5 + x + 2` is accepted for `2x - 3`. Marking a
correct student wrong is the most damaging error this system can make.

**Diagnosis** uses a forced tool call with `blamed_node` constrained to an enum of real node ids,
and each node carries a `blame_hint` that fences it off from its neighbours. Measured: 90% on error
cases, 100% on controls, 3.2s. Before the tool call it was 83%, 4/6, and 23s.

Anything shown on stage is served from cache, because `seed` does not pin model output and one live
run in 23 drifted to a different node.

---

## 6. Numbers, and where they came from

**Measured, trust these:**

| | |
|---|---|
| diagnosis accuracy | 90% error cases, 100% controls, 3.2s |
| Vision transcription | 0.95 similarity, 0.98 math-token recall |
| generated items grading their own answer | 194/194 |
| target reachable by practice | yes, all 37 nodes |
| tests | 817 passing |

**Guesses, tune these first.** Every constant in section 3. None has been calibrated against a real
student. The ones most likely to be wrong, in order:

1. `LAMBDA_BLAME = 1.5` and `BLAME_MAX_B_DELTA = 2.0`. **The cap is currently inert**: the model's
   observed confidence is 0.93 to 0.97, so blame moves `b` by about 1.4 and the cap never engages.
   The 25% discount is doing the protective work the cap was supposed to do.
2. `IMPLICIT_CREDIT_DEFAULT = 0.25` and single-hop propagation. Credit currently stops one level
   down. See section 8.
3. `MASTERED_P = 0.90` with `b = 1` needs about 8 correct photo-quality attempts per skill. That is
   why the seeded week is 244 attempts. It may be too strict.
4. `STABILITY_GROWTH = 0.9`, borrowed from FSRS-shaped reasoning, not fitted.

**Confidence is not usable as a gate.** Measured 0.97 when right, 0.93 to 0.95 when wrong. Not
separable. Any design that thresholds on it is wrong.

---

## 7. What is deliberately not built

Per-student-per-topic learning speed, set-cover repetition compression, the adaptive diagnostic
(the seeded history replaces it), quizzes, knowledge points below the topic level, and multi-course.
All are in the plan or in `core_engine.md`; none is needed to prove the loop.

---

## 8. Known gaps, honestly

- **Implicit credit is single-hop.** A chain rule problem credits function composition and the power
  rule, but nothing those in turn exercise. `core_engine.md` §2.2 specifies transitive propagation
  (multiply along a path, max across paths) and is right that it is more principled. We can settle
  it by replay rather than argument, which is what the event log is for.
- **Six shortcut edges** in the prerequisite graph: direct edges also implied by a longer path. They
  inflate the lock surface. `core_engine.md` makes transitive reduction a storage invariant and we
  should too.
- **Node-level `difficulty_b` is dead data.** Selection reads item difficulty. Three inversions
  exist and nothing will ever tell us they are wrong.
- **Hint prose is English on 34 of 37 nodes.** The three the demo touches are fully Hindi.
- **The blame cap never binds.** See section 6.
- **Committing a blame can lock the node the student was just working on**, because blame cascades
  down. Correct behaviour, possibly bad experience.

---

## 9. Where the code is

```
engine/types.py       shapes and every tunable constant, in one place
engine/mastery.py     the four update rules, status, decay
engine/selection.py   daily set composition, difficulty targeting, interleaving
engine/xp.py          the anti-farming XP formula
engine/replay.py      attempts to state, and per-day frames
services/grading.py   the CAS. Decides correctness.
services/diagnose.py  the model. Explains errors. Never decides correctness.
```
