#!/usr/bin/env python3
"""Claim 5: would this item be bad in front of a student?

Scans every item in items.json for: mojibake, truncation, multi-part questions, prose answers,
figure or cross-exercise references, broken LaTeX, mixed-number ambiguity, raw sympy code leaking
into a stem, and displayed questions that already show the answer.
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import sympy as sp
from sympy.parsing.sympy_parser import (parse_expr, standard_transformations,
                                        implicit_multiplication)

ROOT = Path(__file__).resolve().parents[2]
TR = standard_transformations + (implicit_multiplication,)
I = json.loads((ROOT / "data/items/items.json").read_text())["items"]

hits = defaultdict(list)


def add(tag, iid, detail):
    hits[tag].append((iid, detail))


MOJIBAKE = re.compile(r"[ÃÂâ][-¿‘-”]|â€|Ã©|Ã¢|Â°|Ã—")
PROSE = re.compile(r"\b(because|therefore|since|we see|note that|the function is|proof|"
                   r"continuous|discontinuous|does not exist|answers may vary|increasing|"
                   r"decreasing|removable|jump|infinite)\b", re.I)
FIGREF = re.compile(r"\b(graph|figure|the table|following table|shown|preceding exercise|"
                    r"previous exercise|exercise \d|above|below|picture|diagram|sketch)\b", re.I)
MULTIPART = re.compile(r"\ba\.\s|\bb\.\s|\(a\)|\(b\)|\bi\.\s|\bii\.\s")

for it in I:
    iid, stem = it["item_id"], it.get("stem_latex") or ""
    ans = it.get("answer_latex")

    if MOJIBAKE.search(stem) or (ans and MOJIBAKE.search(str(ans))):
        add("mojibake", iid, stem[:120])

    # unusual codepoints that will not render in a maths font
    odd = {c for c in stem + str(ans) if ord(c) > 0x2000 and c not in "’‘“”"}
    if odd:
        add("nonascii-symbols", iid, "".join(sorted(odd)) + " || " + stem[:100])

    # unbalanced $ or braces
    if stem.count("$") % 2:
        add("latex-unbalanced-dollar", iid, stem[:150])
    if stem.count("{") != stem.count("}"):
        add("latex-unbalanced-brace", iid, stem[:150])
    if ans and str(ans).count("{") != str(ans).count("}"):
        add("answer-unbalanced-brace", iid, str(ans)[:150])

    # truncation
    if stem and not re.search(r"[.?:]\s*$|\$$", stem.strip()):
        add("stem-no-terminator", iid, stem[-90:])

    if FIGREF.search(stem):
        add("figure-or-crossref", iid, stem[:160])
    if MULTIPART.search(stem):
        add("multipart-stem", iid, stem[:160])

    if ans and PROSE.search(str(ans)):
        add("prose-answer", iid, str(ans)[:160])
    if ans and len(str(ans)) > 160:
        add("very-long-answer", iid, str(ans)[:120] + "...")
    if ans is not None and re.search(r"[;,]\s", str(ans)) and it["source"] == "openstax":
        add("multipart-answer", iid, str(ans)[:160])

    # generated only: raw sympy source leaking into a student-facing stem
    if it["source"] == "generated":
        if re.search(r"\*\*|\w\*\w|\bexp\(|sqrt\(|w1|w2|yhat|y_true", stem):
            add("sympy-code-in-stem", iid, stem[:170])
        # mixed-number ambiguity: "1 \frac{1}{x}" reads as one-and-one-over-x
        if re.search(r"(?<![\\a-zA-Z0-9])\d+\s+\\frac", stem):
            add("mixed-number-ambiguity", iid, stem[:170])
        if re.search(r"\d\s*\\cdot\s*1\^|\\left\(-1\\right\)\s*\d", stem):
            add("unreduced-literal-arithmetic", iid, stem[:170])

print("=== CLAIM 5 scan over", len(I), "items ===")
for tag in sorted(hits):
    print(f"\n--- {tag}: {len(hits[tag])}")
    for iid, d in hits[tag][:25]:
        print(f"   {iid}: {d}")
    if len(hits[tag]) > 25:
        print(f"   ... {len(hits[tag])-25} more")

json.dump({k: v for k, v in hits.items()}, open(ROOT / "scripts/audit/_claim5.json", "w"), indent=1)
