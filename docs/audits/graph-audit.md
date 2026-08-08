# Adversarial audit: `data/graph/nodes.json`

Artifact under audit: `data/graph/nodes.json`, 36 nodes, course `calculus-for-ai`, target
`ai.gradient-descent-step`.
Consumers checked: `engine/mastery.py` (status, implicit credit, blame), `engine/selection.py`
(difficulty targeting, goal path, interleave), `engine/xp.py` (depth pays XP),
`experiments/exp2b_tools/run.py:render_nodes` (blame_hint rendering).
Reference: `docs/learning-design.md` 4.1 to 4.4 and 16.
Helper scripts (mine, re-runnable, no engine imports): `scripts/audit/graph/structure.py`,
`scripts/audit/graph/impact.py`.

---

## Verdict

**No, not as it stands.** The graph is structurally immaculate and semantically unreliable, which
is the worst combination because every validator you could write will pass. It is a clean acyclic
DAG with no dangling ids, no duplicates, no self loops, no orphans and credit weights entirely
inside the 0.2 to 0.4 band, and none of that touches the actual problem. The prereq relation
contains at least two edges that are curricular ordering rather than mathematical dependency and
that sit on the critical path to the target, so a failure on `der.product-rule` (a topic gradient
descent does not use) mechanically locks the entire multivariable and AI half of the course. It is
missing at least one prereq that the author's own `encompasses` list proves they knew about
(`opt.critical-points` awards implicit credit for `der.power-rule` while not requiring it, so the
node is reachable by a student who has never differentiated anything). It has no node anywhere for
vectors or the dot product, in a course whose target node is a vector update and whose penultimate
node is a dot product. And 24 of 36 nodes carry no `blame_hint`, including
`mv.directional-derivative`, titled "Directional derivatives and **steepest descent**", which sits
one edge below the target node whose measured section 16 failure was blame being routed to the
wrong node for exactly this reason. Because blame lowers `p` on the blamed node and
`mastery.status()` locks any node with a prereq below `PREREQ_READY`, a misrouted blame onto a
prereq does not merely mislabel the error, it locks the goal. The fixes are small and mostly
mechanical, about two hours of work, but they must land before anything is built on top.

---

## Defect table

Severity: **blocker** = will silently produce a wrong student state or an impossible one;
**serious** = wrong pedagogy or wrong credit, recoverable; **cosmetic** = authoring hygiene.

| # | Sev | Node | Defect | Fix |
|---|---|---|---|---|
| D1 | blocker | `opt.critical-points` | No prereq on any differentiation rule. Its prereqs are `der.slope-interpretation` and `alg.solving-equations`; neither teaches how to compute `f'(x)`. The node is reachable by a student who has never applied the power rule, and finding critical points is then impossible. The node's own `encompasses` lists `der.power-rule` at 0.25, so the author knew it is exercised and simply did not add the edge. `structure.py` flags this as the only encompasses entry outside the transitive prereq closure. | Add prereq `der.power-rule` weight 0.9. Add prereq `alg.factoring` weight 0.7 (solving `f'(x)=0` for a polynomial derivative is a factoring problem). |
| D2 | blocker | `der.chain-rule` | Prereq `der.product-rule` weight 0.6 is not a mathematical dependency. The chain rule is `(f∘g)' = f'(g)·g'`; it uses no product rule anywhere. The author's own `encompasses` list for `der.chain-rule` correctly omits `der.product-rule`, which is direct evidence they did not believe it was exercised. This edge is on the critical path (`der.chain-rule` gates `mv.partial-derivative`, which gates `mv.gradient`, `ai.gradient-of-loss` and the target), so a `der.product-rule` failure locks 8 nodes including the goal. `mastery.status()` ignores prereq weights entirely, so weight 0.6 buys no softness. | Remove prereq `der.product-rule`. Add prereq `der.power-rule` weight 0.9, which is what chain rule problems actually need to differentiate inner and outer and which is already in `encompasses`. |
| D3 | blocker | graph-wide | **No vector node exists.** `mv.gradient` produces a vector, `mv.directional-derivative` is `∇f · u` with `u` a unit vector, and the target `ai.gradient-descent-step` is `w := w − α∇L`, a scalar-times-vector subtraction. Nothing in the 36 nodes covers vector notation, components, scalar multiplication, subtraction or the dot product. A student can be declared "unlocked" for the target having never seen a vector. Any failure that is really a vector-mechanics failure has no node to land on and will be misrouted to a neighbour. | Add a root node `alg.vectors` ("Vector notation, scalar multiples and the dot product", kind skill, `difficulty_b` -0.4). Make it a prereq of `mv.gradient` (0.8), `mv.directional-derivative` (1.0) and `ai.gradient-descent-step` (0.8). |
| D4 | blocker | `ai.gradient-descent-step` | `encompasses: mv.directional-derivative, credit 0.3`. Performing `w := w − α∇L` does not exercise the directional derivative; the directional derivative is the *justification* for why the direction is `−∇L`, and a correct update does not compute one. This hands a student 0.3 pseudo-successes on a depth-11 concept node they never demonstrated. Per section 4.3 rule 2, credit is gated on the diagnosis's `used_nodes`, which makes it worse: the diagnosis model is the same one section 16 measured as unreliable at routing near this node. | Remove the `mv.directional-derivative` entry from `ai.gradient-descent-step.encompasses`. |
| D5 | blocker | `mv.directional-derivative`, `mv.gradient`, `opt.local-extrema` | No `blame_hint` on the three nodes most likely to steal blame from the target. `mv.directional-derivative` is literally titled "...and steepest descent"; `opt.local-extrema` is "Classifying local minima and maxima", which is what a model will reach for when a student ascends instead of descends. This is the c09 failure mode of section 16 with a different magnet. Because both are prereqs (direct or transitive) of the target, a misrouted blame drops their `p`, which drives `p_eff` below `PREREQ_READY`, which **locks the goal node**. | Add the hints in the block below. |
| D6 | serious | `der.definition` | Gates `der.power-rule` (0.6), `der.trig-derivatives` (0.5) and `der.exp-log-derivatives` (0.5). Applying a shortcut rule does not require the limit definition, and the author agrees: none of the three lists `der.definition` in `encompasses`. Since weights are ignored at the gate, the effect is that `der.power-rule`, the 5th easiest node in the graph at `b = -0.2`, is locked behind `lim.concept`, `lim.direct-substitution`, `lim.indeterminate-factoring`, `alg.factoring` and `alg.fraction-arithmetic`. This single edge set produces 4 of the 5 difficulty inversions in D14. | Two defensible options. (a) Keep the edges as curriculum ordering and lower `der.definition.difficulty_b` from 0.5 to 0.2 so the inversions shrink. (b) Remove all three edges and let `der.definition` gate only `der.slope-interpretation`. I recommend (b): it is the mathematically honest relation, it cuts the target's depth from 12 to 10, and the difficulty targeting in `selection.py` already prevents an unready student from being served a rule item. |
| D7 | serious | `der.quotient-rule` | Prereq `der.product-rule` weight 0.7 is ordering, not dependency. `(u/v)' = (u'v − uv')/v²` can be stated and applied with no knowledge of the product rule. Again `encompasses` correctly omits `der.product-rule`. Meanwhile the rule that quotient problems *do* need, `der.power-rule`, is only transitive. | Remove prereq `der.product-rule`; add prereq `der.power-rule` weight 0.9. |
| D8 | serious | `ai.backprop-chain` | Prereq `ai.gradient-descent-step` weight 0.9 is spurious. Backpropagation *computes* gradients; gradient descent *consumes* them. They are independent, and backprop is normally taught before the optimiser. This edge also places the declared `target_node` in the middle of the graph rather than at the end, and inflates `ai.backprop-chain` to depth 13, so it pays 13x XP under `engine/xp.py` on the strength of a fictional dependency. | Remove prereq `ai.gradient-descent-step`. Optionally add prereq `ai.gradient-of-loss` weight 0.9, which backprop genuinely produces. |
| D9 | serious | `mv.functions-several-vars` | Its single prereq `der.slope-interpretation` (0.6) is the **only** edge connecting the single-variable half of the graph to the multivariable and AI half, and it is the wrong one. Understanding `f(x,y)` requires function notation, not tangent slopes. The consequence: everything from `mv.*` to the target hangs off one weak, mismatched edge, and a decay on `der.slope-interpretation` (a concept node with empty `encompasses`, so it can never receive implicit credit, see D15) silently locks the entire AI branch. | Replace prereq `der.slope-interpretation` with `alg.function-composition` weight 0.7. Keep the real single-variable dependency where it already correctly lives, on `mv.partial-derivative` via `der.chain-rule`. |
| D10 | serious | `ai.*` / `opt.*` | The optimisation subtree is disconnected from the AI subtree. `ai.gradient-descent-step` is an algorithm for finding a minimum, and nothing on its path requires the student to know what a local minimum is or how you would recognise one. `opt.critical-points` and `opt.local-extrema` are both leaves that nothing depends on. | Add prereq `opt.local-extrema` weight 0.6 to `ai.gradient-descent-step` (or to `ai.loss-function` if you prefer it earlier). |
| D11 | serious | `explog.rules`, `der.exp-log-derivatives` | Both are leaves, off the goal path, depended on by nothing. For a course called "calculus for AI" this is backwards: `e^x` and `ln x` are the derivatives that matter most (sigmoid, softmax, cross-entropy, log-loss), and trigonometry, which is fully wired, matters least. | Add prereq `der.exp-log-derivatives` weight 0.5 to `ai.gradient-of-loss`, and make at least one `ai.loss-function` item a log-loss so the edge is real. |
| D12 | serious | `alg.solving-equations` | No prereq on `alg.factoring`, yet the title is "Solving linear and **quadratic** equations" and the standard route to a quadratic root is factoring. Its dependant `opt.critical-points` inherits the gap (see D1). | Add prereq `alg.factoring` weight 0.6. |
| D13 | serious | `der.implicit` | Only prereq is `der.chain-rule`. Implicit differentiation of anything containing `xy` or `x²y` requires the **product rule**, and every implicit problem ends by solving a linear equation for `dy/dx`, requiring `alg.solving-equations`. Both are missing. Note the irony: `der.product-rule` is currently a prereq of `der.chain-rule`, where it does not belong, and absent from `der.implicit`, where it does. | Add prereqs `der.product-rule` weight 0.8 and `alg.solving-equations` weight 0.7. Add `encompasses` entries `der.product-rule` 0.3 and `alg.solving-equations` 0.25. |
| D14 | serious | 5 nodes | Five difficulty inversions, a node rated easier than its own prereq: `der.power-rule` (-0.2) under `der.definition` (0.5), delta -0.7; `der.slope-interpretation` (0.1) under `der.definition`, -0.4; `der.trig-derivatives` (0.1) under `der.definition`, -0.4; `der.exp-log-derivatives` (0.2) under `der.definition`, -0.3; `der.constant-multiple-sum` (-0.3) under `der.power-rule` (-0.2), -0.1. Four of five point at one node. See D19 for why the stated consequence is not the real one. | Fix via D6, and either raise `der.constant-multiple-sum` to -0.1 or accept the 0.1 tie as noise. |
| D15 | serious | `mv.functions-several-vars`, `ai.loss-function`, `der.slope-interpretation` | The only three non-root nodes with an empty `encompasses`. All three are concept nodes on the AI path, and `mv.functions-several-vars` gates the whole `ai.*` branch. A node that never appears in anyone's `encompasses` can never receive implicit credit, so under the section 4.3 rule 4 decay model its `p_eff` only ever falls between explicit reviews. When it crosses `PREREQ_READY` it silently locks `ai.loss-function`, `ai.gradient-of-loss` and `ai.gradient-descent-step` with no visible cause. | Add `mv.functions-several-vars` credit 0.3 to `mv.partial-derivative.encompasses` and credit 0.3 to `ai.loss-function.encompasses`. Add `der.definition` credit 0.25 to `der.slope-interpretation.encompasses`. |
| D16 | serious | `der.trig-derivatives` | `encompasses: trig.unit-circle, credit 0.25` contradicts the node's own `blame_hint`, which says "Wrong VALUES at standard angles belong to trig.unit-circle". Differentiating `sin x` to `cos x` does not evaluate anything at a standard angle unless the problem asks for a value there, so the entry awards credit for a skill the solution did not exercise. | Remove the entry, or restrict it to items that request a value at a specific angle. |
| D17 | serious | `der.product-rule` | `encompasses: der.constant-multiple-sum, credit 0.25`. A product rule solution *writes* a sum (`u'v + uv'`); it does not *apply* the sum rule, which is about differentiating a sum term by term. Merely related, not exercised. | Remove the entry. |
| D18 | serious | `mv.partial-derivative` | Its `blame_hint` is the only one of the twelve with no exclusion clause: "Taking a partial derivative, including holding the other variables constant." Section 16 records case c08, a student who differentiated both variables, at **1/3**, scattering across `mv.partial-derivative`, `der.product-rule` and `der.definition`. This hint was the one meant to catch c08 and it does not, because it names the correct behaviour instead of fencing off the neighbours. | Replace with the sharpened text in the block below, and add hints to `der.definition` and `der.constant-multiple-sum` so the scatter has somewhere else to be pushed away from. |
| D19 | serious | all 36 | `difficulty_b` on a node is **never read by the engine**. `selection.predicted_success` uses `item.difficulty_b` off `data/items/items.json`; `services/grading.py` and `services/diagnose.py` do the same; the drill pipeline writes item difficulty from the tagger, not from the node. So the claim under audit that inversions "break the 85% difficulty targeting" is false as built, and the more interesting problem is the opposite: node difficulty is dead data that nobody validates against item difficulty, so nothing will ever tell you it is wrong. | Either delete `difficulty_b` from the node schema, or use it: assert that each node's mean item difficulty is within 0.5 of the node value, and fall back to it for untagged items. |
| D20 | serious | all 36 | `goal_tags` is specified in the section 4.1 node schema and is absent from every node. Section 4.4 budgets a "Goal link" slot and says "Prefer ones on the goal path" for new nodes, neither of which can be implemented without it. With 16 of 36 nodes off the goal path (D21), this is the field that would have made the gap visible. | Add `goal_tags` to every node, at minimum `["gradient-descent"]` on the 20 ancestors of the target. |
| D21 | serious | 16 nodes | 44 percent of the course is not required for the declared target: `ai.backprop-chain`, `alg.radicals`, `alg.sign-distribution`, `alg.solving-equations`, `der.exp-log-derivatives`, `der.higher-order`, `der.implicit`, `der.quotient-rule`, `der.trig-derivatives`, `explog.rules`, `lim.continuity`, `mv.chain-rule-multivar`, `opt.critical-points`, `opt.local-extrema`, `trig.identities`, `trig.unit-circle`. Note `alg.sign-distribution` in that list: the node section 16 spent its headline finding on is not a prerequisite of anything on the goal path. A student on the gradient descent goal can have blame routed to a node their curriculum never contains, and the section 4.4 remediation slot will then serve them items for it. | Applying D1, D10, D11, D12 and D13 pulls 7 of these onto the path (verified by `impact.py`: 20 required nodes becomes 26). Cut or explicitly re-scope the rest, see the recommendations. |
| D22 | serious | `der.higher-order` | Prereq is `der.power-rule` only. Differentiating a polynomial twice needs the sum and constant-multiple rules on the second pass at least as much as the first. | Add prereq `der.constant-multiple-sum` weight 0.7. |
| D23 | cosmetic | `der.definition` | Expanding `f(x+h) − f(x)` for any multi-term `f` requires distributing the negative, which is exactly `alg.sign-distribution`, and there is no prereq on it. Low severity because `alg.sign-distribution` is a root and cheap to satisfy, but it is a genuine dependency and it would put that node on the goal path where section 16 implicitly assumes it already is. | Add prereq `alg.sign-distribution` weight 0.6 and `encompasses` entry credit 0.25. |
| D24 | cosmetic | `alg.exponent-rules` | `blame_hint` says "including getting -3-1 wrong". Ambiguous in a prompt-only field: it renders to the diagnosis model as the string `-3-1`, which could be an expression, a subtraction, or a mangled exponent decrement. Section 16 establishes that this exact field is load-bearing prompt engineering. | Reword to "...including computing a decremented exponent wrongly, such as -3 - 1 = -4". |
| D25 | cosmetic | `alg.fraction-arithmetic` | `blame_hint` excludes "arithmetic slips inside them" but there is no arithmetic node in the graph, so the exclusion routes the model to nowhere and it will pick a neighbour instead. | Either add an `alg.arithmetic` node or reword the exclusion to name a destination. |
| D26 | cosmetic | `trig.identities` | `encompasses: trig.unit-circle, credit 0.25`. Manipulating `sin²+cos²=1` or a double-angle identity symbolically does not require evaluating anything at a standard angle. Related, not necessarily exercised. Same class as D16, lower stakes because the node is a leaf. | Remove, or drop credit to 0.2. |
| D27 | cosmetic | `der.exp-log-derivatives` | `encompasses: explog.rules, credit 0.25`. `d/dx ln x = 1/x` exercises no log property. Only true for composite arguments such as `ln(x²)`. | Keep at 0.2 and only for composite-argument items, or remove. |
| D28 | cosmetic | `ai.gradient-of-loss`, `ai.backprop-chain` | Omissions in `encompasses`. Computing `∂L/∂w` for any composed model exercises `der.chain-rule`, which is absent. Backprop produces the loss gradient, and `ai.gradient-of-loss` is absent from `ai.backprop-chain.encompasses`. | Add `der.chain-rule` 0.3 to `ai.gradient-of-loss`; add `ai.gradient-of-loss` 0.35 to `ai.backprop-chain`. |
| D29 | cosmetic | `ai.gradient-descent-step` | Prereq `mv.directional-derivative` weight 0.8 gates the target on the *justification* for the update direction rather than the ability to perform it. Defensible as pedagogy; noted because weight 0.8 does nothing at the gate and it is the second half of the D4 pair. | Keep if intentional, but be aware the target is unreachable without a depth-11 concept node. |
| D30 | cosmetic | `der.quotient-rule` | Highest total implicit credit in the graph: 0.35 + 0.30 + 0.25 = 0.90 across three children from one correct attempt. Every individual entry is justified (D-table clean on all three), but the total is close to a full extra repetition awarded off one problem. | No change required; flagged so it is a deliberate choice rather than an accident. |
| D31 | cosmetic | 21 remaining nodes | No `blame_hint`. Lower risk than D5 but every one of them is a plausible magnet. | See the ready-to-apply block below. |

**Counts: 5 blockers, 17 serious, 9 cosmetic.**

---

## Proposed `blame_hint` block, ready to apply

`blame_hint` is prompt-only and is rendered by `exp2b_tools/run.py:render_nodes` as
`title [hint]`. Section 16's measured lesson is that the discriminating half of a hint is the
exclusion clause, not the description. Every hint below names at least one place the error is
**not**. The three revisions come first because two of them are implicated in measured failures.

### Revisions to existing hints

```json
"mv.partial-derivative":
  "ONLY for the partial derivative itself, above all differentiating with respect to BOTH variables instead of holding the others constant. An error inside the single-variable differentiation belongs to the rule used, not here."

"alg.exponent-rules":
  "Arithmetic and laws of exponents, including computing a decremented exponent wrongly, such as -3 - 1 = -4. Not for misapplying the power rule itself, which is der.power-rule."

"alg.fraction-arithmetic":
  "Combining, subtracting or simplifying rational expressions. A pure numeric slip inside one is not this node: blame the topic the problem is tagged to."
```

### New hints, highest risk first

```json
"mv.directional-derivative":
  "ONLY for the directional derivative as the gradient dotted with a unit direction, including forgetting to normalise the direction. The SIGN or form of the gradient descent update belongs to ai.gradient-descent-step, never here, despite the words 'steepest descent' in the title."

"opt.local-extrema":
  "ONLY for classifying an already-found critical point as a minimum, maximum or neither. Moving in the wrong direction during a parameter update belongs to ai.gradient-descent-step."

"mv.gradient":
  "ONLY for assembling the gradient vector from its partials: wrong ordering, missing component, or returning a scalar. One partial computed wrongly belongs to mv.partial-derivative; the update that consumes the gradient belongs to ai.gradient-descent-step."

"der.definition":
  "ONLY for the difference-quotient limit itself: a malformed quotient, a dropped limit, or failing to cancel h. If the student used a shortcut rule and applied it wrongly, blame that rule's node, not this one."

"alg.function-composition":
  "ONLY for failing to see or decompose f(g(x)) outside of differentiation. Misidentifying inner and outer WHILE differentiating belongs to der.chain-rule."

"lim.direct-substitution":
  "ONLY for substituting incorrectly, or substituting into an undefined expression and stopping there. Reaching 0/0 and not knowing to factor belongs to lim.indeterminate-factoring, and not knowing what a limit is belongs to lim.concept."

"der.constant-multiple-sum":
  "ONLY for the sum and constant-multiple rules: a dropped coefficient, or a constant differentiated to something other than zero. Products belong to der.product-rule and quotients to der.quotient-rule."

"ai.gradient-of-loss":
  "ONLY for computing dL/dw itself: a missing parameter, a dropped chain rule factor, or differentiating with respect to the data instead of the parameters. The update rule that consumes the gradient belongs to ai.gradient-descent-step."

"ai.loss-function":
  "ONLY for treating the loss as a function of the PARAMETERS rather than of the data. Not a general bucket for anything machine-learning flavoured."

"mv.chain-rule-multivar":
  "ONLY for the multivariable chain rule, such as omitting one path in df/dt = sum over variables of partial times derivative. A single-variable chain rule error belongs to der.chain-rule."

"mv.functions-several-vars":
  "ONLY for reading or evaluating f(x,y), including confusing a variable with a parameter. Differentiating it belongs to mv.partial-derivative."

"explog.rules":
  "ONLY for log and exponential algebra, such as ln(ab) or e^(a+b). The derivative formulas for e^x and ln x belong to der.exp-log-derivatives."

"der.exp-log-derivatives":
  "ONLY for the derivative formulas of e^x and ln x. Log and exponent algebra belongs to explog.rules, and a missing inner derivative to der.chain-rule."

"alg.factoring":
  "ONLY for failing to factor a polynomial correctly. Factoring in order to resolve a 0/0 limit belongs to lim.indeterminate-factoring."

"alg.solving-equations":
  "ONLY for solving a given equation wrongly. Setting up the WRONG equation, including writing the wrong f'(x) = 0, belongs to the topic that produced it."

"opt.critical-points":
  "ONLY for the procedure of setting f'(x) = 0 and locating the points. An algebra slip while solving belongs to alg.solving-equations; a differentiation slip belongs to the rule used."

"der.slope-interpretation":
  "ONLY for the MEANING of f'(a) as a tangent slope, including reporting f(a) when f'(a) was asked for. Computing the derivative belongs to the rule used."

"der.implicit":
  "ONLY for implicit differentiation as such, above all omitting dy/dx on a y term. A plain chain rule error belongs to der.chain-rule and the final rearrangement to alg.solving-equations."

"der.higher-order":
  "ONLY for taking the further derivative: stopping at the first, or misreading the order asked for. An error inside any single differentiation belongs to that rule's node."

"ai.backprop-chain":
  "ONLY for propagating gradients layer by layer, such as skipping a layer's local derivative. The multivariable chain rule itself belongs to mv.chain-rule-multivar."

"lim.continuity":
  "ONLY for the continuity test at a point and its three conditions. Evaluating the limit belongs to lim.direct-substitution and the meaning of a limit to lim.concept."

"trig.unit-circle":
  "ONLY for wrong exact values of sine or cosine at standard angles. The derivative formulas belong to der.trig-derivatives."

"trig.identities":
  "ONLY for misusing a Pythagorean or double-angle identity. A wrong angle VALUE belongs to trig.unit-circle."

"alg.radicals":
  "ONLY for converting between radical and rational-exponent form. Exponent arithmetic itself belongs to alg.exponent-rules."
```

If a new `alg.vectors` node is added per D3:

```json
"alg.vectors":
  "ONLY for vector mechanics: components, scalar multiples, subtraction, and the dot product. The direction the gradient points belongs to mv.gradient, and the descent update to ai.gradient-descent-step."
```

### Review of the 12 existing hints

| Node | Verdict |
|---|---|
| `alg.sign-distribution` | Accurate and sharp. The section 16 fix held. |
| `lim.concept` | Accurate and sharp. |
| `lim.indeterminate-factoring` | Accurate. The "including calling it 'does not exist'" clause is a good misconception catch. |
| `der.power-rule` | Accurate. Correctly pairs with `alg.exponent-rules`. |
| `der.trig-derivatives` | Accurate, and it contradicts the node's own `encompasses` list, see D16. |
| `der.product-rule` | Accurate. |
| `der.quotient-rule` | Accurate. |
| `der.chain-rule` | Accurate. |
| `ai.gradient-descent-step` | Accurate. The capitalised SUBTRACTS is doing real work per section 16. |
| `alg.exponent-rules` | Ambiguous example, D24. |
| `alg.fraction-arithmetic` | Exclusion points at no node, D25. |
| `mv.partial-derivative` | Weakest of the twelve: no exclusion clause, and measured at 1/3 on c08. D18. |

---

## What I checked that came back clean

Reproduce with `python3 scripts/audit/graph/structure.py`.

- **Acyclicity.** Kahn's algorithm topologically orders all 36 of 36 nodes. No cycles.
- **No duplicate ids.** 36 ids, 36 unique.
- **No dangling references.** Every id in every `prereqs` and `encompasses` list resolves to a real node, across all 36 nodes.
- **No self loops** in either relation.
- **No unreachable nodes.** Every node has a finite longest-chain depth, so every node is reachable from the 8-node root layer. 8 roots, 9 leaves, no isolated components.
- **No upward `encompasses`.** No node awards credit to one of its own descendants, which would be a credit cycle.
- **Credit band.** All 37 `encompasses` credits are inside 0.2 to 0.4 as documented. Distribution: 0.25 appears 7 times, 0.3 appears 13, 0.35 appears 11, 0.4 appears 6. No violations, though nothing sits at the 0.2 floor and section 4.3 advises discounting hard at about 0.25, so the band is used top-heavy.
- **Prereq weights.** All 44 prereq edges have weights inside 0 to 1.
- **Schema.** Every node has `id`, `title`, `kind`, `difficulty_b`, `prereqs`, `encompasses`. `kind` is `skill` or `concept` everywhere. Only `goal_tags` is missing (D20).
- **Item coverage.** All 36 nodes have at least 6 items in `data/items/items.json`, so no node is unreachable for lack of content. (Item quality is another agent's audit.)
- **Root layer is sane.** The 8 roots are exactly the 8 nodes a course should assume: sign distribution, fraction arithmetic, exponent rules, factoring, solving equations, function composition, unit circle, informal limit. No calculus node is accidentally a root.
- **Encompasses is mostly a subset of the transitive prereq closure,** as section 4.1 says it should be. Exactly one violation, D1, and it turned out to be the missing-prereq blocker rather than a bad credit entry.

### Depth and the length of the course

Longest prereq chain: **13 nodes**. Depth of the target `ai.gradient-descent-step`: **12**.

```
lim.concept -> lim.direct-substitution -> lim.indeterminate-factoring -> der.definition
  -> der.power-rule -> der.constant-multiple-sum -> der.product-rule -> der.chain-rule
  -> mv.partial-derivative -> mv.gradient -> ai.gradient-of-loss -> ai.gradient-descent-step
  -> ai.backprop-chain
```

Node counts by depth: 8, 4, 2, 1, 4, 4, 3, 2, 2, 2, 2, 1, 1.

**Are these sane for a few weeks? Marginally, and for the wrong reason.** Section 4.4 introduces 1 to
2 new nodes per session, so 36 nodes is 18 to 36 sessions, roughly 4 to 7 weeks at 5 sessions a
week. That part is fine. The problem is the *serial* constraint: depth 12 means the student cannot
reach the goal in fewer than 12 sessions no matter how fast they are, and four of those twelve
steps are edges this audit says are wrong. The depth-1 layer holds 8 nodes and depths 3, 4, 12 and
13 hold one or two each, so the graph is a funnel that narrows to a single-file queue exactly where
the material gets hard and the student most wants parallel options for the interleaving rule in
section 4.4.

Applying the D2, D6, D7 and D8 removals plus the D1, D10, D11, D12, D13, D22, D23 additions
(`python3 scripts/audit/graph/impact.py`) gives:

- max depth 13 to **10**, target depth 12 to **10**
- `der.chain-rule` 8 to **3**, `der.quotient-rule` 8 to **3**, `der.implicit` 9 to **5**
- nodes required for the target 20 to **26** of 36
- the critical path becomes the conceptual spine, which is the right one:
  `lim.concept -> lim.direct-substitution -> lim.indeterminate-factoring -> der.definition -> der.slope-interpretation -> mv.functions-several-vars -> mv.partial-derivative -> mv.gradient -> ai.gradient-of-loss -> ai.gradient-descent-step`

### The XP side effect nobody mentioned

`engine/xp.py` computes `XP = XP_BASE * node_depth * novelty * quality`, so **prereq depth is
money** and every spurious edge is an inflation bug. There are 23 ordered node pairs where the
deeper node is also the easier one, meaning the easier node pays more XP. Worst examples:

- `der.constant-multiple-sum` (depth 6, `b` = -0.3) pays 6x; `der.definition` (depth 4, `b` = +0.5) pays 4x
- `der.constant-multiple-sum` pays 6x; `lim.indeterminate-factoring` (depth 3, `b` = +0.2) pays 3x, twice the reward for an easier node
- `der.quotient-rule` (depth 8, `b` = +0.5) pays 8x; `ai.loss-function` (depth 7, `b` = +1.0) pays 7x
- `ai.backprop-chain` pays 13x, of which the last edge is D8's fictional dependency

The `xp.py` docstring claims "grinding is worthless" because depth stops a student farming the easy
roots. That defence holds only if depth tracks difficulty. Here it does not, and the proposed edge
fixes reduce the inverted pairs from 23 to 18. Fixing the rest requires the difficulty
recalibration in D14 and D19.

---

## Recommendations

Separate from the findings above. These are judgements, not defects.

**1. Fix the five blockers before anything else.** D1, D2, D3, D4, D5 are perhaps 90 minutes of
work in total, and each one is invisible until a student hits it. D5 in particular is the cheapest:
section 16 measured a 0/3 to 3/3 swing from sharpening two strings.

**2. Decide what this course is for, then cut.** Right now it is a general calculus course with an
AI node bolted on the end. As a mathematics educator I would cut `trig.identities`,
`der.trig-derivatives`, `lim.continuity`, `alg.radicals` and `der.implicit`: five nodes, all leaves,
all off the goal path, none of which appear anywhere in gradient descent. That is 14 percent of the
course a goal-directed student will never be shown, and section 4.4's "prefer ones on the goal path"
rule means they will sit unselected while still costing item-bank budget. If instead the course is
meant to be broad, keep them but wire them in and say so, because right now the graph asserts a
single target node and then spends nearly half its mass elsewhere.

**3. Add what is actually missing for gradient descent.** In priority order: vectors and the dot
product (D3, a blocker); a node for the derivative of a composed loss such as
`L = (y − wx)²`, which is the single most important computation in the course and currently exists
only as `ai.gradient-of-loss` with no scaffold below it; and the linearity of the gradient over a
sum of training examples, which is why `der.constant-multiple-sum` matters in ML and is currently
unmotivated. Consider a `ml.sigmoid-derivative` node to give `explog.rules` and
`der.quotient-rule` a reason to exist on the goal path.

**4. Write these checks as ingest validators, not as an audit.** Everything in `structure.py` should
run in CI on `data/graph/nodes.json`: acyclicity, dangling ids, credit band, and specifically the
two checks that found real bugs here, `encompasses` outside the transitive prereq closure (found
D1) and difficulty inversions (found D6). Section 4.1 already says to validate the DAG on ingest;
extend it. Add one more: every non-root node must appear in at least one other node's `encompasses`,
which would have caught D15.

**5. Make prereq weight mean something, or delete it.** `mastery.status()` deliberately ignores
weights, so a weight-0.5 prereq and a weight-1.0 prereq lock a node identically. The author clearly
used low weights (0.5, 0.6, 0.7) on precisely the edges this audit found spurious, which suggests
they were reaching for a "soft prereq" concept the engine does not implement. Either add one, for
example weight below 0.7 means recommended-not-required, or remove the field so that adding an edge
is understood to be an unconditional lock. This is the root cause behind D2, D6, D7 and D9: four
defects that all look like someone hedging with a number that does nothing.

**6. Pre-compute the diagnosis routing for every item.** Section 16 notes `seed` does not pin the
output and 11 of 12 cases agree across identical passes. Given how much of this audit is about
blame landing on the wrong node, the graph should be treated as an input to an offline routing test:
run every item's known-wrong variants through the diagnosis and assert the blamed node. That turns
`blame_hint` from prompt folklore into a regression suite.

**7. Reconsider whether `ai.backprop-chain` should exist in a demo whose target is
`ai.gradient-descent-step`.** It is the deepest, hardest, last node, it is reached through a
fictional edge, and nothing depends on it. Either promote it to the target or drop it.
