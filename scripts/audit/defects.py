#!/usr/bin/env python3
"""Consolidated defect tally: how many items are actually fit to show a student?"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
I = json.loads((ROOT / "data/items/items.json").read_text())["items"]
RAW = {r["openstax_id"]: r for r in json.loads((ROOT / "data/items/raw_items.json").read_text())["items"]}

CMDS = ['partial', 'pi', 'infty', 'sin', 'cos', 'tan', 'sec', 'csc', 'cot', 'ln', 'log', 'sqrt',
        'frac', 'lim', 'theta', 'alpha', 'beta', 'le', 'ge', 'neq', 'pm', 'to', 'cdot', 'Delta',
        'nabla']


def split_cmd(t):
    for m in re.finditer(r"\\([a-zA-Z]+)\s+([a-zA-Z])(?![a-zA-Z])", t):
        if m.group(1) + m.group(2) in CMDS and m.group(1) not in CMDS:
            return m.group(0)
    return None


PROSE = re.compile(r"answers may vary|it is continuous|does not exist|^DNE$|^None$|^Nowhere$|"
                   r"discontinuit|absolute (maximum|minimum)|the sign is|the partial derivative is|"
                   r"occurs at|removable|jump", re.I)
FIG = re.compile(r"following table|the graph|graph of|figure|preceding exercise|"
                 r"two exercises|tree diagram|shown", re.I)
MULTI = re.compile(r"(^|[\s$;])[a-d]\.\s|\(i\)|\(ii\)")

defects = defaultdict(list)
for it in I:
    iid, stem, ans = it["item_id"], it["stem_latex"] or "", str(it.get("answer_latex") or "")
    raw = RAW.get(iid[3:], {}) if it["source"] == "openstax" else {}

    if split_cmd(stem + " " + ans):
        defects["broken LaTeX: control sequence split by a space"].append(iid)
    if stem.count("$") % 2 or stem.count("{") != stem.count("}"):
        defects["broken LaTeX: unbalanced $ or braces in the stem"].append(iid)
    if FIG.search(stem):
        defects["references a figure, table or another exercise"].append(iid)
    if PROSE.search(ans.strip()):
        defects["answer is prose, not an expression"].append(iid)
    if MULTI.search(ans):
        defects["multi-part answer (a./b./c.)"].append(iid)
    if re.search(r"^(show that|prove|create a tree|explain)", stem.strip(), re.I) or \
       re.search(r"\bshow that\b", stem, re.I):
        defects["asks for a proof or construction, not an expression"].append(iid)
    if raw and raw.get("number") is None:
        defects["answer accepted with no exercise-number cross-check"].append(iid)
    if raw.get("group_instruction") and \
       re.search(r"consider the function \$([^$]+)\$", raw["group_instruction"]) and \
       re.search(r"consider the function \$([^$]+)\$", raw["group_instruction"]).group(1) not in raw["stem_raw"]:
        defects["group instruction belongs to a different exercise group"].append(iid)
    if it["source"] == "generated":
        if re.search(r"\*\*|\bexp\(|sqrt\(|w1|w2|y_true|\^2\b", stem.split("Find")[0]):
            defects["raw sympy source shown to the student"].append(iid)
        if re.search(r"(?<![\\a-zA-Z0-9])\d+\s+\\frac", stem):
            defects["mixed-number ambiguity in the rendered stem"].append(iid)
        if re.search(r"\d{6,}", str(it.get("answer_sympy"))):
            defects["answer is a 15-digit float the student must type exactly"].append(iid)

dups = defaultdict(list)
for it in I:
    dups[it["stem_latex"]].append(it)
for k, v in dups.items():
    if len(v) > 1:
        if len({str(i["answer_latex"]) for i in v}) > 1:
            for i in v:
                defects["identical stem, CONTRADICTORY answers"].append(i["item_id"])
        else:
            for i in v:
                defects["exact duplicate stem"].append(i["item_id"])

print("=== defect classes ===")
for k in sorted(defects, key=lambda k: -len(defects[k])):
    print(f"  {len(defects[k]):4d}  {k}")
    print(f"        {sorted(set(defects[k]))[:8]}")

affected = set()
for v in defects.values():
    affected |= set(v)
print(f"\ndistinct items carrying at least one defect: {len(affected)} of {len(I)}")
byn = Counter(i["node_id"] for i in I if i["item_id"] in affected)
tot = Counter(i["node_id"] for i in I)
print("\n=== clean items per node ===")
print(f"{'node':<32} {'total':>5} {'defective':>10} {'clean':>6}")
for n in sorted(tot):
    print(f"{n:<32} {tot[n]:5d} {byn.get(n,0):10d} {tot[n]-byn.get(n,0):6d}")
thin = [(n, tot[n] - byn.get(n, 0)) for n in sorted(tot) if tot[n] - byn.get(n, 0) < 3]
print(f"\nnodes with fewer than 3 defect-free items: {len(thin)}")
for t in thin:
    print("  ", t)
json.dump({k: sorted(set(v)) for k, v in defects.items()},
          open(ROOT / "scripts/audit/_defects.json", "w"), indent=1)
