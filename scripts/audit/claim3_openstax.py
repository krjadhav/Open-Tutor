#!/usr/bin/env python3
"""Claim 3: do OpenStax answers belong to their questions, and does the number guard work?

Part A re-derives the id -> answer mapping from the cached HTML and audits the guard.
Part B sympy-verifies every derivative-style item it can parse.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache/openstax"
sys.path.insert(0, str(ROOT / "pipeline"))
from mathml import html_to_text  # noqa: E402

RAW = json.loads((ROOT / "data/items/raw_items.json").read_text())["items"]
ITEMS = json.loads((ROOT / "data/items/items.json").read_text())["items"]

SECT_TO_ANS = {
    "2-2-the-limit-of-a-function": "chapter_2", "2-3-the-limit-laws": "chapter_2",
    "2-4-continuity": "chapter_2", "3-1-defining-the-derivative": "chapter_3",
    "3-3-differentiation-rules": "chapter_3",
    "3-5-derivatives-of-trigonometric-functions": "chapter_3",
    "3-6-the-chain-rule": "chapter_3", "3-8-implicit-differentiation": "chapter_3",
    "3-9-derivatives-of-exponential-and-logarithmic-functions": "chapter_3",
    "4-3-maxima-and-minima": "chapter_4", "4-3-partial-derivatives": "chapter_4_v3",
    "4-5-the-chain-rule": "chapter_4_v3",
    "4-6-directional-derivatives-and-the-gradient": "chapter_4_v3",
}


def answers_of(page):
    soup = BeautifulSoup((CACHE / (page + ".html")).read_text(), "html.parser")
    out = {}
    for node in soup.select('div[data-type=solution][id$="-solution"]'):
        ex_id = node.get("id")[: -len("-solution")]
        num_el = node.select_one(".os-number")
        out[ex_id] = num_el.get_text(strip=True) if num_el else None
    return out


def main():
    print("=== PART A: the number cross-check guard ===")
    ans_pages = {p: answers_of(p) for p in set(SECT_TO_ANS.values())}
    byid = {r["openstax_id"]: r for r in RAW}

    stats = Counter()
    guard_should_have_fired, guard_skipped = [], []
    for r in RAW:
        page = SECT_TO_ANS.get(r["section"])
        if not page:
            stats["no-answer-page"] += 1
            continue
        amap = ans_pages[page]
        if r["openstax_id"] not in amap:
            stats["no matching solution element"] += 1
            continue
        akey_num, q_num = amap[r["openstax_id"]], r["number"]
        if akey_num is None or q_num is None:
            stats["guard SKIPPED (a number was missing)"] += 1
            guard_skipped.append((r["openstax_id"], q_num, akey_num, r["has_answer"]))
        elif akey_num != q_num:
            stats["numbers disagree"] += 1
            if r["has_answer"]:
                guard_should_have_fired.append((r["openstax_id"], q_num, akey_num))
        else:
            stats["numbers agree"] += 1
    for k, v in stats.most_common():
        print(f"  {v:5d}  {k}")
    print(f"\n  guard leaked a mismatched answer into the bank: {len(guard_should_have_fired)}")
    for g in guard_should_have_fired[:10]:
        print("    ", g)
    print(f"  guard could not run (a number was missing): {len(guard_skipped)}")
    for g in guard_skipped[:10]:
        print("    ", g)

    # odd-numbered check: OpenStax publishes answers for odd exercises only
    print("\n-- exercise parity of items that carry an answer --")
    par = Counter()
    evens = []
    for r in RAW:
        if not r["has_answer"]:
            continue
        n = re.sub(r"\D", "", r["number"] or "")
        if not n:
            par["no number"] += 1
            continue
        if int(n) % 2:
            par["odd"] += 1
        else:
            par["even"] += 1
            evens.append((r["openstax_id"], r["number"]))
    print(" ", dict(par))
    if evens:
        print("  EVEN-numbered exercises carrying a published answer:", evens[:15])

    print("\n=== PART B: sympy verification of derivative-style items ===")
    from sympy.parsing.latex import parse_latex
    import sympy as sp

    x = sp.Symbol("x")
    checked = ok = wrong = unparse = 0
    problems = []
    for it in ITEMS:
        if it["source"] != "openstax":
            continue
        stem, ans = it["stem_latex"], str(it.get("answer_latex") or "")
        # "find f'(x)" style: a single f(x)=... definition and a bare-expression answer
        m = re.search(r"\$\s*([a-zA-Z])\s*\(\s*x\s*\)\s*=\s*(.+?)\s*\.?\$", stem)
        if not m or "'" in stem.split("$")[-1]:
            pass
        if not m:
            continue
        if not re.search(r"find\s+\$?[a-z]\^?\{?'|find\s+\\frac\{dy\}\{dx\}|derivative", stem, re.I):
            continue
        body = m.group(2)
        aa = ans.strip().strip("$").strip()
        aa = re.sub(r"^[a-zA-Z]\^?\{?'\}?\s*\(x\)\s*=\s*", "", aa)
        aa = re.sub(r"^\\frac\{dy\}\{dx\}\s*=\s*", "", aa)
        for pat, rep in ((r"\\text\{(sin|cos|tan|sec|csc|cot|ln|log)\}", r"\\\1"),
                         (r"\\text\{/\}", "/"), ("\u2212", "-")):
            body = re.sub(pat, rep, body)
            aa = re.sub(pat, rep, aa)
        try:
            f = parse_latex(body)
            a = parse_latex(aa)
        except Exception:
            unparse += 1
            continue
        if x not in f.free_symbols:
            continue
        checked += 1
        try:
            same = sp.simplify(sp.diff(f, x) - a) == 0
        except Exception:
            same = None
        if same is True:
            ok += 1
        else:
            wrong += 1
            problems.append((it["item_id"], it["node_id"], body[:70], aa[:70],
                             str(sp.diff(f, x))[:70]))
    print(f"  parsed and CAS-compared : {checked}")
    print(f"  answer verified correct : {ok}")
    print(f"  answer NOT verified     : {wrong}")
    print(f"  latex unparseable       : {unparse}")
    for p in problems:
        print("   ", p[0], "|", p[1])
        print("      stem f =", p[2])
        print("      published answer =", p[3])
        print("      sympy d/dx       =", p[4])


if __name__ == "__main__":
    main()
