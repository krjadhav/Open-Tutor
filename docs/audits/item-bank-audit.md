# Item bank audit

Adversarial audit of `data/items/items.json` (376 items), `data/items/generated_items.json` (132),
`data/items/tagged_items.json` (282), `data/items/raw_items.json` (660) and `data/graph/nodes.json`
(36 nodes), against the claims in `docs/learning-design.md` section 17.

Audited 2026-08-07. Scripts under `scripts/audit/`. Nothing outside `docs/audits/` and
`scripts/audit/` was modified.

**API key check: the literal string `sk_REDACTED_ROTATE_THIS_KEY` does not appear anywhere in the working tree, in
any tracked file, or in any commit reachable from `--all`. No `sk_`-prefixed secret of any kind was
found. Clean.**

---

## Verdict

**No, not as it stands.** The counting claim is honest and the generated half is genuinely strong:
I independently recomputed all 132 generated answers from their `check` specs with sympy without
looking at the stored values, and 131 of 132 match exactly in both `answer_sympy` and
`answer_latex`, all 132 round-trip through the production grader, and the stem re-renders
byte-identically from the spec in all 132 cases. That part of the design works. But "correct by
construction" is not the same as "correct", and the audit breaks the claim in two places the design
did not anticipate: the *unevaluated* renderer silently performs the very algebra the drill is
meant to test, which destroys 2 of the 6 drills on `alg.sign-distribution` (the node the demo
climaxes on), and the `context` string is dropped by the renderer for `evaluate` and `compose`,
which strips the AI framing out of 4 of the 7 `ai.loss-function` items and leaves one stem
(`gen-ai.loss-function-4`) that literally asks for two different derivatives in one sentence.
The OpenStax half is worse. The 7-mismatch number guard does work as advertised (I re-derived it
from the cached HTML and confirmed 7 disagreements, 0 leaked), but it never ran on 62 of the 244
shipped items because their exercise number is `null`, and it cannot catch the real failure: two
pairs of items ship **identical stems with contradictory answers** (`lim x->2 f(x)` answered both
`1` and `0`), so the CAS grader will mark a correct student wrong roughly half the time on those.
The group-instruction reattachment, called out in the docs as load-bearing, is misattributing
instructions on **30 shipped items**, including 18 limit questions prefixed with an instruction
about a completely unrelated function. And the tagger's `answer_is_checkable` flag, which the whole
bank is filtered on, admitted 24 prose answers ("Yes. It is continuous.", "Answers may vary."), 21
multi-part `a./b./c.` answers, and 6 "show that" proofs. Counting only defects I can demonstrate
mechanically, **96 of 376 items (26%) are unfit to show a student**, and four nodes fall below the
"none thin" bar once those are removed. The bank is a good skeleton and a bad body: keep the
generator, fix the renderer, and re-derive the OpenStax half with a real per-group instruction
parser before anything is built on top of it.

---

## Defects

Severity: **blocker** = would show a student a wrong, unanswerable or unrenderable item, or would
grade a correct answer wrong. **serious** = degrades the pedagogy or the integrity of a claim.
**cosmetic** = ugly but harmless.

| # | Sev | Item id(s) | What is wrong |
|---|---|---|---|
| 1 | blocker | `os-fs-id1170572540807`, `os-fs-id1170572268064` | Identical stem (`For the following exercises, consider the function $f(x)=(1+x)^{1/x}$. $\lim_{x\to 2}f(x)$`), contradictory answers: `1` and `0`. Both shipped, both tagged `lim.direct-substitution`. The CAS grader will fail a correct student on one of them. |
| 2 | blocker | `os-fs-id1170572540909`, `os-fs-id1170572372780` | Identical stem (`$\lim_{x\to 0^-}f(x)$`), contradictory answers: `1` and `−2`. Also tagged to two different nodes (`lim.direct-substitution`, `lim.concept`) despite being byte-identical. |
| 3 | blocker | `gen-ai.loss-function-4` | The stem says one thing and the spec computes another, the exact failure §17 claims is impossible. Stem: `Loss L = (a*x + b - 10)^2, find ∂L/∂b. Find $\frac{\partial f}{\partial x}$ for $f = (ax+b-10)^2$.` The `check` spec has no `var`, so it defaults to `x`; the stored answer `2a(ax+b-10)` is `∂/∂x`, not `∂/∂b`. Self-contradictory in a single sentence. |
| 4 | blocker | `gen-ai.loss-function-6` | Same missing-`var` bug, degenerate outcome. Spec `partial` of `2*(a*y-5)**2` with no `var`, so it differentiates with respect to `x`, which does not occur in the expression. Stored answer is `0`. `_nontrivial` only rejects constant answers for task `differentiate`, so `partial` slips through. |
| 5 | blocker | `gen-ai.loss-function-3` | No-op drill. Stem: `Evaluate $(a2 + b - 10)^2$, giving an exact value.` Answer: `(2a + b - 10)^2`. The question *is* the answer. The `_nontrivial` guard compares `latex(PU(expr))` to `latex(answer)` as strings; `a 2` vs `2 a` differ only in term order, so the guard passed. Confirmed structurally: `parse_latex(stem) == parse_latex(answer)`. |
| 6 | blocker | `gen-alg.sign-distribution-3` | The skill is rendered away. Spec `-( (2*x + 3) - (x - 5) )`; `PU` (evaluate=False) still flattens the nested `Add` and distributes the unary minus, so the stem reads `Expand and simplify $-x-5-3$`. There is no bracket, so the drill does not train distributing a negative across a bracket at all. This is the node the demo climax depends on. |
| 7 | blocker | `gen-alg.sign-distribution-5` | Same cause, worse outcome. Spec `-( (1/2)*x - 3/4 )` renders as `Expand and simplify $-\frac{x}{2} + \frac{3}{4}$`; the answer is `3/4 - x/2`. The displayed question is the answer, reordered. The latex-string guard was defeated by the reordering. |
| 8 | blocker | `gen-ai.gradient-descent-step-8` | Float answer that does not round-trip. Computed value is `Float('0.4702691809394981')`; `str()` stored `0.470269180939498`, losing the last digit (difference 1.11e-16). Verified against the production grader: typing the mathematically exact value `0.47026918093949805` is **rejected**, and so is `0.4703`. Only the exact stored 15-digit string is accepted. |
| 9 | blocker | `gen-ai.gradient-descent-step-7` | Answer is `0.99 - 0.01*cos(1)`. A student is expected to type a symbolic expression in a "what is the new value of w" drill; the numeric answer `0.9846` is rejected by the grader. |
| 10 | blocker | 8 items: `os-fs-id1167793515331`, `os-fs-id1167793941283`, `os-fs-id1169736608305`, `os-fs-id1169736662054`, `os-fs-id1169736662220`, `os-fs-id1167793630197`, `os-fs-id1170571654941`, `os-fs-id1170572499827` | Unanswerable: reference a figure, a table or a preceding exercise that is not in the bank. `needs_figure` is computed on the bare `stem` only, never on the reattached `group_instruction`, so "use the information in the following table" and "using the graph of the surface" both slipped past the filter. |
| 11 | blocker | 5 items, all `lim.continuity`: `os-fs-id1170570998828`, `os-fs-id1170571120812`, `os-fs-id1170573414086`, `os-fs-id1170573449551`, `os-fs-id1170573580625` | Piecewise definitions flattened into unparseable LaTeX with unbalanced braces, 2 of them with an odd number of `$`. Example: `For $f(x)={x^{2} \text{if} x \neq 1 3 \text{if} x = 1,$`. Will not render. |
| 12 | blocker | 7 items: `os-fs-id1167793544558`, `os-fs-id1167794293307`, `os-fs-id1167794327634`, `os-fs-id1169739353376`, `os-fs-id1170571654941`, `os-fs-id1170573361460`, `os-fs-id1170573361738` | LaTeX control sequences split by a space by the MathML converter: `\partia l` (should be `\partial`), `\p i` (`\pi`), `\inft y` (`\infty`), `\thet a` (`\theta`). Renders as literal garbage. §17 claims "MathML needs a real converter" was solved; it was not solved completely. |
| 13 | blocker | 24 items (see `scripts/audit/_defects.json` key `answer is prose, not an expression`) | Answers are prose, yet `answer_is_checkable` is `true` so they shipped. Examples: `Answers may vary.`, `Yes. It is continuous.`, `Nowhere`, `Discontinuous at 1; removable`, `The absolute minimum was in 1848, when no gold was produced.`, `The sign is negative.` A CAS cannot grade any of these. The tagger's system prompt explicitly forbids exactly this. |
| 14 | blocker | 21 items (key `multi-part answer (a./b./c.)`) | Multi-part answers marked checkable. Examples: `a. −0.80000000; b. −0.98000000; c. −0.99800000; ...`, `a. $2,$ b. does not exist, c. $2.5$`, `a. $-4$ b. $y=3-4x$`. Same prompt violation. |
| 15 | blocker | 6 items: `os-fs-id1167793397561`, `os-fs-id1167793630197`, `os-fs-id1167793887463`, `os-fs-id1167793925055`, `os-fs-id1167793929166`, `os-fs-id1167793992274` | "Show that …" proofs and "Create a tree diagram …" constructions, marked checkable. There is no expression to type. |
| 16 | blocker | 30 items (full list in `scripts/audit/_defects.json`) | Wrong or truncated group instruction. Three distinct failures: (a) 18 items carry `For the following exercises, consider the function $f(x)={(1+x)}^{1/x}$.` prefixed to entirely unrelated limit questions, because `group_instruction()` walks `find_all_previous("p")` and falls back to an earlier group when the correct instruction is not a `<p>`; (b) 10 `der.definition` items carry the `x_1`/`x_2` secant instruction but their stem supplies `a=2`, i.e. they belong to the next exercise group; (c) 2 items carry `For the following exercises, find equations of.`, i.e. the instruction itself was truncated mid-sentence by the MathML converter. |
| 17 | serious | 62 of 244 OpenStax items (26%) | The answer-number cross-check never ran. `parse_section` only compares numbers when *both* the question number and the answer-key number are non-null; 62 shipped items have `number: null`, so their answer was accepted on an id match alone. The docs present the guard as the thing that keeps the grader honest; on a quarter of the bank it is not running. |
| 18 | serious | ~28 items | Mis-tagged. Detail in the Claim 2 section below. Worst single case: `os-fs-id1169739335937` ("Find a quadratic polynomial such that f(1)=5, f'(1)=3, f''(1)=-6") is tagged `alg.solving-equations`, a prerequisite node that blame propagation routes blocked students to. A student blocked on basic algebra would be handed a second-derivative problem. |
| 19 | serious | 12 items: `gen-ai.backprop-chain-1..8`, `gen-ai.gradient-of-loss-5`, `gen-ai.loss-function-2`, `-4`, `-6` | Raw sympy source leaks into a student-facing stem. Every backprop item reads e.g. `Two-layer network: output = (1 - (w2*(w1*3 + w2)))**2. Find $\frac{\partial f}{\partial w1}$ for $f = ...$`. The `context` string restates the expression in unrendered code, next to the same expression in LaTeX. |
| 20 | serious | `gen-ai.backprop-chain-1` | The composition is evaluated away before display. `t_partial` renders from `P(expr)` (evaluated), so `(w2*(w1*3))**2` displays as `f = 9w_1^2 w_2^2`. The stated purpose of the node is "the partial needs the chain rule applied more than once"; the displayed drill is a bare power rule. |
| 21 | serious | 4 items: `gen-ai.loss-function-1`, `-3`, `-5`, `-7` | `t_evaluate` and `t_compose` silently discard the `context` parameter (only `t_partial` and `t_gd_step` use it). The AI framing is stored in the JSON but never shown, so `gen-ai.loss-function-1` reaches the student as `Evaluate $(2 - 5)^2$, giving an exact value.`, i.e. arithmetic, tagged to `ai.loss-function`. Affects 12 generated items in total (8 `alg.function-composition` items lose only decorative context). |
| 22 | serious | 5 items: `gen-alg.fraction-arithmetic-1`, `-3`, `-5`, `-6`, `-8` | Mixed-number ambiguity in the rendered stem. `1/(x+h)` renders through `PU` as `1 \frac{1}{h + x}`, which reads as "one and one over h plus x". Over half the node's items are affected. |
| 23 | serious | 8 items: `gen-ai.gradient-descent-step-1..6`, `-8`, `gen-ai.loss-function-5` | Answers stored as floats (`2.40000000000000`, `0.500000000000000`). Six of the eight happen to grade correctly because sympy compares `2.4` and `12/5` as equal, but the design has no policy for numeric tolerance and item 8 above shows what happens when the float is not exact. |
| 24 | serious | `gen-alg.exponent-rules-3` / `gen-alg.radicals-5` | Byte-identical item (`Evaluate $27^{2/3}$`) shipped under two different node ids. A student mastering one is credited on an unrelated node. |
| 25 | serious | 12 nodes | Effective variety is one template. `alg.factoring`, `alg.fraction-arithmetic`, `alg.function-composition`, `alg.sign-distribution`, `der.slope-interpretation`, `mv.functions-several-vars`, `trig.identities`, `trig.unit-circle` each reduce to **1** distinct stem shape; `alg.exponent-rules`, `alg.radicals`, `alg.solving-equations`, `explog.rules`, `opt.local-extrema` reduce to 2. All 8 `trig.unit-circle` items are `Evaluate $\text{trig}(\text{angle})$`; `tan` never appears despite the generator guidance asking for it. Eight items is eight instances of one drill, not eight drills. |
| 26 | serious | `alg.sign-distribution` | Only **4** of 6 items actually contain a bracketed group preceded by a minus (verified by regex on the rendered stem). The node the whole demo narrative hangs on has four real drills. |
| 27 | serious | `docs/learning-design.md` §17 | Internal inconsistency: "**15 nodes** had zero items" then "132 drills for those **18 nodes**". `SPECS` in `generate_drills.py` covers 18 nodes; `tagged_items.json` shows 15 nodes with zero checkable OpenStax items and 3 more below the `<3` threshold. The two sentences describe different sets and neither is labelled. |
| 28 | serious | `gen-alg.exponent-rules-4` | Displayed drill is far easier than the spec. Spec `(x**3/x**5)**(-2)` renders through `PU` as `Simplify $\frac{1}{\frac{1}{x^{4}}}$`, so the exponent arithmetic is already done. Answer `x^4`. Same class of failure as items 6 and 7, milder. |
| 29 | cosmetic | 119 items | `\text{sin}`, `\text{cos}`, `\text{lim}`, `\text{sec}`, `\text{ln}` instead of `\sin`, `\cos`, `\lim`. Renders, but upright with wrong spacing, and it defeats any downstream LaTeX parser. |
| 30 | cosmetic | 35 items | `\text{/}` instead of `/`. |
| 31 | cosmetic | 32 items | Bare unicode maths glyphs outside `$…$`: `−` (U+2212), `∇`, `∂`, `∴`, `…`, `±`, `·`, and non-breaking hyphens `‑` in generated `context` strings. |
| 32 | cosmetic | `os-fs-id1169739299853` | Stem asks for `h'(x)`; the published answer is labelled `k'(x)=−13/(4x−3)^2`. Value is correct; the label is a textbook typo carried through verbatim. |
| 33 | cosmetic | `gen-ai.loss-function-7` | Stem renders as `Evaluate $\left(\left(-1\right) 12 - 1 + 2 \cdot 1^{2} + 5\right)^{2}$`. `(-1)12` with no operator reads as `-112`. Answer `36` is correct. |
| 34 | cosmetic | `gen-der.slope-interpretation-1` | `Find the slope of the tangent to $y = 3x + 2$ at $x = 1$`, i.e. the tangent to a straight line. `_nontrivial` only rejects constant answers for task `differentiate`, not `derivative_at`. |
| 35 | cosmetic | `gen-ai.gradient-descent-step-5` | Labelled "Cubic loss with a saddle point"; `w^3 - 3w` is unbounded below and the generator guidance asked for convex losses. Arithmetic is correct. |

**Totals: 16 blocker rows covering 81 distinct items, 12 serious rows covering a further 78 distinct
items, 7 cosmetic rows. 96 distinct items carry a hard defect that a script can demonstrate; the
remaining serious count is dominated by the 62 items whose answer was never number-cross-checked,
which is a process gap rather than a proven per-item error.**

---

## Claim-by-claim, independently verified

### Claim 1: "Every generated item's answer is correct, by construction."

**Partly true, and true in the way that matters least.** `scripts/audit/claim1_recompute.py`
re-implements all 13 tasks from scratch (it does not call `drill_tasks.build`) and recomputes every
answer from `check.task` + `check.params` with sympy, ignoring the stored values.

| measure | result |
|---|---|
| generated items | 132 |
| `answer_sympy` reloads and equals my independent recomputation | 131 / 132 |
| `answer_latex` equals `sympy.latex(my recomputation)` | 131 / 132 |
| mismatches | 1 (`gen-ai.gradient-descent-step-8`, float precision, defect 8) |
| stem re-renders byte-identically from the spec | **132 / 132** |
| all 132 reload and pass `check_student_answer` against their own stored string | 132 / 132 |
| stems where the displayed question is structurally identical to the answer | 2 (`gen-ai.loss-function-3`, `gen-alg.sign-distribution-5`) |
| stems where the displayed expression no longer exercises the node's skill | 4 (`gen-alg.sign-distribution-3`, `-5`, `gen-alg.exponent-rules-4`, `gen-ai.backprop-chain-1`) |
| `partial`/`differentiate` specs whose `var` is absent from the expression | 1 (`gen-ai.loss-function-6`, answer forced to 0) |
| specs whose `context` is silently dropped by the renderer | 12 |

The arithmetic is sound. The failure is upstream of the arithmetic: `PU` (`evaluate=False`) is
treated as a guarantee that the question is shown unsimplified, and it is not. `evaluate=False`
stops sympy evaluating `Add` arguments but the parser still builds `Mul(Integer(-1), …)` and
flattens nested `Add`s, so `-((2x+3)-(x-5))` renders as `-x-5-3`. The `_nontrivial` guard is then
asked to catch the consequence by comparing two LaTeX *strings*, which any term reordering defeats.

### Claim 2: "Every item is tagged to the right node."

**False.** I judged every item in 20 nodes by hand (189 OpenStax items) plus all 132 generated
items, well above the 60 asked for, weighted onto the named nodes. Confident mis-tags:

| item id | tagged | should be | why |
|---|---|---|---|
| `os-fs-id1169739335937` | `alg.solving-equations` | `der.higher-order` / `opt.*` | "Find a quadratic polynomial such that f(1)=5, f'(1)=3, f''(1)=−6". Requires second derivatives. Placed in a pre-calculus prerequisite node that blame propagation routes struggling students to. Worst mis-tag in the bank. |
| `os-fs-id1169737933513`, `os-fs-id1169738199977`, `os-fs-id1169738039194` | `der.chain-rule` | `der.exp-log-derivatives` | All three are "use logarithmic differentiation". Two other items from the *same* OpenStax exercise group (`os-fs-id1169738092313`, `os-fs-id1169738094022`) *were* tagged `der.exp-log-derivatives`. The tagger split one group across two nodes. |
| `os-fs-id1169739266714` | `der.chain-rule` | `der.higher-order` | "Find its acceleration at time t" from `s(t)=sin(4t)`: a second derivative. |
| `os-fs-id1169738991955` | `der.chain-rule` | `der.slope-interpretation` / `opt.critical-points` | "Find the x-coordinates at which the tangent line is horizontal." |
| `os-fs-id1169736595964`, `os-fs-id1169739303429`, `os-fs-id1169739273659` | `der.trig-derivatives` | `der.higher-order` | 59th derivative of sin x; `d²y/dx²` of `2csc x`; velocity and acceleration of `2 sin t`. All are higher-order derivative drills. |
| `os-fs-id1167794296106` | `mv.partial-derivative` | `mv.gradient` | Stem is literally "Find the gradient ∇f(x,y,z)". |
| `os-fs-id1167793929685` | `mv.partial-derivative` | `opt.critical-points` | "Find all points at which f_x = f_y = 0". The near-identical `os-fs-id1167793423260` *was* tagged `opt.critical-points`. Inconsistent. |
| `os-fs-id1167793268124`, `os-fs-id1167793929056`, `os-fs-id1167794293188` | `mv.gradient` | `mv.directional-derivative` | "the direction the function increases most rapidly" / "maximum rate of change and the direction in which it occurs" is the steepest-descent skill, and `mv.directional-derivative` is titled "Directional derivatives and steepest descent". |
| `os-fs-id1167793941435`, `os-fs-id1167793929431` | `mv.gradient` | (drop) | Tangent-plane and normal-line equations. Not a gradient drill, and the instruction is truncated. |
| `os-fs-id1167793387139` | `mv.directional-derivative` | `mv.gradient` | Its own group instruction says "find the gradient". |
| 8 items: `os-fs-id1165041865041`, `1165042132672`, `1165042062070`, `1165042288667`, `1165040756181`, `1165042266576`, `1165040675916`, `1165042108871` | `opt.critical-points` | `opt.local-extrema` | All from the "find the local and/or absolute extrema" groups. `opt.local-extrema` received exactly one item from the same group (`os-fs-id1165042051306`); the other 8 went to the neighbouring node. This is why `opt.critical-points` looks healthy at 17 and `opt.local-extrema` needed 6 generated drills. |
| `os-fs-id1170572232003` | `lim.direct-substitution` | `lim.concept` | Table-based numerical estimation of a limit, not substitution. |
| `os-fs-id1170572540909` vs `os-fs-id1170572372780` | `lim.direct-substitution` vs `lim.concept` | (drop) | Byte-identical stems tagged to different nodes. |
| `gen-ai.loss-function-1`, `-3`, `-5`, `-7` | `ai.loss-function` | `alg.*` arithmetic | After the renderer drops `context`, these are `Evaluate $(2-5)^2$` and friends. They train no loss-function skill. |
| `gen-ai.loss-function-2`, `-4`, `-6` | `ai.loss-function` | (rewrite) | Differentiate with respect to `x`, the model *input*. The node is "Loss as a function of **parameters**". `-4` and `-6` are already blockers. |
| `gen-ai.backprop-chain-1` | `ai.backprop-chain` | `der.power-rule` | Displayed as `f = 9w_1^2 w_2^2`; no chain rule required. |

**28 confident mis-tags out of ~321 items judged (8.7%).** Systemic pattern: the tagger is
inconsistent *within* a single OpenStax exercise group, which is the one place it should be
trivially consistent, and its `answer_is_checkable` flag (defects 13 to 15) fails on 51 items
against its own explicit instructions. `tag_confidence` is reported as zero low-confidence for all
281 items, which given this error rate means the confidence signal is uninformative and cannot be
used as a review filter.

### Claim 3: "OpenStax answers belong to their questions."

**The specific guard works. The claim it is meant to support does not hold.**
`scripts/audit/claim3_openstax.py` re-parses the cached HTML in `.cache/openstax/` and re-derives
the id → answer-number mapping independently of `fetch_openstax.py`.

| measure | result |
|---|---|
| solution elements matched to a question by id | 314 |
| question and answer-key numbers agree | 229 |
| question and answer-key numbers **disagree** | **7**, which matches the documented count exactly |
| mismatched answers that leaked into the bank | **0**, so the guard did fire |
| answers accepted with the guard unable to run (a number was `null`) | **78 raw, 62 shipped (26% of the OpenStax bank)** |
| items carrying an answer whose exercise number is even | 0 (consistent with odd-only publication) |

CAS verification, `scripts/audit/claim3_verify2.py` and `claim3_broad.py` (46 derivative-style
items, 3 partial-derivative items, 3 limit items, 52 total sampled beyond the 40 requested):

| measure | result |
|---|---|
| derivative-style items parsed and CAS-compared | 46 |
| verified correct outright | 24 |
| verified correct after hand-adjudicating a `sec`/`csc`/`cot` identity my comparator could not close | **+21** (all 8 "MISMATCH" and 10 of 14 "unparseable" rows are correct textbook answers; e.g. `d/dx cot x = -csc²x = -cot²x - 1`) |
| genuinely wrong answer found | **0** |
| items where the answer answers a *different* question than the stem asks | **0** in this sample |
| partial-derivative items CAS-checked | 3, all correct (1 apparent mismatch is `sec² = tan²+1`) |
| direct-substitution limits CAS-checked | 3, all correct |

So the OpenStax *answers* are, where I could decide, correct answers to their questions. The
failure is elsewhere: 18 items use an undefined `f`/`g`/`h` whose defining graph or table was never
scraped (`$\lim_{x\to 0^-} g(x)$` with answer `3`), and two pairs of graph-dependent exercises
collapse to identical stems with contradictory answers once the graph is stripped (defects 1 and
2). "The answer belongs to the question" is true; "the question is answerable from what shipped"
is not.

### Claim 4: "376 items, all 36 nodes covered, none thin."

**The counts are exactly right. The variety claim is not.** `scripts/audit/counts.py`.

| measure | claimed | verified |
|---|---|---|
| total items in `items.json` | 376 | **376** |
| `source=openstax` | 244 | **244** |
| `source=generated` | 132 | **132** |
| distinct nodes covered | 36 | **36** |
| nodes with 0 items | 0 | **0** |
| nodes with <3 items | 0 | **0** |
| duplicate `item_id` | (not claimed) | **0** |
| `node_id` values not present in the graph | (not claimed) | **0** |
| `generated_items.json` count | 132 | 132 |
| `tagged_items.json` count / checkable | 282 / 244 | 282 / 244, 1 `tag_error` |
| `raw_items.json` total / usable | 660 / 282 | 660 / 282 |

Effective variety, measured as distinct stem shapes after collapsing every `$…$` span and every
numeral (`scripts/audit/claim4_variety.py`):

| node | items | distinct stem shapes |
|---|---|---|
| `alg.factoring` | 8 | **1** |
| `alg.fraction-arithmetic` | 8 | **1** |
| `alg.function-composition` | 8 | **1** |
| `alg.sign-distribution` | 6 | **1** |
| `der.slope-interpretation` | 8 | **1** |
| `mv.functions-several-vars` | 8 | **1** |
| `trig.identities` | 6 | **1** |
| `trig.unit-circle` | 8 | **1** |
| `alg.exponent-rules` / `alg.radicals` / `alg.solving-equations` / `explog.rules` / `opt.local-extrema` | 6 to 9 each | 2 |

Duplicates: 4 stems appear more than once (2 with contradictory answers, defects 1 and 2; 1 exact
cross-node clone, defect 24; 1 benign). 209 stem pairs exceed 0.85 token Jaccard, 172 of them
within a single node.

**Counts after removing every item with a demonstrable hard defect: 280 of 376 survive, and 4 nodes
fall below "none thin":** `ai.backprop-chain` **0**, `ai.gradient-descent-step` **1**,
`lim.continuity` **1**, `lim.concept` **2**. `lim.direct-substitution` drops to 3 and
`ai.loss-function` to 3.

### Claim 5: "The bank is usable."

**No.** `scripts/audit/claim5_quality.py` and `scripts/audit/defects.py`.

| problem | items |
|---|---|
| answer is prose, not an expression | 24 |
| multi-part `a./b./c.` answer | 21 |
| wrong or truncated group instruction | 30 |
| raw sympy source in a student-facing stem | 12 |
| references a figure, table or another exercise | 8 |
| LaTeX control sequence split by a space | 7 |
| proof or construction, no expression to type | 6 |
| unbalanced `$` or braces (will not render) | 5 |
| mixed-number ambiguity (`1 \frac{1}{x}`) | 5 |
| 15-digit float the student must type exactly | 8 |
| identical stem with a contradictory answer | 4 |
| exact duplicate stem | 4 |
| **distinct items with at least one hard defect** | **96 of 376 (26%)** |
| `\text{sin}` / `\text{lim}` instead of `\sin` / `\lim` (cosmetic) | 119 |
| `\text{/}` instead of `/` (cosmetic) | 35 |
| bare unicode maths glyphs outside `$…$` (cosmetic) | 32 |

---

## What I checked that came back clean

So the reader knows the boundaries of this audit:

- **No API key.** `sk_REDACTED_ROTATE_THIS_KEY` appears in no file, no cached HTML, and no commit in
  `git log --all -p`. No `sk_`-prefixed string of any form exists in the tree.
- **Arithmetic of the generator.** All 132 generated answers recomputed from their specs with an
  independent re-implementation of every task; 131 exact, 1 off by 1.1e-16.
- **Stem-to-spec fidelity.** All 132 generated stems re-render byte-identically from
  `check.task` + `check.params`. There is no drift between what is stored and what the spec
  produces. (The defect is that the spec's own renderer is lossy, not that the two disagree.)
- **Grader round-trip.** All 132 generated items load through `dt.load_answer` and pass
  `dt.check_student_answer` against their own stored string; `set` and `expr` kinds both survive.
  Spot-checked generosity: `12/5` accepted for `2.4`, `3, 1/2` accepted for `1/2, 3`,
  `1/sqrt(2)` accepted for `sqrt(2)/2`, `x**(-6)` accepted for `1/x^6`. The grader really is
  form-tolerant, as claimed.
- **The documented `split_symbols` fix.** `TRANSFORMS` uses plain `implicit_multiplication`, and
  every `w1`/`w2` backprop item differentiates with respect to a symbol that actually exists.
  Verified: no backprop answer is `0`.
- **The documented answer-serialisation fix.** `answer_kind` is present on all 132 items and
  `FiniteSet` answers round-trip. No `Matrix([[…]])` or `{2, 3}` strings in the bank.
- **The 7-mismatch guard.** Re-derived from the cached HTML: exactly 7 number disagreements, and
  exactly 0 of them leaked an answer into the bank. That claim is accurate.
- **OpenStax answer correctness where a CAS can decide it.** 52 items CAS-checked
  (46 derivative-style, 3 partial, 3 limit). Zero wrong answers found; every apparent mismatch
  resolved to a trigonometric identity or a parser artifact on my side.
- **Odd-numbered publication.** Every numbered item carrying an answer has an odd exercise number,
  consistent with OpenStax's answer-key policy. No even-numbered item smuggled in a fabricated
  answer.
- **Referential integrity.** 0 duplicate `item_id`, 0 `node_id` outside the 36-node graph, 0 empty
  nodes, `counts` block in `items.json` matches the actual contents, `generated_items.json` and
  the generated slice of `items.json` agree item for item.
- **Counts.** Every number in §17's tables (376 / 244 / 132 / 36 / 660 / 282 / 281 / 244) verified
  independently and found correct.
- **No mojibake.** Despite §17 flagging the ISO-8859-1 risk, I found **zero** `Ã`/`â€`-class
  mojibake. The encoding fix worked. The corruption that does exist (`\partia l`, `\p i`) comes
  from the MathML converter, not the encoding.
- **No truncated stems** beyond the 3 flagged, and those 3 end mid-clause because the source
  exercise does, not because of a byte limit.

Not checked: whether the 12 CAS rejections cited in §17 really occurred (would require rerunning
the generator against the Sarvam API); the licensing claim; anything in `engine/` or `experiments/`.

---

## Recommendations

Separate from the findings above. These are my suggestions, not defects.

1. **Do not render questions from `parse_expr(evaluate=False)`.** It is not a display-safe
   unevaluated form. Render the stem from the model's original *string* with a small
   sympy-syntax-to-LaTeX printer that never reassociates, or make the model emit the display LaTeX
   as a separate field and validate that it parses back to the spec expression. Defects 5 to 7, 22,
   28 and 33 all share this single root cause.
2. **Replace the string-comparison triviality guard with a structural one.** `latex(PU(expr)) ==
   latex(answer)` is defeated by term reordering. Compare `parse_latex(rendered_stem)` to the
   answer for structural equality, and additionally require a task-specific property: a
   sign-distribution drill must contain a `Mul(-1, Add(...))` node in the *rendered* tree, a
   fraction-arithmetic drill must contain at least two distinct denominators, and so on.
3. **Make `var` required for `partial`.** Defaulting to `x` produced a self-contradictory stem and
   a forced-zero answer. Also extend the `is_number` triviality rejection from `differentiate` to
   `partial`, `derivative_at` and `gradient`.
4. **Render `context` for every task, or drop it from the schema.** Silently storing a field the
   renderer ignores is how `ai.loss-function` became four arithmetic questions. And render the
   context as prose, never as raw sympy. The backprop stems should not contain `**`.
5. **Give float answers a tolerance, or forbid them.** Either store `answer_kind: "numeric"` with
   an explicit tolerance the grader honours, or require `gd_step` learning rates and start points
   that produce exact rationals. Right now a correct student can be rejected by a 1e-16 difference,
   which is the error the design says is the most damaging.
6. **Rewrite `group_instruction()`.** Do not walk `find_all_previous("p")` unbounded. OpenStax
   wraps each exercise group; find the nearest common ancestor group container and take its
   instruction, and hard-fail the item if no instruction is found inside that container rather than
   inheriting one from a previous group. Then reject any item whose instruction ends in a
   preposition, which catches the truncated ones.
7. **Compute `needs_figure` on the final stem, not the bare one.** Add `surface`, `preceding
   exercise`, `previous exercise`, `tree diagram`, `equation 3.` and `table` to the pattern, and
   run it after the instruction is reattached. That alone removes defect 10.
8. **Stop trusting the tagger's `answer_is_checkable`.** Add a deterministic post-filter: reject any
   answer matching `^(yes|no|none|nowhere|DNE|answers may vary)`, containing `a. `/`b. `, longer
   than ~80 characters, or containing more than one `=` at top level. That is 51 items removed with
   no model call.
9. **Deduplicate on the stem before writing `items.json`,** and hard-fail the build when two items
   share a stem but not an answer. Two such pairs shipped.
10. **Fix the MathML converter's control-sequence emission** (`\partial`, `\pi`, `\infty`,
    `\theta` are being split) and emit `\sin` rather than `\text{sin}`. 119 items improve for free.
11. **Measure variety, not count, in the "none thin" gate.** Require ≥3 distinct stem shapes per
    node, not ≥3 items. Under that rule 12 nodes currently fail and the generator needs a second
    task type for each.
12. **Reconcile §17's "15 nodes" and "18 nodes"** and restate the headline as "376 items, 280 of
    which are currently fit to serve" once the blockers are triaged. The current phrasing is
    defensible arithmetic attached to an indefensible implication.
