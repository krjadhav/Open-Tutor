"""What the proposed edge changes do to depth, the critical path and the XP economy.

XP = XP_BASE * node_depth * novelty * quality (engine/xp.py), so prereq depth is money.
Run: python3 scripts/audit/graph/impact.py
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
GRAPH = os.path.join(ROOT, "data", "graph", "nodes.json")

# (node, prereq) edges the audit proposes to REMOVE
REMOVE = [
    ("der.chain-rule", "der.product-rule"),
    ("der.quotient-rule", "der.product-rule"),
    ("der.power-rule", "der.definition"),
    ("der.trig-derivatives", "der.definition"),
    ("der.exp-log-derivatives", "der.definition"),
    ("ai.backprop-chain", "ai.gradient-descent-step"),
]

# (node, prereq, weight) edges the audit proposes to ADD
ADD = [
    ("der.chain-rule", "der.power-rule", 0.9),
    ("der.quotient-rule", "der.power-rule", 0.9),
    ("opt.critical-points", "der.power-rule", 0.9),
    ("opt.critical-points", "alg.factoring", 0.7),
    ("alg.solving-equations", "alg.factoring", 0.6),
    ("der.implicit", "der.product-rule", 0.8),
    ("der.implicit", "alg.solving-equations", 0.7),
    ("der.higher-order", "der.constant-multiple-sum", 0.7),
    ("der.definition", "alg.sign-distribution", 0.6),
    ("ai.gradient-descent-step", "opt.local-extrema", 0.6),
    ("ai.gradient-of-loss", "der.exp-log-derivatives", 0.5),
]


def depths(prereqs, by_id):
    cache = {}

    def d(nid, stack=()):
        if nid in cache:
            return cache[nid]
        if nid in stack:
            return 0
        best = 0
        for pid in prereqs[nid]:
            if pid in by_id and pid != nid:
                best = max(best, d(pid, stack + (nid,)))
        cache[nid] = best + 1
        return cache[nid]

    for i in by_id:
        d(i)
    return cache


def chain(prereqs, by_id, nid):
    best, out = 0, []
    for pid in prereqs[nid]:
        if pid in by_id:
            c = chain(prereqs, by_id, pid)
            if len(c) > best:
                best, out = len(c), c
    return out + [nid]


def ancestors(prereqs, by_id, target):
    acc, stack = set(), [target]
    while stack:
        cur = stack.pop()
        for pid in prereqs[cur]:
            if pid in by_id and pid not in acc:
                acc.add(pid)
                stack.append(pid)
    return acc


def main():
    doc = json.load(open(GRAPH))
    nodes = doc["nodes"]
    by_id = {n["id"]: n for n in nodes}
    target = doc["target_node"]

    before = {n["id"]: [e["id"] for e in n["prereqs"]] for n in nodes}
    after = {k: list(v) for k, v in before.items()}
    for nid, pid in REMOVE:
        if pid in after[nid]:
            after[nid].remove(pid)
    for nid, pid, _w in ADD:
        if pid not in after[nid]:
            after[nid].append(pid)

    db, da = depths(before, by_id), depths(after, by_id)

    print("=== depth, before -> after proposed edge changes ===")
    for nid in sorted(by_id, key=lambda i: (-db[i], i)):
        mark = "" if db[nid] == da[nid] else f"   <-- {da[nid] - db[nid]:+d}"
        print(f"  {nid:32s} b={by_id[nid]['difficulty_b']:+.1f}  depth {db[nid]:2d} -> {da[nid]:2d}{mark}")

    print(f"\nmax depth: {max(db.values())} -> {max(da.values())}")
    print(f"depth of target {target}: {db[target]} -> {da[target]}")

    print("\n=== critical path to the target, after ===")
    for s in chain(after, by_id, target):
        print(f"    {s}")

    ab, aa = ancestors(before, by_id, target), ancestors(after, by_id, target)
    print(f"\n=== nodes required for the target: {len(ab)+1} -> {len(aa)+1} of {len(by_id)} ===")
    print(f"  newly required: {sorted(aa - ab)}")
    print(f"  still off the goal path: {sorted(set(by_id) - aa - {target})}")

    print("\n=== XP economy: XP = XP_BASE * depth * novelty * quality (engine/xp.py) ===")
    print("pairs where the DEEPER node is also the EASIER one, so the easier node pays more XP:")
    pairs = []
    for i in by_id:
        for j in by_id:
            gap = by_id[i]["difficulty_b"] - by_id[j]["difficulty_b"]
            if db[i] > db[j] and gap < -0.15:
                pairs.append((db[i] - db[j], gap, i, j))
    pairs.sort(key=lambda r: (r[1], -r[0]))
    print(f"  {len(pairs)} such pairs. Worst 8 by difficulty gap:")
    for dd, gap, i, j in pairs[:8]:
        print(f"    {i:28s} depth {db[i]:2d} b={by_id[i]['difficulty_b']:+.1f} pays {db[i]:2d}x   "
              f"vs {j:26s} depth {db[j]:2d} b={by_id[j]['difficulty_b']:+.1f} pays {db[j]:2d}x")

    print("\n  same check after the proposed edge fixes:")
    pairs2 = [(da[i] - da[j], by_id[i]["difficulty_b"] - by_id[j]["difficulty_b"], i, j)
              for i in by_id for j in by_id
              if da[i] > da[j] and by_id[i]["difficulty_b"] - by_id[j]["difficulty_b"] < -0.15]
    print(f"    {len(pairs)} -> {len(pairs2)} inverted pairs")


if __name__ == "__main__":
    main()
