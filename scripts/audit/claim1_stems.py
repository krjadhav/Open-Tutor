#!/usr/bin/env python3
"""Claim 1b: does stem_latex actually correspond to the check spec?

Re-render each stem independently from the spec and diff against the stored stem. Also flag
grading hazards: float answers, answers equal to the displayed question, unstated-variable stems.
"""
import json
import re
from pathlib import Path

import sympy as sp
from sympy.parsing.sympy_parser import (parse_expr, standard_transformations,
                                        implicit_multiplication)

ROOT = Path(__file__).resolve().parents[2]
TR = standard_transformations + (implicit_multiplication,)


def P(s):
    return parse_expr(str(s), transformations=TR, evaluate=True)


def PU(s):
    try:
        return parse_expr(str(s), transformations=TR, evaluate=False)
    except Exception:
        return P(s)


L = sp.latex


def render(task, p):
    v = sp.Symbol(str(p.get("var", "x")))
    ctx = p.get("context")
    if task == "differentiate":
        return f"Differentiate $f({v}) = {L(PU(p['expr']))}$."
    if task == "derivative_at":
        return (f"Find the slope of the tangent to $y = {L(P(p['expr']))}$ at "
                f"${v} = {L(P(p['at']))}$.")
    if task == "partial":
        s = f"Find $\\frac{{\\partial f}}{{\\partial {v}}}$ for $f = {L(P(p['expr']))}$."
        return f"{ctx} {s}" if ctx else s
    if task == "gradient":
        return f"Find the gradient $\\nabla f$ of $f(x,y) = {L(P(p['expr']))}$."
    if task == "expand":
        return f"Expand and simplify ${L(PU(p['expr']))}$."
    if task == "factor":
        return f"Factor ${L(PU(p['expr']))}$."
    if task == "simplify":
        return f"Simplify ${L(PU(p['expr']))}$."
    if task == "solve":
        return (f"Solve for ${v}$: ${L(P(p['expr']))} = {L(P(p.get('expr2','0')))}$.")
    if task == "evaluate":
        return f"Evaluate ${L(PU(p['expr']))}$, giving an exact value."
    if task == "compose":
        return (f"If $f({v}) = {L(P(p['expr']))}$ and $g({v}) = {L(P(p['expr2']))}$, "
                f"find $f(g({v}))$.")
    if task == "limit":
        return f"Evaluate $\\lim_{{{v} \\to {L(P(p['at']))}}} {L(PU(p['expr']))}$."
    if task == "gd_step":
        w = sp.Symbol(str(p.get("var", "w")))
        s = (f"The loss is $L({w}) = {L(P(p['expr']))}$. Using a learning rate of "
             f"$\\alpha = {L(P(p['lr']))}$ and starting from ${w} = {L(P(p['at']))}$, perform one "
             f"gradient descent update. What is the new value of ${w}$?")
        return f"{ctx} {s}" if ctx else s
    if task == "local_min_x":
        return (f"Find the $x$-coordinate of the local minimum of "
                f"$f({v}) = {L(P(p['expr']))}$.")
    raise ValueError(task)


def main():
    gen = json.loads((ROOT / "data/items/generated_items.json").read_text())["items"]
    drift, floats, noop, ctx_ignored, var_mismatch = [], [], [], [], []
    for it in gen:
        chk = it["check"]
        task, p = chk["task"], chk["params"]
        try:
            mine = render(task, p)
        except Exception as e:
            drift.append((it["item_id"], f"RENDER-FAIL {e}"))
            continue
        if mine != it["stem_latex"]:
            drift.append((it["item_id"], f"stored={it['stem_latex']!r}\n        rerender={mine!r}"))

        # float answer -> the grader demands 15 significant digits
        a = it["answer_sympy"]
        if re.search(r"\d\.\d{4,}", str(a)) or (re.search(r"\.", str(a)) and it["answer_kind"] == "expr"):
            floats.append((it["item_id"], a, it["stem_latex"][:90]))

        # a context string that the renderer silently discards
        if p.get("context") and task not in ("partial", "gd_step"):
            ctx_ignored.append((it["item_id"], task, p["context"]))

        # the displayed question already equals the answer
        try:
            if L(PU(p["expr"])) == it["answer_latex"]:
                noop.append((it["item_id"], task, it["stem_latex"]))
        except Exception:
            pass

        # stem shows a variable the spec does not differentiate with respect to
        if task in ("partial", "differentiate"):
            e = P(p["expr"])
            v = sp.Symbol(str(p.get("var", "x")))
            if v not in e.free_symbols:
                var_mismatch.append((it["item_id"], task, p, str(e.free_symbols)))

    print(f"=== stem re-render drift: {len(drift)}")
    for d in drift:
        print("  ", d[0], d[1][:400])
    print(f"\n=== float / decimal answers (grader needs exact 15 digits): {len(floats)}")
    for f in floats:
        print("  ", f[0], "|", f[1], "|", f[2])
    print(f"\n=== context discarded by renderer: {len(ctx_ignored)}")
    for c in ctx_ignored:
        print("  ", c)
    print(f"\n=== no-op (question latex == answer latex): {len(noop)}")
    for n in noop:
        print("  ", n)
    print(f"\n=== differentiating w.r.t. an absent variable: {len(var_mismatch)}")
    for v in var_mismatch:
        print("  ", v)


if __name__ == "__main__":
    main()
