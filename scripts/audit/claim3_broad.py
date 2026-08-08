#!/usr/bin/env python3
"""Claim 3 broad sweep: unanswerable stems, unchecked answers, and CAS-verified partials/limits."""
import json
import re
from collections import Counter
from pathlib import Path

import sympy as sp
from sympy.parsing.latex import parse_latex

ROOT = Path(__file__).resolve().parents[2]
ITEMS = [i for i in json.loads((ROOT / "data/items/items.json").read_text())["items"]
         if i["source"] == "openstax"]
RAW = {r["openstax_id"]: r for r in json.loads((ROOT / "data/items/raw_items.json").read_text())["items"]}
x, y, z, t, u, v = sp.symbols("x y z t u v")


def clean(s):
    s = str(s)
    s = re.sub(r"\\text\{\s*(sin|cos|tan|sec|csc|cot|ln|log|lim)\s*\}", r"\\\1", s)
    s = s.replace("\\text{-}", "-").replace("\u2212", "-").replace("·", "*")
    s = re.sub(r"\\text\{\s*/\s*\}", "/", s)
    s = re.sub(r"\\text\{[^}]*\}", " ", s)
    return s.strip().strip("$").strip().rstrip(".").rstrip(",").strip()


print(f"OpenStax items in the bank: {len(ITEMS)}")

print("\n=== A. items whose answer was accepted with NO number cross-check ===")
unchecked = [i for i in ITEMS if RAW.get(i["item_id"][3:], {}).get("number") is None]
print(f"  {len(unchecked)} of {len(ITEMS)} ({100*len(unchecked)/len(ITEMS):.0f}%) carry a null "
      f"exercise number, so the guard never ran on them")

print("\n=== B. stems that reference an undefined function or a missing table ===")
UNDEF = re.compile(r"\b(f|g|h)\s*\(\s*[a-z0-9]\s*\)")
bad = []
for i in ITEMS:
    s = i["stem_latex"]
    # a stem that uses f/g/h without ever defining them
    names = set(re.findall(r"([fgh])\s*\(", s))
    defined = set(re.findall(r"([fgh])\s*\([^)]*\)\s*=", s))
    undef = names - defined
    if undef and not re.search(r"following table|graph", s, re.I):
        bad.append((i["item_id"], i["node_id"], sorted(undef), s[:130]))
print(f"  {len(bad)} items use an undefined f/g/h")
for b in bad:
    print("   ", b[0], b[1], b[2], "|", b[3])

print("\n=== C. CAS check of partial-derivative items ===")
rows = []
for i in ITEMS:
    s, a = i["stem_latex"], str(i.get("answer_latex") or "")
    if i["node_id"] not in ("mv.partial-derivative", "mv.functions-several-vars"):
        continue
    m = re.search(r"\$\s*(?:[fgz]\s*\(\s*x\s*,\s*y\s*\)|z)\s*=\s*(.+?)\s*\.?\s*\$", s)
    ma = re.search(r"\\frac\{\\partial f\}\{\\partial x\}\s*=\s*([^,$]+)", a)
    if not (m and ma):
        continue
    try:
        f = parse_latex(clean(m.group(1))).subs(sp.Symbol("e"), sp.E)
        ax = parse_latex(clean(ma.group(1))).subs(sp.Symbol("e"), sp.E)
    except Exception:
        rows.append((i["item_id"], "UNPARSEABLE", m.group(1)[:60], ma.group(1)[:60]))
        continue
    ok = False
    for fn in (lambda q: q, sp.trigsimp, sp.simplify):
        try:
            if sp.simplify(fn(sp.expand(sp.diff(f, x) - ax))) == 0:
                ok = True
                break
        except Exception:
            pass
    rows.append((i["item_id"], "OK" if ok else "MISMATCH", m.group(1)[:60], ma.group(1)[:60],
                 str(sp.diff(f, x))[:60]))
print(" ", dict(Counter(r[1] for r in rows)), "of", len(rows))
for r in rows:
    if r[1] != "OK":
        print("   ", r)

print("\n=== D. CAS check of direct-substitution limits ===")
rows = []
for i in ITEMS:
    s, a = i["stem_latex"], str(i.get("answer_latex") or "")
    m = re.search(r"lim\}?_\{x\\to\s*(-?\{?-?[\d.]+\}?)\}\s*(.+?)\s*\.?\s*\$", clean(s))
    if not m or i["node_id"] not in ("lim.direct-substitution", "lim.indeterminate-factoring"):
        continue
    pt = m.group(1).strip("{}")
    try:
        e = parse_latex(clean(m.group(2))).subs(sp.Symbol("e"), sp.E)
        lim = sp.limit(e, x, sp.sympify(pt))
        av = parse_latex(clean(a).split("=")[-1]).subs(sp.Symbol("e"), sp.E)
    except Exception as ex:
        rows.append((i["item_id"], "UNPARSEABLE", m.group(2)[:50], a[:50]))
        continue
    ok = sp.simplify(lim - av) == 0
    rows.append((i["item_id"], "OK" if ok else "MISMATCH", m.group(2)[:50], a[:50], str(lim)[:40]))
print(" ", dict(Counter(r[1] for r in rows)), "of", len(rows))
for r in rows:
    if r[1] != "OK":
        print("   ", r)
