# Open Tutor: MVP plan

> **Historical.** This was the plan made before building. For what the engine
> actually does today, read [engine.md](engine.md). Sections on scope and the
> demo script are still current; section 6 (engine subset) has been superseded.

Companion to `learning-design.md`. That document is why; this one is what to build on Sunday.
All three pillars are measured and hold (§14, §15, §16). Nothing here is speculative.

---

## 1. The one sentence

**Open Tutor finds the one thing actually blocking you and fixes it.**

Not "an AI tutor". Not "personalised learning". If a judge remembers one thing, it should be the
moment the app says *"Your chain rule is fine. You're dropping the negative when you distribute
across -(3x-2). That's cost you four problems this week."*

Everything below is scaffolding around producing that sentence.

---

## 2. Scope guardrails

**In:** one goal slice (36 nodes, calculus to gradient descent), one student, six problems a day,
photo in, blame propagation out, Hindi toggle, a graph that visibly changes.

**Out:** multi-course, accounts and teams, live graph compilation, leaderboards, social, streaks,
notifications, payments, teacher dashboards, anything with the word "analytics".

**The test for any feature:** does it appear in the 90-second demo? If not, it is P2.

---

## 3. Screens

Seven screens. Each has exactly one job.

| # | Screen | Job | Priority |
|---|---|---|---|
| 1 | **Goal** | Say what you want to understand. Sets the whole frame. | P0 |
| 2 | **The Path** | The graph as game board. Where you are, where you're going. | P0 |
| 3 | **Today's Set** | Six problems, why each one is there. | P0 |
| 4 | **Solve** | One problem, hints, answer box, camera. | P0 |
| 5 | **Check** | "Here's what we read. Is this right?" | P0 |
| 6 | **Result** | Verdict instantly, diagnosis 3s later, blame made visible. | P0 |
| 7 | **Session complete** | XP, nodes lit, what unlocked, tomorrow's focus. | P0 |

Screen 5 is the one people will want to cut. Do not cut it. Vision inserted a spurious `^-` in
one of four real samples (§15), so it is the error boundary, and it is also the screen that makes
the system feel honest rather than magical.

---

## 4. Features

### P0, demo-critical

- **Goal to slice.** Hardcode one goal for the demo. The natural-language mapping is a nice-to-have.
- **The Path** with four node states: mastered, learning, edge (frontier), locked. Target node pinned at the top. Percentage complete and estimated days at current pace.
- **Daily set composition.** 6 items using the §4.4 budget: blockers first, then reviews, then edge, then one goal-link problem. Store the reason each item was chosen and show it.
- **Interleaving.** Never two adjacent items from the same graph neighbourhood. One sort, large perceived quality gain.
- **Typed answer + CAS check.** sympy symbolic equivalence. This is the grader. The model never decides correctness.
- **Photo path.** Upload, Sarvam Vision `content_type="handwritten"`, transcription shown for confirmation, then diagnosis.
- **Diagnosis via forced tool call.** With `blamed_node` enum-constrained and `blame_hint` rendered into the node list. 3.2s, so it can land inline (§16).
- **Blame propagation, visibly.** The blamed node dims and reopens on The Path, with an animation. This is the demo.
- **Blockers list.** Open misconceptions as named, human-readable items: "Dropping negatives across brackets, 4 times this week."
- **Session complete.** XP earned, nodes lit, what unlocked, one line naming tomorrow's focus.
- **Seeded 5-day history.** Non-negotiable. A day-1 empty graph demos terribly.
- **Cached demo diagnoses.** `seed` does not pin output (§16). Pre-compute the responses for the demo problems and serve from cache with the live path as fallback.

### P1, ship if time allows

- **Hint ladder**, 4 rungs, ending in reveal plus an isomorphic twin problem. Hints cost evidence weight, never XP, never blocked.
- **Hindi toggle.** Pre-translated and cached in the `translations` table. Template the maths out as `{{m1}}` before translating (§13).
- **Teach ladder** for a new edge node: worked example, faded example, independent problem.
- **Contest the blame.** "Was this actually your mistake? yes / no". One tap. Better UX and a data flywheel.

### P2, only if everything else is done

- Bulbul TTS for accessibility.
- Streaks (forgiving, one free skip a week).
- Natural-language goal to target-node mapping.

---

## 5. Gamification

### The frame: training, not a game show

The research note in `mathacademy_research.md` says it well: effective learning should feel like a
workout with a trainer. That is the aesthetic. Adults who want to understand backpropagation are
not motivated by cartoon owls, and the moment this looks like Duolingo it reads as a toy.

Use: sets, sessions, edge, blockers, progress, unlocked. Avoid: lives, hearts, gems, streaks that
punish, mascots, confetti for trivial actions.

### XP that cannot be farmed

```
XP = depth(node) x novelty x quality

novelty   new edge node 3.0 | due review 1.0 | not-due repractice 0.15
quality   hint-free 1.0 | hinted 0.6 | reveal + twin correct 0.4
```

Grinding easy problems earns almost nothing, and since difficulty is auto-targeted at 85% success
you cannot farm easy wins either. This is what makes a leaderboard safe later.

### The board is the graph

Do not bolt points onto a chapter list. The Path *is* the game board. The reward moment is a node
turning gold **and the frontier visibly expanding**, with "this unlocked 3 new topics". That is a
better hit than a number going up, and it is honest, because it is literally the state of the
student's knowledge.

### The five emotional beats

Design each screen around exactly one of these:

1. **Aspiration.** Name your goal, see the whole path light up. (Goal, The Path)
2. **Recognition.** "You're dropping negatives across brackets." Being *seen*. (Result)
3. **Progress.** A node turns gold. (Result, Session complete)
4. **Momentum.** "3 topics unlocked." (Session complete)
5. **Destination.** "18 days to gradient descent at your pace." (The Path, Session complete)

Beat 2 is the product. Give it the most screen real estate and the most design attention.

### One number for the student

Percentage of the goal slice mastered, plus estimated days remaining at current pace. "34% of the
way to understanding gradient descent, about 18 days" retains far better than any badge.

---

## 6. Engine: what to actually implement

The full model in §4 is more than a weekend needs. Build this subset:

**Keep**
- Beta posterior `(a, b)` per node, `p = a/(a+b)`. Cheap and gives honest uncertainty.
- Retrievability `R = exp(-Δt/S)`, `p_eff = p·R`, due when `R < 0.85`.
- Derived status: locked / edge / learning / mastered.
- Update rules 1 (direct), 2 (implicit credit), 3 (blame), 4 (decay).
- Daily set composition budget and interleaving.
- Difficulty targeting: pick the item whose predicted success is nearest 0.85.

**Cut for the MVP**
- Per-student-per-topic `speed` multiplier. Tuning surface, no demo value.
- Set-cover repetition compression. Replace with "pick the two most-overdue reviews".
- The adaptive diagnostic. The seeded history replaces it.

**Non-negotiable from the experiments**
- CAS decides correctness; the model is invoked only on confirmed-wrong answers (§14 finding 2).
- Cap the magnitude of any single blame update. Confidence is not a usable gate (§14 finding 3).
- Every new node needs a `blame_hint` if its title could describe someone else's error (§16).

---

## 7. Data model

Per §12. The one thing that matters: **`attempts` is an append-only event log and `node_state` is
derived from it.** You will retune the blame weight and decay constants repeatedly, and replay
against real attempts is the only way to tell whether a change helped.

```
students(id, goal_node, lang, created_at)
nodes(id, title, blame_hint, kind, difficulty_b, goal_tags)
edges(from, to, kind: prereq|encompass, weight)
items(id, node_id, stem_latex, answer, difficulty_b, source, encompasses[])
attempts(id, student_id, item_id, node_id, answer_given, correct, hint_level,
         photo_url, ocr_text, diagnosis_json, latency_ms, created_at)   -- append only
node_state(student_id, node_id, a, b, S, last_seen, misconceptions[])   -- derived
daily_sets(id, student_id, date, item_ids[], rationale_json)
translations(entity_type, entity_id, lang, text)
```

`items.source` (`openstax` | `generated` | `original`) from the first migration, so commercialising
means deleting rows and regenerating rather than re-architecting.

---

## 8. Build sequence

Ordered so that at every checkpoint you have something demoable.

1. **Seed data first.** Graph into the DB, ~60 OpenStax items tagged to nodes, the 5-day fake history. Everything downstream needs this and it is unglamorous, so do it while fresh.
2. **Engine as a pure module.** State, four update rules, set composition. Unit-testable with no UI, no API. Prove the graph changes correctly against a scripted sequence of attempts.
3. **Solve to Result, typed answers only.** CAS check. This is the whole loop working end to end without any AI.
4. **The Path.** Now the engine's output becomes visible, and demo quality starts to be judgeable.
5. **Photo path.** Vision, Check screen, diagnosis tool call, blame propagation animation.
6. **Session complete and XP.**
7. **P1 features** in the order listed.
8. **Freeze, then rehearse the demo six times.** Cache the diagnoses. Find what breaks.

Step 8 is not padding. Something will break on stage; the rehearsals are what let you route around
it calmly.

---

## 9. The demo, 90 seconds

1. "I want to understand how neural networks learn." The Path appears, gradient descent pinned at the top, 34% lit, "about 18 days".
2. Today's Set. Each problem shows why it is there. Tap problem 3.
3. Solve on paper, photograph it. Check screen: "here's what we read", confirm.
4. Verdict instantly: incorrect. Then the diagnosis: **"Your chain rule is correct. You dropped the negative when you distributed across -(3x-2). Same mistake as Tuesday."**
5. The Path animates: chain rule stays gold, the algebra node dims and reopens, Blockers gains an entry, tomorrow's set rebuilds with a sign-distribution drill at the top.
6. Hindi toggle. Same problem, same hint, notation unchanged.

Step 4 is the product. Rehearse it until it is exact.

---

## 10. Known risks on the day

| Risk | Response |
|---|---|
| Live Vision call fails or is slow on venue wifi | Cache the demo photo's transcription. Have the live path as a visible fallback, not the primary. |
| Diagnosis returns a different node than rehearsed | Diagnoses for demo problems are pre-computed and cached. `seed` does not pin output (§16). |
| Someone asks "how do you know the AI isn't hallucinating the grade" | "It doesn't grade. A computer algebra system does. The model only explains why you were wrong." Then show the CAS check. |
| Someone asks about the graph not scaling | The compiler slide, plus the honest answer that it needs a human review pass. |
| c08-style wrong routing appears live | The message will still be right. Read it out. The teaching output is more reliable than the routing (§16). |
