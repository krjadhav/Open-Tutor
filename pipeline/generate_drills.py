#!/usr/bin/env python3
"""Generate CAS-validated drills for the graph nodes OpenStax cannot cover.

Two groups of nodes have no textbook items and both matter:

  - the algebra and trig prerequisite layer, because a calculus book assumes those skills rather
    than drilling them. These are exactly the nodes blame propagation routes errors to, so
    without drills the loop dead-ends: the student gets a blocker they cannot practise.
  - the AI layer (loss, gradient of loss, gradient descent, backprop), because no calculus
    textbook contains it. This is the bridge to the stated goal.

The model never writes an answer. It writes a *spec*, sympy computes the answer, and the stem is
rendered from the same spec (see drill_tasks.py). Anything sympy rejects never reaches a student.

These items are also the commercially clean ones: self-generated, so unlike the OpenStax
exercises they can ship in a paid product.

    export SARVAM_API_KEY=...
    python3 generate_drills.py --per-node 8
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drill_tasks as dt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "data" / "graph" / "nodes.json").read_text())
NODES = {n["id"]: n for n in GRAPH["nodes"]}
TAGGED = ROOT / "data" / "items" / "tagged_items.json"
OUT = ROOT / "data" / "items" / "generated_items.json"
FINAL = ROOT / "data" / "items" / "items.json"

# What kind of drill actually exercises each node. The guidance is the difference between a drill
# that trains the skill and one that merely mentions it.
SPECS = {
    "alg.sign-distribution": (["expand"],
        "EVERY expression must have a negative sign in front of a bracketed sum or difference, "
        "such as 5*x - (3*x - 2) or 2*x - (x - 4) - 3. That subtraction across a bracket IS the "
        "skill. An expression without one is useless here."),
    "alg.fraction-arithmetic": (["simplify"],
        "Rational expressions that must be put over a common denominator, such as 1/(x+h) - 1/x, "
        "2/x + 3/(x+1), or (1/x - 1/3)/(x-3)."),
    "alg.exponent-rules": (["simplify", "evaluate"],
        "Products, quotients and powers of powers with negative or fractional exponents, such as "
        "x**(-3)*x**5, (x**2)**(-3), or 27**(2/3)."),
    "alg.factoring": (["factor"],
        "Quadratics and differences of squares that factor over the integers, such as "
        "x**2 - 7*x + 12 or 4*x**2 - 9."),
    "alg.solving-equations": (["solve"],
        "Linear and quadratic equations with rational roots. Put the left side in expr and the "
        "right side in expr2."),
    "alg.function-composition": (["compose"],
        "Outer and inner function pairs with the structure that later needs the chain rule, such "
        "as f(x)=x**5 with g(x)=3*x**2+1."),
    "alg.radicals": (["simplify", "evaluate"],
        "Radicals and rational exponents, such as sqrt(x**6), 8**(2/3), or sqrt(50)."),
    "trig.unit-circle": (["evaluate"],
        "Exact trig values at standard angles only: pi/6, pi/4, pi/3, pi/2, 2*pi/3, 3*pi/4, "
        "5*pi/6, pi. Write them as cos(pi/3), sin(3*pi/4), tan(pi/6)."),
    "trig.identities": (["simplify"],
        "Expressions that collapse via a Pythagorean or double-angle identity, such as "
        "sin(x)**2 + cos(x)**2, 2*sin(x)*cos(x), or 1 - 2*sin(x)**2."),
    "explog.rules": (["simplify", "evaluate"],
        "Logarithm and exponential laws. Note sympy's log() is the NATURAL log and e is E. Use "
        "things like log(E**4), exp(log(7)), log(x**3) - log(x)."),
    "der.slope-interpretation": (["derivative_at"],
        "Polynomials or simple trig where the tangent slope at the given point is a clean value."),
    "der.constant-multiple-sum": (["differentiate"],
        "Sums and constant multiples of powers ONLY. No products, no quotients, no compositions, "
        "since those belong to other nodes."),
    "mv.functions-several-vars": (["partial"],
        "Simple two-variable polynomials such as x**2*y**3 or 3*x*y + y**2. The skill being "
        "drilled is holding the other variable constant."),
    "opt.local-extrema": (["local_min_x"],
        "Cubics or quartics with exactly one local minimum at a clean x value, such as "
        "x**3 - 3*x or x**4 - 8*x**2."),
    "ai.loss-function": (["evaluate", "partial"],
        "Squared error losses. Use context to frame it as a model's prediction, e.g. 'A model "
        "predicts yhat = 3 when the true value is 5.' then a loss expression."),
    "ai.gradient-of-loss": (["partial"],
        "Squared error loss written in terms of a weight, such as (5 - (w*2 + 1))**2 with the "
        "input and target already substituted. Use context to frame it as one training example."),
    "ai.gradient-descent-step": (["gd_step"],
        "Convex losses in w with clean arithmetic, such as w**2, 3*w**2 - 4*w, or (w-5)**2. "
        "Use small learning rates like 0.1 or 0.01."),
    "ai.backprop-chain": (["partial"],
        "Composed expressions where the partial needs the chain rule applied more than once, "
        "such as (1 - (w2*(w1*3)))**2 with respect to w1. Use context to frame it as a tiny "
        "two-layer network."),
}

TOOL = {
    "type": "function",
    "function": {
        "name": "propose_drills",
        "description": "Propose practice drills as machine-checkable specs.",
        "parameters": {
            "type": "object",
            "properties": {
                "drills": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "enum": sorted(dt.TASKS)},
                            "expr": {"type": "string",
                                     "description": "Main expression in SYMPY syntax: ** for powers, "
                                                    "* for multiplication, pi, E, sqrt(), sin(), log()."},
                            "expr2": {"type": "string", "description": "Second expression, for compose and solve."},
                            "var": {"type": "string", "description": "Variable, default x."},
                            "at": {"type": "string", "description": "Point, for derivative_at, limit and gd_step."},
                            "lr": {"type": "string", "description": "Learning rate, for gd_step."},
                            "context": {"type": "string", "description": "Optional one-sentence framing."},
                            "difficulty": {"type": "number", "description": "-2 easy to +2 hard."},
                        },
                        "required": ["task", "expr", "difficulty"],
                    },
                }
            },
            "required": ["drills"],
        },
    },
}


def make_prompt(node_id, tasks, guidance, n, avoid):
    node = NODES[node_id]
    hint = f"\nDisambiguation: {node['blame_hint']}" if node.get("blame_hint") else ""
    dont = ("\n\nDo NOT repeat any of these expressions, which already exist:\n"
            + "\n".join(f"  {a}" for a in avoid)) if avoid else ""
    return [
        {"role": "system", "content":
            "You write practice drills for a maths tutoring system. You never write answers: you "
            "write a machine-checkable spec, and a computer algebra system derives the answer. "
            "All expressions must be valid SYMPY syntax (** for powers, explicit * for "
            "multiplication). Vary the drills; do not produce the same shape repeatedly."},
        {"role": "user", "content":
            f"""SKILL: {node['title']}{hint}

ALLOWED TASKS: {', '.join(tasks)}

WHAT A GOOD DRILL LOOKS LIKE FOR THIS SKILL:
{guidance}

Propose {n} drills of increasing difficulty that specifically train this skill and nothing else.
Then call propose_drills."""},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-node", type=int, default=8)
    ap.add_argument("--min-per-node", type=int, default=4)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        print("SARVAM_API_KEY is not set.")
        sys.exit(2)
    from sarvamai import SarvamAI
    client = SarvamAI(api_subscription_key=key)

    existing = Counter()
    if TAGGED.exists():
        for it in json.loads(TAGGED.read_text())["items"]:
            if it.get("answer_is_checkable") and it.get("node_id"):
                existing[it["node_id"]] += 1

    targets = [nid for nid in SPECS if existing.get(nid, 0) < 3]
    print(f"{len(targets)} nodes need drills "
          f"({sum(1 for t in targets if existing.get(t,0)==0)} with nothing at all)\n")

    def ask(node_id, n, avoid):
        tasks, guidance = SPECS[node_id]
        for attempt in range(3):
            try:
                r = client.chat.completions(
                    messages=make_prompt(node_id, tasks, guidance, n, avoid),
                    model="sarvam-105b", temperature=0.4, max_tokens=3000,
                    tools=[TOOL],
                    tool_choice={"type": "function", "function": {"name": "propose_drills"}})
                d = r.model_dump() if hasattr(r, "model_dump") else r
                calls = ((d.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
                if not calls:
                    continue
                a = (calls[0].get("function") or {}).get("arguments")
                if isinstance(a, str):
                    a = json.loads(a)
                return a.get("drills") or []
            except Exception:  # noqa: BLE001
                time.sleep(2 ** attempt)
        return []

    def build_node(node_id):
        kept, seen, rejected = [], set(), Counter()
        tasks_allowed = set(SPECS[node_id][0])
        for round_no in range(2):
            want = args.per_node if round_no == 0 else args.per_node
            for d in ask(node_id, want, [k["params"]["expr"] for k in kept]):
                task = d.get("task")
                if task not in tasks_allowed:
                    rejected[f"task {task} not allowed for this node"] += 1
                    continue
                params = {k: v for k, v in d.items() if k not in ("task", "difficulty") and v}
                try:
                    stem, ans_latex, ans = dt.build(task, params)
                except Exception as e:  # noqa: BLE001
                    rejected[str(e)[:60]] += 1
                    continue
                if stem in seen:
                    rejected["duplicate"] += 1
                    continue
                seen.add(stem)
                kept.append({
                    "item_id": f"gen-{node_id}-{len(kept)+1}",
                    "node_id": node_id,
                    "stem_latex": stem,
                    "answer_latex": ans_latex,
                    "answer_kind": dt.serialize_answer(ans)[0],
                    "answer_sympy": dt.serialize_answer(ans)[1],
                    "check": {"task": task, "params": params},
                    "difficulty_b": d.get("difficulty", 0),
                    "encompasses": [e["id"] for e in NODES[node_id].get("encompasses", [])],
                    "answer_is_checkable": True,
                    "source": "generated",
                })
            if len(kept) >= args.min_per_node:
                break
        return node_id, kept, rejected

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(build_node, targets))
    print(f"generated in {time.time()-t0:.0f}s\n")

    all_items, all_rej = [], Counter()
    for node_id, kept, rej in sorted(results):
        all_items.extend(kept)
        all_rej.update(rej)
        flag = "" if len(kept) >= args.min_per_node else "   <-- SHORT"
        print(f"  {node_id:<32} {len(kept):2d} kept, {sum(rej.values()):2d} rejected{flag}")

    print(f"\n{len(all_items)} drills generated and CAS-validated")
    if all_rej:
        print("\nrejections (the CAS doing its job):")
        for why, n in all_rej.most_common(8):
            print(f"  {n:3d}  {why}")

    OUT.write_text(json.dumps({"source": "generated", "licence": "original, no third-party rights",
                               "items": all_items}, indent=2))
    print(f"\nwritten to {OUT}")

    # merge into one bank the app can load
    merged = list(all_items)
    if TAGGED.exists():
        for it in json.loads(TAGGED.read_text())["items"]:
            if it.get("answer_is_checkable") and it.get("node_id"):
                merged.append({
                    "item_id": f"os-{it['openstax_id']}",
                    "node_id": it["node_id"],
                    "stem_latex": it["stem_latex"],
                    "answer_latex": it.get("answer_latex"),
                    "answer_kind": None,
                    "answer_sympy": None,
                    "check": None,
                    "difficulty_b": it.get("difficulty_b", 0),
                    "encompasses": it.get("encompasses", []),
                    "answer_is_checkable": True,
                    "source": "openstax",
                })
    FINAL.write_text(json.dumps({
        "note": "source=openstax is CC BY-NC-SA and cannot ship in a paid product; "
                "source=generated is ours. See learning-design.md section 12.2.",
        "counts": dict(Counter(i["source"] for i in merged)),
        "items": merged}, indent=2))

    counts = Counter(i["node_id"] for i in merged)
    print(f"\nmerged bank: {len(merged)} items across {len(counts)} nodes -> {FINAL}")
    empty = [n for n in NODES if counts.get(n, 0) == 0]
    thin = [n for n in NODES if 0 < counts.get(n, 0) < 3]
    print(f"nodes with no items : {len(empty)}" + (f"  {empty}" if empty else ""))
    print(f"nodes still thin    : {len(thin)}" + (f"  {thin}" if thin else ""))


if __name__ == "__main__":
    main()
