#!/usr/bin/env python3
"""Claim 1: recompute all 132 generated answers from the check spec, independently.

We deliberately re-implement each task from scratch here rather than calling drill_tasks.build,
so a bug in drill_tasks cannot hide itself.
"""
import json
import re
import sys
from pathlib import Path

import sympy as sp
from sympy.parsing.sympy_parser import (parse_expr, standard_transformations,
                                        implicit_multiplication)

ROOT = Path(__file__).resolve().parents[2]
TR = standard_transformations + (implicit_multiplication,)


def P(s):
    return parse_expr(str(s), transformations=TR, evaluate=True)


def solve_spec(task, p):
    """Independent re-derivation of the answer from the spec."""
    v = sp.Symbol(str(p.get("var", "x")))
    if task == "differentiate":
        return sp.diff(P(p["expr"]), v)
    if task == "derivative_at":
        return sp.simplify(sp.diff(P(p["expr"]), v).subs(v, P(p["at"])))
    if task == "partial":
        return sp.diff(P(p["expr"]), v)
    if task == "gradient":
        e = P(p["expr"])
        return sp.Matrix([sp.diff(e, sp.Symbol("x")), sp.diff(e, sp.Symbol("y"))])
    if task == "expand":
        return sp.expand(P(p["expr"]))
    if task == "factor":
        return sp.factor(P(p["expr"]))
    if task == "simplify":
        return sp.simplify(sp.together(P(p["expr"])))
    if task == "solve":
        return sp.FiniteSet(*sp.solve(sp.Eq(P(p["expr"]), P(p.get("expr2", "0"))), v))
    if task == "evaluate":
        return sp.simplify(P(p["expr"]))
    if task == "compose":
        return sp.simplify(P(p["expr"]).subs(v, P(p["expr2"])))
    if task == "limit":
        return sp.limit(P(p["expr"]), v, P(p["at"]))
    if task == "gd_step":
        w = sp.Symbol(str(p.get("var", "w")))
        loss, w0, lr = P(p["expr"]), P(p["at"]), P(p["lr"])
        return sp.simplify(w0 - lr * sp.diff(loss, w).subs(w, w0))
    if task == "local_min_x":
        e = P(p["expr"])
        d1, d2 = sp.diff(e, v), sp.diff(e, v, 2)
        mins = [c for c in sp.solve(sp.Eq(d1, 0), v) if c.is_real and d2.subs(v, c) > 0]
        return sp.simplify(mins[0])
    raise ValueError("unknown task " + str(task))


def load_stored(it):
    kind, s = it.get("answer_kind"), it.get("answer_sympy")
    if kind == "set":
        return sp.FiniteSet(*[P(t) for t in str(s).split(",")])
    if kind == "vector":
        return sp.Matrix([P(t) for t in str(s).split(",")])
    return P(s)


def equal(a, b):
    try:
        if isinstance(a, sp.FiniteSet) or isinstance(b, sp.FiniteSet):
            return sp.FiniteSet(*a) == sp.FiniteSet(*b)
        if isinstance(a, sp.MatrixBase) or isinstance(b, sp.MatrixBase):
            return sp.simplify(sp.Matrix(a) - sp.Matrix(b)) == sp.zeros(*sp.Matrix(a).shape)
        return sp.simplify(sp.together(a - b)) == 0
    except Exception:
        return False


def main():
    gen = json.loads((ROOT / "data/items/generated_items.json").read_text())["items"]
    problems = []
    n_ok = 0
    for it in gen:
        iid = it["item_id"]
        chk = it.get("check") or {}
        task, params = chk.get("task"), chk.get("params") or {}
        try:
            mine = solve_spec(task, params)
        except Exception as e:
            problems.append((iid, "RECOMPUTE-FAIL", f"{type(e).__name__}: {e}"))
            continue
        # 1. stored answer_sympy must reload and equal my recomputation
        try:
            stored = load_stored(it)
        except Exception as e:
            problems.append((iid, "ANSWER-UNLOADABLE", f"{type(e).__name__}: {e}"))
            continue
        if not equal(mine, stored):
            problems.append((iid, "ANSWER-MISMATCH",
                             f"spec gives {mine}, stored answer_sympy is {stored}"))
            continue
        # 2. answer_latex must match the recomputed answer
        try:
            lat_reparse = sp.parse_latex if False else None
        except Exception:
            pass
        if sp.latex(mine) != it.get("answer_latex"):
            problems.append((iid, "LATEX-DRIFT",
                             f"latex(recomputed)={sp.latex(mine)!r} vs stored answer_latex="
                             f"{it.get('answer_latex')!r}"))
        else:
            n_ok += 1
    print(f"generated items: {len(gen)}")
    print(f"fully clean (answer + latex): {n_ok}")
    print(f"problems: {len(problems)}")
    for p in problems:
        print("  ", p[0], p[1], p[2][:200])
    json.dump(problems, open(ROOT / "scripts/audit/_claim1_problems.json", "w"), indent=1)


if __name__ == "__main__":
    main()
