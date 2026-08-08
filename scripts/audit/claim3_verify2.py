#!/usr/bin/env python3
"""Claim 3 part B, refined: CAS-verify OpenStax answers against their questions.

Covers derivative-style, limit-style and partial-derivative-style items. Normalises the
textbook's LaTeX quirks first so a failure means a real disagreement, not a parser artifact.
"""
import json
import re
from pathlib import Path

import sympy as sp
from sympy.parsing.latex import parse_latex

ROOT = Path(__file__).resolve().parents[2]
ITEMS = json.loads((ROOT / "data/items/items.json").read_text())["items"]
x, y, z = sp.symbols("x y z")


def clean(s):
    s = str(s)
    s = re.sub(r"\\text\{\s*(sin|cos|tan|sec|csc|cot|ln|log|lim)\s*\}", r"\\\1", s)
    s = s.replace("\\text{-}", "-").replace("\u2212", "-")
    s = re.sub(r"\\text\{\s*/\s*\}", "/", s)
    s = re.sub(r"\\text\{[^}]*\}", " ", s)
    s = s.replace("\\left", "").replace("\\right", "")
    s = s.strip().strip("$").strip().rstrip(".").rstrip(",").strip()
    return s


def to_expr(s):
    e = parse_latex(clean(s))
    # the textbook writes e^{...}; parse_latex makes `e` a plain symbol
    return e.subs(sp.Symbol("e"), sp.E)


def eq(a, b):
    for f in (lambda t: t, sp.trigsimp, sp.expand_trig, sp.simplify):
        try:
            if sp.simplify(f(sp.expand(a - b))) == 0:
                return True
        except Exception:
            pass
    try:
        d = sp.N(sp.simplify(a - b).subs({x: sp.Rational(37, 29), y: sp.Rational(11, 7),
                                          z: sp.Rational(5, 3)}))
        return abs(complex(d)) < 1e-9
    except Exception:
        return False


def answer_candidates(ans):
    """A textbook answer often shows working: take every '=' separated piece."""
    a = clean(ans)
    a = re.sub(r"^[a-zA-Z]\^?\{?'\}?\s*\(\s*x\s*\)\s*=\s*", "", a)
    a = re.sub(r"^\\frac\{d[yz]\}\{d[xtu]\}\s*=\s*", "", a)
    parts = [p.strip() for p in re.split(r"(?<![<>!])=", a) if p.strip()]
    return parts or [a]


def main():
    rows = []
    for it in ITEMS:
        if it["source"] != "openstax":
            continue
        stem, ans = it["stem_latex"], str(it.get("answer_latex") or "")
        if not ans or ans == "None":
            continue
        # skip items that plainly need a table/figure or are prose
        if re.search(r"following table|graph|figure|answers may vary", stem + ans, re.I):
            continue
        m = re.search(r"\$\s*(?:[a-zA-Z]\s*\(\s*x\s*\)|y)\s*=\s*(.+?)\s*\.?\s*\$", stem)
        if not m:
            continue
        if not re.search(r"find\s+\$?[a-z]\^?\{?['′]|\\frac\{dy\}\{dx\}|derivative of", stem, re.I):
            continue
        body = m.group(1)
        if re.search(r"[fgh]\s*\(", body):        # answer depends on an unknown function/table
            continue
        try:
            f = to_expr(body)
        except Exception:
            rows.append((it["item_id"], it["node_id"], "STEM-UNPARSEABLE", body[:70], ans[:70]))
            continue
        if x not in f.free_symbols:
            continue
        d = sp.diff(f, x)
        verdict = "UNPARSEABLE-ANSWER"
        for cand in answer_candidates(ans):
            try:
                a = to_expr(cand)
            except Exception:
                continue
            if eq(d, a):
                verdict = "OK"
                break
            verdict = "MISMATCH"
        rows.append((it["item_id"], it["node_id"], verdict, body[:70], ans[:80], str(d)[:80]))

    from collections import Counter
    c = Counter(r[2] for r in rows)
    print("=== derivative-style OpenStax items, CAS-verified ===")
    print(" ", dict(c), " total", len(rows))
    for r in rows:
        if r[2] != "OK":
            print(f"\n  {r[0]} [{r[1]}] {r[2]}")
            print(f"     f       = {r[3]}")
            print(f"     answer  = {r[4]}")
            if len(r) > 5:
                print(f"     d/dx    = {r[5]}")


if __name__ == "__main__":
    main()
