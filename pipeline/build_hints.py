#!/usr/bin/env python3
"""Build the four rung hint ladder, one ladder per graph node, and resolve misconception tags.

Two pieces of content live here, both entirely local. No model is called: the API is expensive
and, more importantly, hint text is the thing a struggling student reads at their worst moment.
It is authored, reviewed and versioned like any other product copy.

THE LADDER (learning-design.md section 12.1, ui-spec.md section 3, Solve)

    rung 1  names the idea, gives no step        "A fraction of two functions. Which rule is that?"
    rung 2  states the rule itself, as maths     is_math true, rendered in the serif face
    rung 3  the first concrete step for this kind of problem
    rung 4  the full method, plus the offer of an isomorphic twin

Rungs 1 and 2 are hand-authored for all 37 nodes, because "which idea is this" and "what is the
rule" are node-level facts and a template cannot say them well. Rungs 3 and 4 are templated per
TASK type (the `check.task` of the node's generated drills), because the first concrete step of
an `expand` drill is the same sentence whatever the numbers are. The nine nodes whose bank is
OpenStax-only have no task to template from, so their rungs 3 and 4 are hand-authored too.

Two rules the copy obeys:

  - **A hint is never a penalty.** No "you should have known this", no "as you were told".
    Taking a hint already costs evidence weight (HINT_PENALTY); it must not also cost dignity.
  - **Under about twenty words.** A hint that has to be read twice is not a hint.

THE TWIN OFFER

Rung 4 offers a twin only for nodes that actually have a drill with a `check` spec, because
`pipeline.twin.twin_of` can only twin those. Promising a twin on an OpenStax-only node and then
having nothing to show is worse than not offering one.

MISCONCEPTION NAMES

`data/content/misconceptions.json` maps a kebab-case tag to a human-readable name. The raw tag
must never reach a student, so `misconception_name` falls back to a readable form derived from
the tag rather than to the tag itself. See `misconception_name` for the fallback rules.

    python3 pipeline/build_hints.py           # write data/content/hints.json
    python3 pipeline/build_hints.py --check   # validate the committed file, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODES_PATH = ROOT / "data" / "graph" / "nodes.json"
ITEMS_PATH = ROOT / "data" / "items" / "items.json"
HINTS_PATH = ROOT / "data" / "content" / "hints.json"
MISCONCEPTIONS_PATH = ROOT / "data" / "content" / "misconceptions.json"

RUNGS_PER_NODE = 4
MAX_WORDS = 24          # the copy target is "about 20"; this is the hard ceiling the build asserts


# --------------------------------------------------------------------------- rungs 1 and 2
#
# (rung 1, rung 2). Rung 2 is always marked is_math and is written as mathematics, because the
# rule stated in symbols is what a student can carry to the next problem. Rung 1 must not leak
# the step: it names the idea and hands the decision back.

HAND_RUNGS_12: dict[str, tuple[str, str]] = {
    "alg.sign-distribution": (
        "There is a minus sign sitting in front of a bracket. How far does it reach?",
        r"$-(a - b) = -a + b$. The minus multiplies every term inside.",
    ),
    "alg.fraction-arithmetic": (
        "Two fractions cannot be combined until they describe parts of the same whole.",
        r"$\frac{a}{b} + \frac{c}{d} = \frac{ad + bc}{bd}$",
    ),
    "alg.exponent-rules": (
        "Same base on both parts. Which law of exponents fits this shape?",
        r"$x^{m}x^{n} = x^{m+n}$, $\dfrac{x^{m}}{x^{n}} = x^{m-n}$, $(x^{m})^{n} = x^{mn}$",
    ),
    "alg.factoring": (
        "You are looking for two brackets whose product rebuilds this expression.",
        r"$x^{2} + (p+q)x + pq = (x+p)(x+q)$, and $a^{2} - b^{2} = (a-b)(a+b)$",
    ),
    "alg.solving-equations": (
        "Gather everything on one side first. What kind of equation is left?",
        r"$ax + b = 0 \Rightarrow x = -\frac{b}{a}$; "
        r"$ax^{2} + bx + c = 0 \Rightarrow x = \frac{-b \pm \sqrt{b^{2} - 4ac}}{2a}$",
    ),
    "alg.function-composition": (
        "One function is sitting inside another. Which one is outer, which is inner?",
        r"$f(g(x))$: apply $g$ to $x$ first, then apply $f$ to that result.",
    ),
    "alg.vectors": (
        "Vectors combine component by component. Which operation is being asked for here?",
        r"$k\mathbf{u} = (ku_{1}, ku_{2})$ and "
        r"$\mathbf{u} \cdot \mathbf{v} = u_{1}v_{1} + u_{2}v_{2}$",
    ),
    "alg.radicals": (
        "A root is a fractional power in disguise. Rewriting it that way usually helps.",
        r"$\sqrt[n]{x^{m}} = x^{m/n}$, so $\sqrt{x} = x^{1/2}$",
    ),
    "trig.unit-circle": (
        "This is one of the standard angles. Picture where it lands on the circle.",
        r"On the unit circle, $\cos\theta$ is the $x$ coordinate and $\sin\theta$ the $y$.",
    ),
    "trig.identities": (
        "Something here collapses. Which identity matches the pattern in front of you?",
        r"$\sin^{2}\theta + \cos^{2}\theta = 1$ and $\sin 2\theta = 2\sin\theta\cos\theta$",
    ),
    "explog.rules": (
        "Logs turn products into sums and exponents into multipliers.",
        r"$\ln(ab) = \ln a + \ln b$, $\ln(a^{n}) = n\ln a$, $e^{\ln a} = a$",
    ),
    "lim.concept": (
        "Ask what the function heads towards near the point, not what it equals there.",
        r"$\lim_{x \to a} f(x) = L$ means $f(x)$ gets arbitrarily close to $L$ as $x \to a$.",
    ),
    "lim.direct-substitution": (
        "If nothing breaks when the value goes in, that value is the limit.",
        r"If $f$ is continuous at $a$, then $\lim_{x \to a} f(x) = f(a)$.",
    ),
    "lim.indeterminate-factoring": (
        "Substituting gave you nothing usable. That is a signal, not an answer.",
        r"$\lim_{x \to a} \frac{(x-a)g(x)}{(x-a)h(x)} = \lim_{x \to a} \frac{g(x)}{h(x)}$",
    ),
    "lim.continuity": (
        "Three separate things have to agree before a graph has no break there.",
        r"$f$ is continuous at $a$ when $f(a)$ exists and $\lim_{x \to a} f(x) = f(a)$.",
    ),
    "der.definition": (
        "The derivative is a limit of slopes of secant lines that keep shrinking.",
        r"$f'(x) = \lim_{h \to 0} \frac{f(x+h) - f(x)}{h}$",
    ),
    "der.slope-interpretation": (
        "The derivative at a point is the slope of the tangent line there.",
        r"The tangent to $y = f(x)$ at $x = a$ has slope $f'(a)$.",
    ),
    "der.power-rule": (
        "A single power of the variable. Two things happen to that exponent.",
        r"$\frac{d}{dx}x^{n} = nx^{n-1}$",
    ),
    "der.constant-multiple-sum": (
        "Differentiate term by term. Constant multipliers simply ride along.",
        r"$(af + bg)' = af' + bg'$",
    ),
    "der.trig-derivatives": (
        "Sine and cosine trade places when differentiated, and one of them picks up a minus.",
        r"$(\sin x)' = \cos x$, $(\cos x)' = -\sin x$, $(\tan x)' = \sec^{2}x$",
    ),
    "der.exp-log-derivatives": (
        "One of these is its own derivative. The other becomes a reciprocal.",
        r"$(e^{x})' = e^{x}$ and $(\ln x)' = \frac{1}{x}$",
    ),
    "der.product-rule": (
        "Two functions multiplied together. Their derivatives do not simply multiply.",
        r"$(uv)' = u'v + uv'$",
    ),
    "der.quotient-rule": (
        "A fraction of two functions. Which rule is that?",
        r"$\left(\frac{u}{v}\right)' = \frac{u'v - uv'}{v^{2}}$",
    ),
    "der.chain-rule": (
        "Something is nested inside something else. Name the outer and the inner.",
        r"$\frac{d}{dx}f(g(x)) = f'(g(x)) \cdot g'(x)$",
    ),
    "der.implicit": (
        "Here $y$ depends on $x$, so differentiating $y$ cannot leave nothing behind.",
        r"$\frac{d}{dx}\left[y^{n}\right] = ny^{n-1}\frac{dy}{dx}$",
    ),
    "der.higher-order": (
        "Differentiate, then differentiate the result. No new rule is needed.",
        r"$f''(x) = \frac{d}{dx}\left[f'(x)\right]$",
    ),
    "mv.functions-several-vars": (
        "The output depends on more than one input. Vary them one at a time.",
        r"$f(x, y)$ assigns a single number to each pair $(x, y)$.",
    ),
    "mv.partial-derivative": (
        "Differentiate with respect to one variable while every other one stays frozen.",
        r"For $\frac{\partial f}{\partial x}$, treat $y$ as a constant, then differentiate in $x$.",
    ),
    "mv.chain-rule-multivar": (
        "Every route from the variable to the output contributes its own term.",
        r"$\frac{df}{dt} = \frac{\partial f}{\partial x}\frac{dx}{dt} "
        r"+ \frac{\partial f}{\partial y}\frac{dy}{dt}$",
    ),
    "mv.gradient": (
        "Collect the partial derivatives into one vector. It points somewhere useful.",
        r"$\nabla f = \left(\frac{\partial f}{\partial x}, \frac{\partial f}{\partial y}\right)$",
    ),
    "mv.directional-derivative": (
        "Rate of change along a direction you choose. Steepest ascent is one special case.",
        r"$D_{\mathbf{u}}f = \nabla f \cdot \mathbf{u}$, where $\lVert\mathbf{u}\rVert = 1$",
    ),
    "opt.critical-points": (
        "Look for where the tangent goes flat, or where there is no tangent.",
        r"Critical points are where $f'(x) = 0$ or $f'(x)$ is undefined.",
    ),
    "opt.local-extrema": (
        "A flat tangent is not enough on its own. Which way does the curve bend?",
        r"If $f'(c) = 0$ and $f''(c) > 0$, then $c$ is a local minimum.",
    ),
    "ai.loss-function": (
        "The loss measures how wrong a prediction is, as a function of the weights.",
        r"$L(w) = \left(\hat{y}(w) - y\right)^{2}$, where $y$ is the true value.",
    ),
    "ai.gradient-of-loss": (
        "Ask how the loss changes when one weight moves. That is a partial derivative.",
        r"$\frac{\partial L}{\partial w} = 2\left(\hat{y} - y\right)"
        r"\frac{\partial \hat{y}}{\partial w}$",
    ),
    "ai.gradient-descent-step": (
        "The gradient points uphill, and you are trying to get downhill.",
        r"$w_{\text{new}} = w - \alpha \frac{\partial L}{\partial w}$",
    ),
    "ai.backprop-chain": (
        "A network is functions inside functions. The chain rule applies once per layer.",
        r"$\frac{\partial L}{\partial w_{1}} = \frac{\partial L}{\partial a}"
        r"\frac{\partial a}{\partial w_{1}}$",
    ),
}


# --------------------------------------------------------------------------- rungs 3 and 4

#: Appended to rung 4 only when the node has at least one drill with a `check` spec, because
#: that is exactly the condition under which `pipeline.twin.twin_of` can produce a twin.
TWIN_OFFER = "Then try a twin problem with new numbers."

#: task -> (rung 3, rung 4 method). The twin offer is appended to the rung 4 method by `build`.
RUNGS_34_BY_TASK: dict[str, tuple[str, str]] = {
    "expand": (
        "Take what is outside the bracket into each term inside, one term at a time.",
        "Distribute across every term, then collect like terms.",
    ),
    "simplify": (
        "Get both parts into the same form first, then combine what matches.",
        "Rewrite over a common denominator or base, combine, then cancel.",
    ),
    "factor": (
        "Find the pair of numbers whose product and whose sum both match.",
        "Use that pair to split the middle term, then group into brackets.",
    ),
    "solve": (
        "Move every term to one side so the other side is zero.",
        "With zero on one side, factor or use the formula, then read off the roots.",
    ),
    "evaluate": (
        "Rewrite it in a form whose exact value you already know.",
        "Reduce to a standard exact value and leave it exact.",
    ),
    "compose": (
        "Put the whole inner function wherever the outer function shows its variable.",
        "Substitute the inner function into the outer one, then simplify.",
    ),
    "differentiate": (
        "Name the rule that matches the structure before you write anything down.",
        "Apply the matching rule, differentiate each piece, then simplify.",
    ),
    "derivative_at": (
        "Differentiate first. Substitute the given value only afterwards.",
        "Differentiate, substitute the point, then simplify to a number.",
    ),
    "limit": (
        "Substitute the value. If it breaks, rewrite the expression before substituting.",
        "Clear the indeterminate form by factoring and cancelling, then substitute.",
    ),
    "partial": (
        "Treat every variable except the named one as a constant.",
        "Hold the other variables fixed, differentiate in the named one, then simplify.",
    ),
    "gradient": (
        "Take one partial derivative per variable and keep them in order.",
        "Compute each partial derivative, then stack them into a vector.",
    ),
    "gd_step": (
        "Differentiate the loss, then evaluate that gradient at the current value.",
        "New value is old value minus the learning rate times the gradient.",
    ),
    "local_min_x": (
        "Find where the slope is zero, then ask which way the curve bends.",
        "Solve for the flat points and keep the one where the curve turns upward.",
    ),
    "critical_points": (
        "Differentiate, then solve for where that derivative is zero.",
        "Set the derivative to zero and solve; those roots are the critical points.",
    ),
    "nth_derivative": (
        "Differentiate once and simplify before differentiating again.",
        "Repeat the differentiation as many times as asked, simplifying between steps.",
    ),
    "dot_product": (
        "Multiply matching components together, then add the products.",
        "Pair the components, multiply each pair, and sum the results.",
    ),
    "scalar_multiple": (
        "The scalar multiplies every component, not only the first.",
        "Multiply each component by the scalar and keep the components in order.",
    ),
    "vector_magnitude": (
        "Square each component, add those squares, then take the root.",
        "Sum the squared components and take the square root of that total.",
    ),
    "vector_update": (
        "Scale the gradient by the learning rate before you subtract anything.",
        "Multiply the gradient by alpha, then subtract component by component.",
    ),
}

#: Nodes whose bank is OpenStax-only, so there is no `check.task` to template from. Rungs 3 and 4
#: are hand-authored here, and they carry no twin offer because no item of theirs can be twinned.
HAND_RUNGS_34: dict[str, tuple[str, str]] = {
    "lim.concept": (
        "Try values just below and just above the point and watch where they head.",
        "Both sides must approach the same number, and that number is the limit.",
    ),
    "lim.direct-substitution": (
        "Put the value straight in and see whether anything breaks.",
        "If the function is continuous there, the substituted value is the limit.",
    ),
    "lim.indeterminate-factoring": (
        "Factor the top and the bottom and look for a shared factor.",
        "Cancel the common factor, then substitute into what is left.",
    ),
    "der.definition": (
        "Write out the difference quotient before you simplify anything.",
        "Expand the difference quotient, cancel the h, then let h go to zero.",
    ),
    "der.chain-rule": (
        "Differentiate the outer function, leaving the inner one untouched inside it.",
        "Outer derivative at the inner function, multiplied by the inner derivative.",
    ),
    "der.implicit": (
        "Differentiate both sides in x, attaching dy/dx every time you pass a y.",
        "Differentiate both sides, gather the dy/dx terms, then solve for dy/dx.",
    ),
    "mv.partial-derivative": (
        "Cover the other variable with your hand and treat it as a number.",
        "Hold the other variables constant and differentiate in the named one.",
    ),
    "mv.chain-rule-multivar": (
        "Sketch the tree of dependencies and count the routes to the variable.",
        "Multiply along each route, then add the routes together.",
    ),
    "mv.directional-derivative": (
        "Turn the direction into a unit vector before anything else.",
        "Dot the gradient with the unit direction vector.",
    ),
}


# --------------------------------------------------------------------------- misconception names

def load_misconceptions(path: str | Path = MISCONCEPTIONS_PATH) -> dict[str, dict]:
    """Load the tag -> {name, short, node_id} table."""
    return json.loads(Path(path).read_text())


def humanise_tag(tag: str) -> str:
    """Fallback name for a tag that is not in the table.

    The diagnosis tool schema leaves `misconception_tag` a free string, so the model can and will
    invent tags we have not authored. The one thing that must never happen is a raw kebab-case
    tag appearing on a Blockers card, so an unknown tag is rendered rather than passed through:

        "sign-distribution"        -> "Sign distribution"
        "missing_inner_derivative" -> "Missing inner derivative"
        "dropping-negatives-dne"   -> "Dropping negatives does not exist"

    A handful of abbreviations that read as noise in a sentence are expanded. Everything else is
    simply de-kebabbed and sentence-cased. The result is never empty: a tag that survives to
    here as nothing at all becomes "Unclassified".
    """
    expand = {"dne": "does not exist", "lhs": "left hand side", "rhs": "right hand side",
              "cas": "computer algebra", "wrt": "with respect to"}
    words = [w for w in str(tag or "").replace("_", "-").replace(" ", "-").split("-") if w]
    if not words:
        return "Unclassified"
    out = " ".join(expand.get(w.lower(), w.lower()) for w in words)
    return out[0].upper() + out[1:]


def misconception_name(tag: str, table: dict[str, dict] | None = None) -> str:
    """Human-readable name for a misconception tag. Never returns the raw tag."""
    entry = (table if table is not None else load_misconceptions()).get(str(tag or ""))
    if entry and entry.get("name"):
        return entry["name"]
    return humanise_tag(tag)


def misconception_short(tag: str, table: dict[str, dict] | None = None) -> str:
    """Chip suffix, as in "Blocker · signs". Falls back to the humanised tag, lower cased."""
    entry = (table if table is not None else load_misconceptions()).get(str(tag or ""))
    if entry and entry.get("short"):
        return entry["short"]
    return humanise_tag(tag).lower()


def misconception_node(tag: str, table: dict[str, dict] | None = None) -> str | None:
    """The graph node a tag belongs to, or None when the tag is unknown."""
    entry = (table if table is not None else load_misconceptions()).get(str(tag or ""))
    return entry.get("node_id") if entry else None


# --------------------------------------------------------------------------- build

def load_nodes(path: str | Path = NODES_PATH) -> list[str]:
    return [n["id"] for n in json.loads(Path(path).read_text())["nodes"]]


def node_tasks(path: str | Path = ITEMS_PATH) -> dict[str, Counter]:
    """node_id -> Counter of `check.task` over its items. OpenStax items have no check spec."""
    out: dict[str, Counter] = {}
    for it in json.loads(Path(path).read_text())["items"]:
        check = it.get("check")
        if not check:
            continue
        out.setdefault(it["node_id"], Counter())[check["task"]] += 1
    return out


def rungs_for(node_id: str, tasks: Counter | None) -> tuple[list[dict], str]:
    """Four rungs for one node, plus "hand" or "templated" for where 3 and 4 came from."""
    if node_id not in HAND_RUNGS_12:
        raise KeyError(f"no hand-authored rungs 1 and 2 for {node_id}")
    r1, r2 = HAND_RUNGS_12[node_id]

    if node_id in HAND_RUNGS_34:
        r3, r4 = HAND_RUNGS_34[node_id]
        source = "hand"
        offer_twin = False
    else:
        if not tasks:
            raise KeyError(f"{node_id} has no drill task to template from and no hand-authored "
                           f"rungs 3 and 4")
        task = tasks.most_common(1)[0][0]
        if task not in RUNGS_34_BY_TASK:
            raise KeyError(f"no rung 3 and 4 template for task {task!r} (node {node_id})")
        r3, r4 = RUNGS_34_BY_TASK[task]
        source = "templated"
        offer_twin = True

    if offer_twin:
        r4 = f"{r4} {TWIN_OFFER}"

    return ([{"text": r1, "is_math": False},
             {"text": r2, "is_math": True},
             {"text": r3, "is_math": False},
             {"text": r4, "is_math": False}], source)


def build() -> tuple[dict, Counter]:
    """Return (hints, provenance counter). Asserts the invariants the UI depends on."""
    node_ids = load_nodes()
    tasks = node_tasks()
    hints: dict[str, dict] = {}
    provenance: Counter = Counter()

    for node_id in node_ids:
        rungs, source = rungs_for(node_id, tasks.get(node_id))
        hints[node_id] = {"rungs": rungs}
        provenance[source] += 1

    validate(hints, node_ids)
    return hints, provenance


def validate(hints: dict, node_ids: list[str]) -> None:
    """Every node has exactly four rungs, rung 2 is maths, and no rung runs long.

    This runs inside the build as well as inside the tests. A ladder that is missing a rung is a
    Solve screen with a dead accordion row, which is worse than a hint that reads a bit flatly.
    """
    missing = [n for n in node_ids if n not in hints]
    if missing:
        raise AssertionError(f"{len(missing)} nodes have no hint ladder: {missing}")
    extra = [n for n in hints if n not in node_ids]
    if extra:
        raise AssertionError(f"hints for nodes not in the graph: {extra}")

    for node_id, entry in hints.items():
        rungs = entry.get("rungs") or []
        if len(rungs) != RUNGS_PER_NODE:
            raise AssertionError(f"{node_id} has {len(rungs)} rungs, expected {RUNGS_PER_NODE}")
        for i, rung in enumerate(rungs, start=1):
            text = (rung.get("text") or "").strip()
            if not text:
                raise AssertionError(f"{node_id} rung {i} is empty")
            if not isinstance(rung.get("is_math"), bool):
                raise AssertionError(f"{node_id} rung {i} has no is_math flag")
            if len(text.split()) > MAX_WORDS:
                raise AssertionError(
                    f"{node_id} rung {i} is {len(text.split())} words, over the {MAX_WORDS} ceiling")
        if not rungs[1]["is_math"]:
            raise AssertionError(f"{node_id} rung 2 must be marked is_math")
        if rungs[0]["is_math"]:
            raise AssertionError(f"{node_id} rung 1 states the rule; it should not")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="validate the committed hints.json and write nothing")
    args = ap.parse_args()

    hints, provenance = build()
    node_ids = load_nodes()

    if args.check:
        if not HINTS_PATH.exists():
            print(f"{HINTS_PATH} does not exist; run without --check first")
            return 1
        committed = json.loads(HINTS_PATH.read_text())
        validate(committed, node_ids)
        if committed != hints:
            print("committed hints.json is out of date with build_hints.py")
            return 1
        print(f"hints.json is current: {len(committed)} nodes, "
              f"{RUNGS_PER_NODE} rungs each")
        return 0

    HINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HINTS_PATH.write_text(json.dumps(hints, indent=2, ensure_ascii=False) + "\n")

    table = load_misconceptions()
    twin_offers = sum(1 for e in hints.values() if TWIN_OFFER in e["rungs"][3]["text"])
    print(f"{len(hints)} nodes x {RUNGS_PER_NODE} rungs -> {HINTS_PATH}")
    print(f"  rungs 1 and 2 hand-authored : {len(hints)}")
    print(f"  rungs 3 and 4 hand-authored : {provenance['hand']}")
    print(f"  rungs 3 and 4 templated     : {provenance['templated']}")
    print(f"  rung 4 offers a twin        : {twin_offers}")
    print(f"{len(table)} misconception tags in {MISCONCEPTIONS_PATH.name}, "
          f"covering {len({e['node_id'] for e in table.values()})} nodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
