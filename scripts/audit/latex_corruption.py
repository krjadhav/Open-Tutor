#!/usr/bin/env python3
"""Quantify LaTeX corruption produced by the MathML converter."""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
I = json.loads((ROOT / "data/items/items.json").read_text())["items"]
RAW = json.loads((ROOT / "data/items/raw_items.json").read_text())["items"]

PATTERNS = {
    r"\partia l  (partial split by a space)": re.compile(r"\\partia\s+l"),
    r"\p i  (pi split by a space)": re.compile(r"\\p\s+i"),
    r"\text{sin|cos|tan|sec|lim|ln} instead of \sin etc": re.compile(
        r"\\text\{\s*(sin|cos|tan|sec|csc|cot|lim|ln|log)\s*\}"),
    r"\text{/} for a slash": re.compile(r"\\text\{\s*/\s*\}"),
    "bare unicode minus U+2212": re.compile("\u2212"),
    "bare unicode nabla U+2207": re.compile("\u2207"),
    "bare unicode partial U+2202": re.compile("\u2202"),
    "angle brackets U+3008/9": re.compile("[\u3008\u3009]"),
    "therefore U+2234": re.compile("\u2234"),
    "ellipsis U+2026 in maths": re.compile("\u2026"),
}

print(f"{'pattern':<52} {'stems':>6} {'answers':>8} {'items':>6}")
for name, pat in PATTERNS.items():
    s = sum(1 for i in I if pat.search(i.get("stem_latex") or ""))
    a = sum(1 for i in I if pat.search(str(i.get("answer_latex") or "")))
    both = sum(1 for i in I if pat.search((i.get("stem_latex") or "") + str(i.get("answer_latex") or "")))
    print(f"{name:<52} {s:6d} {a:8d} {both:6d}")

corrupt = [i for i in I
           if re.search(r"\\partia\s+l|\\p\s+i", (i.get("stem_latex") or "") + str(i.get("answer_latex") or ""))]
print(f"\nitems with a split control sequence (\\partia l / \\p i): {len(corrupt)}")
for c in corrupt:
    print("  ", c["item_id"], "|", c["node_id"])

print("\n-- unbalanced braces / dollars --")
bad = []
for i in I:
    s = i.get("stem_latex") or ""
    a = str(i.get("answer_latex") or "")
    why = []
    if s.count("$") % 2:
        why.append("odd $ in stem")
    if s.count("{") != s.count("}"):
        why.append(f"brace {s.count('{')}/{s.count('}')} in stem")
    if a.count("{") != a.count("}"):
        why.append(f"brace {a.count('{')}/{a.count('}')} in answer")
    if why:
        bad.append((i["item_id"], i["node_id"], ", ".join(why)))
print(len(bad))
for b in bad:
    print("  ", b)

print("\n-- group instruction mis-attachment (openstax) --")
# the instruction names a function/topic the stem never mentions
sus = []
for r in RAW:
    gi = r.get("group_instruction")
    if not gi:
        continue
    m = re.search(r"consider the function \$([^$]+)\$", gi)
    if m:
        fn = m.group(1)
        if fn not in r["stem_raw"]:
            sus.append((r["openstax_id"], gi[:80], r["stem_raw"][:80]))
print(f"instructions naming a function absent from the stem: {len(sus)}")
for s in sus[:20]:
    print("  ", s[0], "|", s[1], "||", s[2])

gi_counts = Counter(r.get("group_instruction") for r in RAW if r.get("group_instruction"))
print(f"\ndistinct group instructions reattached: {len(gi_counts)}")
for k, v in gi_counts.most_common(12):
    print(f"  {v:3d}  {k[:110]}")
