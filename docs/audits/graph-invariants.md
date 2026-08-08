# Graph invariants: what we measured, what we changed, what we left alone

Scope: `data/graph/nodes.json`, the six shortcut prerequisite edges, the three `difficulty_b`
inversions, and the new executable checklist `engine/graph_validate.py`.

The headline: **the six shortcut edges are load bearing in our engine and were kept.**
`core_engine.md` section 2.1 makes transitive reduction a storage axiom on the argument that a
shortcut can never change readiness. That argument is sound for the model it belongs to, and
unsound for ours. We measured rather than assumed, and the measurement disagrees with the axiom
six times out of six.

---

## 1. Why the axiom does not transfer

`core_engine.md` proves the shortcut harmless from two premises:

- **binary mastery** (section 3): a topic is mastered or it is not, and
- **ancestral closure** (section 3, "the mastered set is ancestrally closed at all times").

Under those, if the longer path `v <- w <- u` is satisfied then `w` is mastered, and closure means
every ancestor of `w` including `u` is mastered too. The direct edge `u -> v` therefore adds no
information and can be dropped.

We have neither premise. `engine/mastery.py` `status` reads:

```python
for prereq_id, _weight in graph.prereqs(node_id):
    if p_eff(get_state(states, prereq_id), now) < PREREQ_READY:
        return STATUS_LOCKED
```

Three properties of that loop matter:

1. It walks `graph.prereqs`, the **direct** prerequisites. It never recurses.
2. It compares against `p_eff`, a **continuous** value that decays as `p * exp(-days / stability)`.
3. Nothing anywhere else rechecks a deeper ancestor.

So mastery here is not ancestrally closed and cannot be: each node's `p` and `stability` move
independently, under its own attempts, its own blame and its own clock. `p_eff(w) >= 0.70` implies
nothing at all about `p_eff(u)`. A shortcut edge is the only mechanism by which `v` ever looks at
`u` again.

---

## 2. What was measured

All measurements replay the real log through the real engine. Nothing below is hand-built state
except scenario C, which is labelled as such.

| | scenario |
|---|---|
| **A** | Replay `data/demo/history.json` (244 attempts), compare `status` for all 37 nodes with and without the edge, at `anchor_today`. |
| **B** | Same replayed state, advance `now` day by day for 365 days of pure decay, diff at every step. |
| **C** | Synthetic: every node `p = 0.95` and fresh, then only the shortcut's source backdated to a range of `p_eff` values. |
| **D** | The realistic one. Continue the demo log with 30 days of correct photo attempts on `v`'s **other** prerequisites, with `used_nodes` populated from each node's `encompasses` list so implicit credit really flows. The source `u` is never practised. Replay the whole extended log. |
| **E** | As D, plus ten blamed failures on `u`, so `u` is weak in **`p`**, not merely decayed. This is the case decay-refresh cannot rescue, because `p_eff = p * R` and `R` is already 1. |

A and B are the honest null results and are reported as such: **on the shipped demo history, no
edge changes any status, now or under 365 days of decay.** The reason is not that the edges are
redundant. It is that in the current demo state `v` is already locked by a *different* prerequisite
that is at or below the shortcut source, so the shortcut has nothing left to gate. Concretely:

| edge | `p_eff(u)` at anchor | min `p_eff` of the other prereqs | window where the edge could bite |
|---|---|---|---|
| `alg.fraction-arithmetic -> der.definition` | 0.763 | 0.761 | none in 365 days |
| `der.power-rule -> der.higher-order` | 0.871 | 0.431 | none |
| `der.chain-rule -> mv.chain-rule-multivar` | 0.148 | 0.500 | none |
| `alg.vectors -> mv.directional-derivative` | 0.367 | 0.500 | none |
| `alg.factoring -> opt.critical-points` | 0.857 | 0.368 | none |
| `alg.vectors -> ai.gradient-descent-step` | 0.367 | 0.500 | none |

A null result on one 5-day seeded history is not evidence of redundancy. It is evidence that this
particular history never enters the region where the edges do work. C, D and E enter it.

---

## 3. Per-edge result and decision

`w` below is the intermediate that carries the longer path. "refreshes `u`?" asks whether `w`
lists `u` in its `encompasses`, since `apply_implicit` advances `last_seen` on credited nodes and
would therefore keep `u` warm without the student ever practising it directly.

### 3.1 `alg.fraction-arithmetic -> der.definition`

- longer path: `der.definition <- lim.indeterminate-factoring <- alg.fraction-arithmetic`
- `lim.indeterminate-factoring` encompasses `alg.factoring`, `lim.direct-substitution`. Not `u`. **No refresh.**
- A: no change. B: no change.
- C: identical down to `p_eff(u) = 0.72`; at 0.69 and below, `der.definition` goes `locked -> mastered` when the edge is dropped.
- D: `p_eff(u) = 0.007`, others at 0.839. `der.definition` `locked -> learning`.
- E: `p(u) = 0.40`. `der.definition` `locked -> learning`.

**KEEP.** Nothing on the remaining path ever looks at fraction arithmetic again, and the difference
quotient is exactly where a decayed fraction skill resurfaces.

### 3.2 `der.power-rule -> der.higher-order`

- longer path: `der.higher-order <- der.constant-multiple-sum <- der.power-rule`
- `der.constant-multiple-sum` encompasses `der.power-rule` at credit 0.3. **This one does refresh `u`.**
- A: no change. B: no change.
- C: differs from `p_eff(u) = 0.69` down.
- D: **no status difference.** Implicit credit held `p_eff(der.power-rule)` at 0.840 across the whole 30 days. This is the one edge that survived scenario D.
- E: `p(u) = 0.527`, `p_eff(u) = 0.202`, others at 0.823. `der.higher-order` `locked -> frontier`.

**KEEP.** This is the interesting case and the one that nearly went the other way. Implicit credit
protects it against *forgetting* but not against *being wrong*: rule 2 only ever adds to `a`, while
blame adds to `b`, so a student who carries a live power-rule misconception while answering sum-rule
items correctly drives `p` down with nothing pushing back. Without the edge, second derivatives
unlock over a power rule the engine has already been told is broken. The narrower reading, that the
edge is redundant under decay alone, is true and is not enough.

### 3.3 `der.chain-rule -> mv.chain-rule-multivar`

- longer path: `mv.chain-rule-multivar <- mv.partial-derivative <- der.chain-rule`
- `mv.partial-derivative` encompasses `der.power-rule`, `der.constant-multiple-sum`, `mv.functions-several-vars`. Not `u`. **No refresh.**
- A, B: no change. C: differs from 0.69 down.
- D: `p_eff(u) = 0.000`, others 0.842. `mv.chain-rule-multivar` `locked -> frontier`.
- E: same, `p(u) = 0.166`.

**KEEP.** The multivariable chain rule is single-variable chain rule applied once per path. Serving
it over a dead `der.chain-rule` is the exact failure the gate exists to prevent.

### 3.4 `alg.vectors -> mv.directional-derivative`

- longer path: `mv.directional-derivative <- mv.gradient <- alg.vectors`
- `mv.gradient` encompasses `mv.partial-derivative` only. **No refresh.**
- A, B: no change. C: differs from 0.69 down.
- D: `p_eff(u) = 0.000`, others 0.842. `locked -> frontier`.
- E: same, `p(u) = 0.155`.

**KEEP.** The directional derivative *is* a dot product with a normalised direction. `alg.vectors`
was added by the earlier graph audit (D3) precisely because vector mechanics had nowhere to land;
dropping the direct edge re-opens the hole from the other side.

### 3.5 `alg.factoring -> opt.critical-points`

- longer paths: via `alg.solving-equations`, and via `der.slope-interpretation <- der.definition <- lim.indeterminate-factoring`
- `alg.solving-equations` encompasses nothing; `der.slope-interpretation` encompasses `der.definition`; `der.power-rule` encompasses `alg.exponent-rules`. **No refresh.** Note that `lim.indeterminate-factoring` *does* encompass `alg.factoring`, but implicit credit in this engine is single-hop (engine.md section 8), so that never reaches `u` from anything on `opt.critical-points`.
- A, B: no change. C: differs from 0.69 down.
- D: `p_eff(u) = 0.009`, others 0.801. `locked -> frontier`.
- E: same, `p(u) = 0.391`.

**KEEP.** Finding critical points means solving `f'(x) = 0`, which for the polynomial items in the
bank means factoring. The longest path to `alg.factoring` runs four hops through the limit chain
and is the least likely to stay warm.

### 3.6 `alg.vectors -> ai.gradient-descent-step`

- longer paths: via `ai.gradient-of-loss <- mv.gradient`, and via `mv.directional-derivative` (both directly and through `mv.gradient`)
- none of `ai.gradient-of-loss`, `mv.directional-derivative`, `opt.local-extrema` encompasses `alg.vectors`. **No refresh.**
- A, B: no change. C: differs from 0.69 down.
- D: `p_eff(u) = 0.000`, others 0.842. `locked -> frontier`.
- E: same, `p(u) = 0.155`.

**KEEP.** The target is `w := w - alpha * grad L`, a scalar times a vector, subtracted from a
vector. It is the one node in the graph where vector mechanics are the whole answer, and it is the
node we most need to be right about.

### Summary

| edge | A demo | B decay 365d | C synthetic | D practice-the-path | E blamed ancestor | decision |
|---|---|---|---|---|---|---|
| `alg.fraction-arithmetic -> der.definition` | same | same | **differs** | **differs** | **differs** | KEEP |
| `der.power-rule -> der.higher-order` | same | same | **differs** | same | **differs** | KEEP |
| `der.chain-rule -> mv.chain-rule-multivar` | same | same | **differs** | **differs** | **differs** | KEEP |
| `alg.vectors -> mv.directional-derivative` | same | same | **differs** | **differs** | **differs** | KEEP |
| `alg.factoring -> opt.critical-points` | same | same | **differs** | **differs** | **differs** | KEEP |
| `alg.vectors -> ai.gradient-descent-step` | same | same | **differs** | **differs** | **differs** | KEEP |

Zero edges removed. The graph is unchanged structurally: still a DAG, still 37 nodes, no dangling
ids, no orphaned items. Removing an edge would also have shifted XP, since `engine/xp.py`
`node_depth` reads the prereq graph, though in fact no depth changed for any of the six.

---

## 4. Where we differ from `core_engine.md`, and what would close the gap

We reject **section 2.1's reduced-storage axiom** and only that. Acyclicity, the second DAG, weight
ranges and the ancestry idea behind key prerequisites we keep, and `engine/graph_validate.py`
enforces them.

The disagreement is not that the axiom is wrong. It is that it is a theorem about a different
student model, quoted as an invariant about ours. Its proof needs ancestral closure; our `status`
is a one-hop check against a continuous decaying value, so closure fails and the theorem's
conclusion goes with it.

There is a principled way to earn the axiom, and it is worth naming because it is the better
long-term fix: **make readiness recursive**. If `status` locked a node when any *ancestor* fell
below `PREREQ_READY`, rather than any direct prerequisite, mastery would be ancestrally closed by
construction, all six shortcuts would become genuinely redundant, and the reduction would be safe
and correct. That is a change to `engine/mastery.py`, which this audit does not own, and it is not
free: it makes lock a whole-ancestry query per node per render, and it makes the lock surface much
larger and much more sensitive to one decayed root. Until someone measures that trade, six explicit
edges are the cheaper and more honest encoding of the same intent.

Until then, treat the shortcut edges as load-bearing data. `engine/graph_validate.py` reports all
six on every run at `info` severity so they stay visible, and
`engine/tests/test_graph_validate.py::TestShippedGraph::test_reports_exactly_the_six_shortcut_edges`
fails if any is removed.

---

## 5. `difficulty_b`: fixed, not deleted

Node-level `difficulty_b` was dead data. `engine/selection.py` targets **item** difficulty
(`Item.difficulty_b`); `Graph` in `engine/types.py` exposes only `prereqs`, `encompasses` and
`title`, so the engine cannot read the node field even in principle.

**Decision: keep the field, correct the three inversions, and make the validator the thing that
reads it.**

Deleting it was the other option and was rejected on the grep, which is the same conclusion the
earlier audit reached at D19 and for the same reasons. Non-engine readers exist:

- `scripts/audit/graph/structure.py:50` lists `difficulty_b` as a mandatory node field and reads it
  at lines 185, 186 and 194. Deleting the field turns that script into a `KeyError`.
- `scripts/audit/graph/impact.py` reads it at lines 100, 119, 125, 126, 129 and 131.
- `db/schema.sql:35` declares `nodes.difficulty_b double precision not null`.

None of those three files is ours to edit, so deletion would mean knowingly breaking two scripts
and contradicting the persistence schema.

That leaves the actual complaint, which was never really "this field is unused" but "nobody
validates it, which is how it came to be wrong". So it is validated now.
`_check_difficulty_monotonicity` requires every node to be rated **strictly** above every one of
its prerequisites, at `error` severity. Equality counts as an inversion: a node that adds a step is
harder than the step it adds to.

The three corrections, each the smallest edit that restores the ordering without creating a new
inversion downstream:

| node | prereq | was | now | why this side of the pair |
|---|---|---|---|---|
| `alg.solving-equations` | `alg.factoring` (-0.8) | -1.0 | **-0.7** | Solving a quadratic *is* factoring plus the zero-product step, so it composes its prerequisite and must sit above it. Raising it leaves the root floor (`alg.sign-distribution` at -1.2 down to `alg.factoring` at -0.8) untouched; lowering `alg.factoring` would have pushed a root below the floor for no gain. Dependants `opt.critical-points` (0.7) and `der.implicit` (0.9) stay clear. |
| `der.slope-interpretation` | `der.definition` (0.5) | 0.1 | **0.6** | The authored edge says the tangent-slope reading depends on the difference quotient, so it inherits the whole limit chain and must be rated above it. Lowering `der.definition` instead was not available: its own prerequisite `lim.indeterminate-factoring` sits at 0.2, so it cannot drop below 0.1 without dragging the limit chain with it, and that chain is the D6 arbitration nobody wants to reopen. 0.6 puts it alongside `der.chain-rule`, under `opt.critical-points` (0.7). |
| `der.constant-multiple-sum` | `der.power-rule` (-0.2) | -0.3 | **-0.1** | The sum and constant-multiple rules are applied *on top of* the power rule in every item in the bank. Dependants `der.product-rule` (0.3), `der.higher-order` (0.3) and `mv.partial-derivative` (0.8) stay clear. |

After the edits `difficulty_b` is strictly increasing along all 54 prerequisite edges, and
`scripts/audit/graph/structure.py`'s own inversion report drops from three to zero.

---

## 6. `engine/graph_validate.py`

Runnable as `python3 -m engine.graph_validate` (exit 1 on any error), importable as
`validate(graph, items_by_node=None) -> list[Problem]`. `validate_file(...)` adds the duplicate-id
check, which needs the raw JSON because `load_graph` keys by id and would already have raised.

Three severities, and only `error` affects the exit code:

| severity | meaning |
|---|---|
| `error` | the graph is wrong and something downstream will misbehave |
| `warning` | worth a look, but a deliberate state of the graph today |
| `info` | recorded so it cannot be forgotten. The six shortcuts live here. |

Checks, with the reason each is not merely tidiness:

| code | severity | why |
|---|---|---|
| `DUPLICATE_ID`, `SELF_LOOP`, `DANGLING_PREREQ`, `DANGLING_ENCOMPASS` | error | referential integrity |
| `PREREQ_CYCLE`, `ENCOMPASS_CYCLE` | error | `core_engine.md` 2.1 and 2.2. Detection is iterative, not recursive, because it runs *before* acyclicity is known. |
| `ENCOMPASS_OUTSIDE_CLOSURE` | error | `apply_implicit` grants credit and refreshes `last_seen` on the strength of an encompass edge. Crediting a non-ancestor keeps warm a skill nothing gates on. `core_engine.md` 2.2 explicitly declines to require this at its scale; inside 37 hand-authored nodes it holds by construction and is cheap to keep. |
| `WEIGHT_RANGE` [0,1], `CREDIT_RANGE` (0,1] | error | credit excludes zero deliberately: a zero-credit edge grants nothing but still refreshes `last_seen`, which defeats decay silently. |
| `NO_ROOTS`, `UNREACHABLE_FROM_ROOTS` | error | a node not reachable forward from any root sits in or behind a cycle |
| `NO_TARGET` | error | the goal path, XP depth and the `goal_link` slot all depend on it |
| `OFF_GOAL_PATH` | **warning** | 10 of 37 nodes (trig, quotient rule, implicit differentiation, backprop) are deliberately off the gradient-descent path so a real student's errors have somewhere honest to land. Reported, not failed. |
| `SHORTCUT_EDGE` | **info** | see sections 1 to 4 |
| `DIFFICULTY_INVERSION`, `MISSING_DIFFICULTY` | error | see section 5 |
| `MISSING_BLAME_HINT` | error | the diagnosis call constrains `blamed_node` to an enum of real ids; a node with no fence attracts blame belonging to a neighbour, which corrupts the backward pass the whole product rests on |
| `TOO_FEW_ITEMS` (< 3 gradeable) | error | a node with no gradeable bank can never leave the frontier, so it blocks everything above it forever, silently. This is what `alg.vectors` did. Ungradeable items do not count: an ungradeable item is not a repetition, it is an item that marks a correct student wrong. |
| `ITEM_ORPHANED` | warning | items tagged to a node id the graph no longer has |

Current output on the shipped graph: **0 errors, 10 warnings (all `OFF_GOAL_PATH`), 6 info (all
`SHORTCUT_EDGE`)**.

---

## 7. Deliberately not changed

- **The six shortcut edges.** Measured load bearing. Section 3.
- **The 10 off-path nodes.** They are why a trig or quotient-rule error has a node to land on
  instead of being misrouted. Warning, not error.
- **`status` itself.** Making readiness recursive is the principled fix that would earn
  `core_engine.md`'s reduction axiom, and it belongs in `engine/mastery.py`, which this audit does
  not own. Section 4 states the trade so whoever picks it up does not have to rediscover it.
- **Implicit credit staying single-hop.** `core_engine.md` 2.2 specifies transitive propagation and
  is probably right. It changes section 3.5's analysis (credit would reach `alg.factoring` through
  the limit chain) but not its conclusion, since rule 2 only ever raises `a` and scenario E is
  driven by `b`.
- **`db/schema.sql`, `scripts/audit/graph/*.py`, `engine/mastery.py`, `engine/types.py`.** Not
  ours. The `difficulty_b` decision in section 5 is the direct consequence.
