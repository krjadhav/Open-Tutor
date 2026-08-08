# Applied: adversarial audit of `data/graph/nodes.json`

Source: `docs/audits/graph-audit.md` (31 defects). This file records what was applied, what was
skipped, and the concrete edit for each. Only `data/graph/nodes.json` was modified.

**Headline numbers**

| | before | after |
|---|---|---|
| nodes | 36 | **37** (`alg.vectors` added) |
| prereq edges | 44 | 53 |
| `encompasses` entries | 37 | 42 |
| depth of `ai.gradient-descent-step` | 12 | **8** |
| max depth in the graph | 13 (`ai.backprop-chain`) | **8** (`ai.gradient-descent-step`) |
| ancestors of the target | 19 (20 of 36 on path incl. target) | **26** (27 of 37 on path incl. target) |
| nodes with a `blame_hint` | 12 of 36 | **37 of 37** |
| `alg.sign-distribution` on the goal path | no | **yes** (via `der.definition`, D23) |

The target is now the deepest node in the graph, which is what D8 was for.

---

## Applied

### D1 `opt.critical-points`, missing differentiation prereq
Added prereqs `der.power-rule` 0.9 and `alg.factoring` 0.7. This also clears the single
`encompasses`-outside-closure violation the audit found (`der.power-rule` 0.25 was already there).

### D2 `der.chain-rule`, spurious product-rule prereq
Removed prereq `der.product-rule` (0.6). Added prereq `der.power-rule` 0.9.
Depth of `der.chain-rule` falls 8 to 3.

### D3 new node `alg.vectors`
Added, exactly as specified, inserted next to the other `alg.*` roots:

```
id alg.vectors | title "Vector notation, scalar multiples and the dot product"
kind skill | difficulty_b -0.4 | prereqs [] | encompasses []
```

Added as a prereq of `mv.gradient` (0.8), `mv.directional-derivative` (1.0) and
`ai.gradient-descent-step` (0.8). It carries the `alg.vectors` hint from the audit's block.
It is the only id added; no existing id was renamed or removed.

### D4 `ai.gradient-descent-step`, pseudo-credit
Removed `encompasses` entry `mv.directional-derivative` (0.3). The prereq edge is untouched (D29
skipped).

### D5 blame magnets near the target
Hints added to `mv.directional-derivative`, `opt.local-extrema`, `mv.gradient`, verbatim from the
audit block.

### D6 `der.definition` over-gating, option (b) as arbitrated
Removed the three prereq edges `der.definition -> der.power-rule` (0.6),
`-> der.trig-derivatives` (0.5), `-> der.exp-log-derivatives` (0.5). `der.definition` now gates
only `der.slope-interpretation`. `der.power-rule` moves from depth 5 to depth 2.

### D7 `der.quotient-rule`
Removed prereq `der.product-rule` (0.7). Added prereq `der.power-rule` 0.9.

### D8 `ai.backprop-chain`
Removed prereq `ai.gradient-descent-step` (0.9). Took the audit's optional half and added prereq
`ai.gradient-of-loss` 0.9, which is also required for D28's new `encompasses` entry to stay inside
the transitive prereq closure. `ai.backprop-chain` is now a leaf that depends on nothing the
target depends on downstream of it; verified that the ancestor set and the descendant set of the
target are disjoint.

### D9 `mv.functions-several-vars`
Replaced prereq `der.slope-interpretation` (0.6) with `alg.function-composition` 0.7.

### D10 optimisation to AI link
Added prereq `opt.local-extrema` 0.6 to `ai.gradient-descent-step`. This pulls
`opt.local-extrema`, `opt.critical-points` and `der.higher-order` onto the goal path.

### D11 exp/log on the goal path
Added prereq `der.exp-log-derivatives` 0.5 to `ai.gradient-of-loss`. This pulls
`der.exp-log-derivatives` and `explog.rules` onto the goal path. The audit's companion suggestion,
"make at least one `ai.loss-function` item a log-loss so the edge is real", is an item-bank change
and is out of scope for this file. **Flagged for whoever owns `data/items/items.json`.**

### D12 `alg.solving-equations`
Added prereq `alg.factoring` 0.6. Note this makes `alg.solving-equations` a non-root; see the
engine test note on `test_a_maximally_busy_day_still_carries_the_goal_link` below.

### D13 `der.implicit`
Added prereqs `der.product-rule` 0.8 and `alg.solving-equations` 0.7. Added `encompasses`
`der.product-rule` 0.3 and `alg.solving-equations` 0.25.

### D15 nodes that can never receive implicit credit
Added `encompasses` `mv.functions-several-vars` 0.3 to `mv.partial-derivative`,
`mv.functions-several-vars` 0.3 to `ai.loss-function`, and `der.definition` 0.25 to
`der.slope-interpretation`.

### D16 `der.trig-derivatives`
Removed `encompasses` `trig.unit-circle` (0.25). The prereq edge stays. The node's own
`blame_hint` no longer contradicts its credit list. Its `encompasses` list is now empty; the node
is an off-path leaf, so nothing depends on it for credit.

### D17 `der.product-rule`
Removed `encompasses` `der.constant-multiple-sum` (0.25).

### D18 `mv.partial-derivative` blame hint
Replaced with the sharpened text (exclusion clause naming the rule used, aimed at case c08).
The two nodes the audit wanted as scatter destinations, `der.definition` and
`der.constant-multiple-sum`, both got hints under D31.

### D20 `goal_tags` on every node
Added to all 37 nodes, placed after `difficulty_b`, matching the field order in `db/schema.sql`.
`["gradient-descent"]` on the 26 ancestors of `ai.gradient-descent-step` **and on the target
itself**, `[]` on the other 10. The ancestor set was recomputed from the final edge set, not taken
from the audit's pre-fix list.

Tagging the target itself is a small judgement call: a goal node with no goal tag would be invisible
to any `goal_tags`-driven query, including section 4.4's goal-link slot. Flip it if you disagree,
it is one line.

Off the goal path (10): `ai.backprop-chain`, `alg.radicals`, `der.implicit`, `der.product-rule`,
`der.quotient-rule`, `der.trig-derivatives`, `lim.continuity`, `mv.chain-rule-multivar`,
`trig.identities`, `trig.unit-circle`.

### D22 `der.higher-order`
Added prereq `der.constant-multiple-sum` 0.7.

### D23 `der.definition`
Added prereq `alg.sign-distribution` 0.6 and `encompasses` `alg.sign-distribution` 0.25.
This is what puts `alg.sign-distribution`, the node the demo's headline diagnosis lands on, onto
the goal path.

### D24 `alg.exponent-rules` hint
Replaced with the disambiguated text ("computing a decremented exponent wrongly, such as
-3 - 1 = -4") plus the `der.power-rule` exclusion.

### D25 `alg.fraction-arithmetic` hint
Reworded so the exclusion names a real destination. No arithmetic node was added, as instructed:

> "Combining, subtracting or simplifying rational expressions. A pure numeric slip inside one is
> not this node: blame the topic the problem is tagged to."

### D26 `trig.identities`
The audit offered "remove, or drop credit to 0.2". **Took the 0.2 option**, so the entry survives
as the weakest credit in the graph rather than leaving a second node with an empty `encompasses`.
Unlike D16 there is no contradiction with the node's own hint here, only an over-claim.

### D27 `der.exp-log-derivatives`
The audit offered "keep at 0.2 for composite-argument items only, or remove". **Set the credit to
0.2** and kept the entry. The "composite-argument items only" half is an item-level restriction the
node schema cannot express, so it is not implemented. **Flagged for the item bank owner.**

### D28 `encompasses` omissions
Added `der.chain-rule` 0.3 to `ai.gradient-of-loss` and `ai.gradient-of-loss` 0.35 to
`ai.backprop-chain`. Both stay inside the transitive prereq closure, the second only because of
the D8 optional prereq.

### D31 remaining `blame_hint`s
All 24 new hints from the ready-to-apply block were applied verbatim, plus the `alg.vectors` hint,
plus the 3 revisions (D18, D24, D25). Coverage is now 37 of 37 nodes.

---

## Skipped

| # | Why |
|---|---|
| D14 | Resolved by D6 as arbitrated. Inversions fell from 5 to 3, and the three that remain (`der.slope-interpretation` -0.4, `alg.solving-equations` -0.2, `der.constant-multiple-sum` -0.1) are not the `der.definition` cluster the defect was about. No `difficulty_b` value was changed. |
| D19 | **Not applied. `difficulty_b` was left in place.** See the verification below. |
| D21 | A consequence, not a defect. The applied fixes moved it from 16 off-path nodes of 36 to 10 of 37 without any node being cut. |
| D29 | `mv.directional-derivative` remains a prereq of the target at weight 0.8. |
| D30 | `der.quotient-rule`'s 0.90 total implicit credit is unchanged and deliberate. |

### D19 in detail: why `difficulty_b` stayed

The instruction was to delete node-level `difficulty_b` **only if** a repo-wide grep confirms every
read is item-level. It does not. The grep splits three ways:

**Item-level reads, as the audit says (these confirm the audit's core claim):**
- `engine/selection.py:93` builds `Item.difficulty_b` from `data/items/items.json`;
  `engine/selection.py:136` `predicted_success` uses `item.difficulty_b`
- `services/grading.py:86` likewise, `services/diagnose.py:69` defaults it per item
- `engine/types.py:135` is the field on `Item`

**Node-level reads that do exist:**
- `scripts/audit/graph/structure.py:50` requires `difficulty_b` as a mandatory node field, and
  lines 185, 186 and 194 read `node["difficulty_b"]` directly. Deleting the field makes the audit's
  own structural checker raise `KeyError`, which collides head-on with the constraint that
  `structure.py` must come back clean. I do not own that file and may not edit it.
- `scripts/audit/graph/impact.py:100, 119, 125, 126, 129, 131` reads it the same way.
- `db/schema.sql:35` declares `nodes.difficulty_b double precision **not null**`, with a comment
  at line 46 asserting it is used for prediction. The comment is wrong (the formula it quotes uses
  `b_item`), but the column is a not-null node-level field in the persistence schema.

**Neither:** `engine/tests/test_replay.py:53-77` and `engine/tests/test_mastery.py` carry
`difficulty_b` in inline fixture graphs, but no assertion reads it. `Graph` in `engine/types.py`
exposes only `prereqs`, `encompasses` and `title`, so the engine genuinely cannot see it.

So the audit's finding is correct about the **engine**, and the instruction's fallback branch
applies: something does read it, so it was left, and this is that report. The cheap follow-up, if
you still want it gone, is one arbitration on `structure.py`, `impact.py` and `db/schema.sql`
together; the graph file is ready for it either way.

---

## Verification

### 1. DAG
Clean, checked two independent ways rather than by eye: Kahn's algorithm topologically orders
37 of 37 nodes, and a separate DFS three-colouring pass finds 0 back edges. `structure.py` agrees
(`ACYCLIC`).

### 2. Dangling ids
None, in `prereqs`, in `encompasses`, or in `target_node`. No self loops. No duplicate ids.
All 53 prereq weights inside 0 to 1, all 42 credits inside 0.2 to 0.4.

### 3. No id renamed or deleted
Diffed the id set against `git show HEAD:data/graph/nodes.json`: zero removals, one addition
(`alg.vectors`). Cross-checked against the item bank: all 376 items in `data/items/items.json`
still resolve to a live node id, 0 orphans.

### 4. Target integrity
`target_node` is still `ai.gradient-descent-step`. Its ancestor set (26) and its descendant set
(empty, it is now a leaf) are disjoint, and it is not its own ancestor. After D8,
`ai.backprop-chain` no longer depends on the target; it now hangs off `ai.gradient-of-loss` and
`mv.chain-rule-multivar`.

### 5. `python3 scripts/audit/graph/structure.py`
Clean except for **one** complaint, which is new and which I did not silently fix:

```
[encompasses entries NOT in the transitive prereq closure]
  mv.partial-derivative -> der.constant-multiple-sum (credit 0.25)
```

This entry was legal before because `mv.partial-derivative -> der.chain-rule -> der.product-rule
-> der.constant-multiple-sum` was a chain. D2 removed the middle link, so the credit target is no
longer an ancestor. It is not a bad credit entry on its face: differentiating `x^2 + 3xy` with
respect to `x` really does apply the sum and constant-multiple rules. **Left as-is for
arbitration**, because both candidate fixes go beyond what was arbitrated:

- (a) add prereq `mv.partial-derivative -> der.constant-multiple-sum`, an edge no defect asked for;
- (b) delete the `encompasses` entry, which is a D17-style removal no defect asked for.

I lean (a): the same check is what surfaced D1, and there it correctly indicated a missing prereq
rather than a bad credit.

Everything else in `structure.py` is informational output, not a complaint, but three lines are
worth reading:
- `[nodes with empty encompasses]` now includes `alg.solving-equations` (still receives credit from
  `opt.critical-points` and `der.implicit`, so D15's failure mode does not apply) and
  `der.trig-derivatives` (emptied by D16, off-path leaf).
- `[difficulty inversions]` 5 to 3, per D14 above.
- `[leaves]` is now 8 and includes the target, which is correct.

### 6. `python3 -m pytest engine/ -q`

Baseline before any edit: **136 passed**.

**Caveat, please read before arbitrating.** `engine/tests/test_selection.py` and
`engine/tests/test_xp.py` were being rewritten by another process throughout this work (mtimes
23:57, 23:59, 00:01, all after the graph write at 23:57:38). I did not edit any test file. The
failure set moved three times underneath me. The table below is the graph-attributable failure set,
taken from the first clean run after the graph write; the "current run" note under it records what
the suite reports now, including two failures that are nothing to do with the graph.

**Graph-attributable result: 2 failed, 123 passed, 12 errors.**

| Test | Diagnosis |
|---|---|
| `test_mastery.py` 12 errors, all at fixture setup: `test_node_with_no_prereqs_is_never_locked`, `test_real_graph_cold_start_locks_everything_downstream`, `test_real_graph_unlocks_a_node_once_its_prereqs_are_ready`, `test_implicit_credit_through_apply_attempt_on_the_real_graph`, `test_incorrect_attempt_gives_no_implicit_credit`, `test_practising_a_topic_never_locks_it_behind_a_prereq_it_exercises`, `test_implicit_credit_alone_can_never_reach_mastered`, `test_blame_on_prereq_spares_the_attempted_topic`, `test_blame_on_the_attempted_node_itself_is_not_discounted`, `test_apply_attempt_never_mutates_its_input`, `test_replay_is_deterministic`, `test_replay_can_carry_a_node_from_locked_to_mastered` | **The test is wrong.** One line in the `real_graph` fixture, `assert len(graph.nodes) == 36, "the demo slice is expected to have 36 nodes"`. D3 was arbitrated in, so 37 is correct. This is a hardcoded count in a fixture, not a behavioural assertion; every one of the 12 dies before its own body runs. Fix: 37, or drop the assert. |
| `test_selection.py::test_goal_ancestors_covers_the_path_and_excludes_unrelated_nodes` | **The new graph is right.** The failing line is `assert "opt.local-extrema" not in ancestors`. D10 deliberately makes `opt.local-extrema` a prereq of the target, so it is now an ancestor by construction. The neighbouring assertion `assert "der.implicit" not in ancestors` still passes and still holds. Fix: delete that one line, or repoint it at a node that is genuinely off-path such as `der.quotient-rule`. |
| `test_selection.py::test_a_maximally_busy_day_still_carries_the_goal_link` | **Needs your judgement, and it is the one interesting failure.** The test's stated purpose still holds: the set is full at 6, both blockers are present and first, and the reserved `goal_link` slot survives. What broke is `slots.count("review") == 2`; the set came back `[blocker, blocker, review, new, goal_link, new]`. Cause: the `busy_day_states` fixture ages `alg.factoring` by 45 days (`p_eff` 0.011) and `alg.solving-equations` by 30 days, and D12 just made `alg.factoring` a prereq of `alg.solving-equations`. So `alg.solving-equations` is now **locked** rather than due, its review disappears, and a `new` takes the place. The graph edge is right (you cannot solve a quadratic you cannot factor), and the engine behaved exactly as `mastery.status()` documents. But it surfaces a real design question that is not mine to settle: a stale root now suppresses a dependant's due review instead of scheduling it, so the student is offered new material while a decayed prereq quietly locks work they had already mastered. If that is intended, update the fixture (age only `alg.factoring`, or add a third stale node). If it is not, the fix belongs in `selection.py`, not in the graph. |

**Current run (3 failed, 123 passed, 12 errors).** The 12 `test_mastery.py` errors are unchanged
and are the `== 36` fixture assert above. `test_goal_ancestors_covers_the_path_and_excludes_unrelated_nodes`
has since been fixed by the other process and now passes. The three current
`test_selection.py` failures, `test_a_maximally_busy_day_still_carries_the_goal_link`,
`test_the_goal_link_slot_is_reserved_not_merely_last_in_a_shorter_set` and
`test_the_goal_link_slot_survives_an_oversubscribed_budget`, all raise
`NameError: name 'busy_day_states' is not defined`: that helper was renamed to `busy_day` at line
389 and three call sites at lines 428, 448 and 460 have not been updated yet. That is a
half-finished rename in a file I do not own, not a graph regression, and it currently masks the
real `slots.count("review") == 2` failure analysed in the table above. Re-run once that rename
lands.

`test_xp.py` deserves a note even though it currently passes: at the time of the graph write it had
19 failures, all traceable to a hardcoded `DEEP_NODE_DEPTH = 12` and to `der.chain-rule` moving
from depth 8 to depth 3. It has since been changed to compute the depth from the graph. One of
those failures was not a stale constant but a genuine fragility worth keeping in mind:
`test_honest_review_beats_grinding_but_loses_to_new_work` asserted
`review / grind == NOVELTY_REVIEW / NOVELTY_OVERPRACTICE` (6.667) on `der.chain-rule`, and at depth
3 the rounding inside `xp_for` (`round(10 * 3 * 0.15) = 4`, not 4.5) makes the true ratio 7.5. The
smaller depths this graph produces put that assertion inside the rounding noise for any low-depth
node. That is an XP-rounding property, not a graph defect.
