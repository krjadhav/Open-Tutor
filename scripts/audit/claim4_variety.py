#!/usr/bin/env python3
"""Claim 4: effective variety per node, not just counts.

Two measures:
  - template collapse: strip every number and reduce the stem to its shape. If a node's 8 items
    reduce to 1 shape, it is one drill printed 8 times.
  - near-duplicate pairs by token Jaccard within and across nodes.
"""
import json
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
I = json.loads((ROOT / "data/items/items.json").read_text())["items"]


def shape(s):
    s = re.sub(r"\$.*?\$", "<M>", s or "")          # collapse every maths span
    s = re.sub(r"\d+(\.\d+)?", "N", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def mathshape(s):
    """Shape of the maths itself: keep operators and function names, drop numerals/letters."""
    s = s or ""
    s = re.sub(r"\\(?:left|right)", "", s)
    s = re.sub(r"\d+", "N", s)
    s = re.sub(r"(?<![\\a-zA-Z])[a-zA-Z](?![a-zA-Z])", "v", s)
    s = re.sub(r"\s+", "", s)
    return s


def toks(s):
    return set(re.findall(r"[a-zA-Z\\]+|\d+", (s or "").lower()))


by = defaultdict(list)
for it in I:
    by[it["node_id"]].append(it)

print(f"{'node':<32} {'n':>3} {'shapes':>7} {'mathshapes':>11} {'variety':>8}")
weak = []
for n in sorted(by):
    items = by[n]
    sh = len({shape(i["stem_latex"]) for i in items})
    ms = len({mathshape(i["stem_latex"]) for i in items})
    v = ms / len(items)
    print(f"{n:<32} {len(items):3d} {sh:7d} {ms:11d} {v:8.2f}")
    if sh <= 2 and len(items) >= 5:
        weak.append((n, len(items), sh))

print("\n-- nodes where every item is the same sentence with different numbers --")
for w in weak:
    print("  ", w[0], f"{w[1]} items reduce to {w[2]} distinct stem shape(s)")
    for i in by[w[0]][:3]:
        print("      ", i["stem_latex"][:100])

print("\n=== exact duplicate stems (whole bank) ===")
c = Counter(i["stem_latex"] for i in I)
dups = {k: v for k, v in c.items() if v > 1}
print(f"  {len(dups)} stems appear more than once")
for k, v in dups.items():
    ids = [i["item_id"] for i in I if i["stem_latex"] == k]
    print(f"   x{v} {k[:110]}")
    print(f"        {ids}")

print("\n=== near-duplicate pairs (Jaccard >= 0.85) ===")
pairs = []
items = [(i["item_id"], i["node_id"], i["stem_latex"], toks(i["stem_latex"])) for i in I]
for a, b in combinations(items, 2):
    if not a[3] or not b[3]:
        continue
    j = len(a[3] & b[3]) / len(a[3] | b[3])
    if j >= 0.85:
        pairs.append((round(j, 2), a[0], b[0], a[1], b[1], a[2][:70], b[2][:70]))
pairs.sort(reverse=True)
print(f"  {len(pairs)} pairs")
samenode = [p for p in pairs if p[3] == p[4]]
print(f"  of which within the same node: {len(samenode)}")
for p in pairs[:40]:
    tag = "SAME-NODE" if p[3] == p[4] else "cross-node"
    print(f"   {p[0]} {tag} {p[1]} / {p[2]}")
    print(f"        {p[5]}")
    print(f"        {p[6]}")

print("\n=== identical spec expressions among generated items ===")
gen = json.loads((ROOT / "data/items/generated_items.json").read_text())["items"]
e = defaultdict(list)
for g in gen:
    e[(g["check"]["task"], g["check"]["params"].get("expr"))].append(g["item_id"])
for k, v in e.items():
    if len(v) > 1:
        print("  ", k, v)
