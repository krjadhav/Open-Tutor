#!/usr/bin/env python3
"""Claim 4: independent recount of the bank."""
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
items = json.loads((ROOT / "data/items/items.json").read_text())
gen = json.loads((ROOT / "data/items/generated_items.json").read_text())
tag = json.loads((ROOT / "data/items/tagged_items.json").read_text())
raw = json.loads((ROOT / "data/items/raw_items.json").read_text())
nodes = json.loads((ROOT / "data/graph/nodes.json").read_text())["nodes"]
nid = [n["id"] for n in nodes]

I = items["items"]
print("items.json total      :", len(I))
print("declared counts       :", items.get("counts"))
print("actual by source      :", Counter(i["source"] for i in I))
print("generated_items.json  :", len(gen["items"]))
print("tagged_items.json     :", len(tag["items"]))
print("raw_items.json        :", len(raw["items"]), "declared total", raw.get("total"),
      "with_answers", raw.get("with_answers"))
print("graph nodes           :", len(nid))

print("\n-- item_id uniqueness --")
ids = Counter(i["item_id"] for i in I)
dupids = {k: v for k, v in ids.items() if v > 1}
print("duplicate item_ids:", len(dupids), list(dupids.items())[:10])

print("\n-- node coverage --")
c = Counter(i["node_id"] for i in I)
unknown = [k for k in c if k not in nid]
print("node_ids not in graph:", unknown)
empty = [n for n in nid if c.get(n, 0) == 0]
thin = [n for n in nid if 0 < c.get(n, 0) < 3]
print("empty nodes:", len(empty), empty)
print("thin nodes (<3):", len(thin), thin)

print("\n-- per node counts --")
by = defaultdict(lambda: Counter())
for i in I:
    by[i["node_id"]][i["source"]] += 1
for n in nid:
    print(f"  {n:<32} {c.get(n,0):3d}  gen={by[n]['generated']:3d} os={by[n]['openstax']:3d}")

print("\n-- tagged pipeline reconciliation --")
T = tag["items"]
print("tagged declared counts:", tag.get("counts"))
tag_err = [t for t in T if t.get("tag_error")]
checkable = [t for t in T if t.get("answer_is_checkable") and t.get("node_id")]
print("tag_error:", len(tag_err), "checkable+node:", len(checkable))
print("openstax items in items.json:", sum(1 for i in I if i["source"] == "openstax"))

print("\n-- raw reconciliation --")
R = raw["items"]
print("has_answer:", sum(1 for r in R if r["has_answer"]))
print("has_answer & not needs_figure:", sum(1 for r in R if r["has_answer"] and not r["needs_figure"]))
print("needs_figure & has_answer:", sum(1 for r in R if r["has_answer"] and r["needs_figure"]))
osid = Counter(r["openstax_id"] for r in R)
print("duplicate openstax_ids in raw:", sum(1 for v in osid.values() if v > 1))
