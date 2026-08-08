"""Independent structural checks on data/graph/nodes.json.

Deliberately does NOT import engine/ so that the checks are independent of the code under audit.
Run: python3 scripts/audit/graph/structure.py
"""

import json
import os
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
GRAPH = os.path.join(ROOT, "data", "graph", "nodes.json")

CREDIT_LO, CREDIT_HI = 0.2, 0.4


def load():
    with open(GRAPH) as fh:
        return json.load(fh)


def main():
    doc = load()
    nodes = doc["nodes"]
    target = doc["target_node"]
    ids = [n["id"] for n in nodes]
    by_id = {n["id"]: n for n in nodes}

    print(f"nodes: {len(nodes)}  unique ids: {len(set(ids))}  target: {target}")

    # --- duplicates
    dupes = {i for i in ids if ids.count(i) > 1}
    print(f"\n[duplicate ids] {sorted(dupes) or 'none'}")

    # --- dangling / self loops
    dangling, selfloop = [], []
    for n in nodes:
        for rel in ("prereqs", "encompasses"):
            for e in n.get(rel, []):
                if e["id"] not in by_id:
                    dangling.append((n["id"], rel, e["id"]))
                if e["id"] == n["id"]:
                    selfloop.append((n["id"], rel))
    print(f"[dangling refs] {dangling or 'none'}")
    print(f"[self loops] {selfloop or 'none'}")

    # --- required fields
    missing = []
    for n in nodes:
        for f in ("id", "title", "kind", "difficulty_b", "prereqs", "encompasses"):
            if f not in n:
                missing.append((n["id"], f))
        if n.get("kind") not in ("skill", "concept"):
            missing.append((n["id"], f"bad kind {n.get('kind')!r}"))
        if "goal_tags" not in n:
            missing.append((n["id"], "goal_tags (design 4.1 field)"))
    print(f"[missing/invalid fields] {missing or 'none'}")

    prereqs = {n["id"]: [(e["id"], e["weight"]) for e in n.get("prereqs", [])] for n in nodes}

    # --- DAG check via Kahn
    indeg = {i: 0 for i in ids}
    children = defaultdict(list)     # prereq -> dependants
    for nid, ps in prereqs.items():
        for pid, _w in ps:
            if pid in by_id:
                children[pid].append(nid)
                indeg[nid] += 1
    q = deque([i for i in ids if indeg[i] == 0])
    topo = []
    seen_indeg = dict(indeg)
    while q:
        cur = q.popleft()
        topo.append(cur)
        for ch in children[cur]:
            seen_indeg[ch] -= 1
            if seen_indeg[ch] == 0:
                q.append(ch)
    print(f"\n[DAG] topo order covers {len(topo)}/{len(ids)} -> "
          f"{'ACYCLIC' if len(topo) == len(ids) else 'CYCLE PRESENT'}")

    roots = [i for i in ids if not prereqs[i]]
    print(f"[roots, no prereqs] {len(roots)}: {sorted(roots)}")

    leaves = [i for i in ids if not children[i]]
    print(f"[leaves, nothing depends on them] {len(leaves)}: {sorted(leaves)}")

    # --- depth = 1 + longest prereq chain
    depth = {}

    def d(nid, stack=()):
        if nid in depth:
            return depth[nid]
        if nid in stack:
            return 0
        best = 0
        for pid, _w in prereqs[nid]:
            if pid in by_id and pid != nid:
                best = max(best, d(pid, stack + (nid,)))
        depth[nid] = best + 1
        return depth[nid]

    for i in ids:
        d(i)

    print("\n[depths]")
    for lvl in sorted(set(depth.values())):
        row = sorted(i for i in ids if depth[i] == lvl)
        print(f"  depth {lvl}: {len(row):2d}  {row}")
    print(f"  max depth = {max(depth.values())}")

    # --- longest chain reconstruction
    def longest_chain(nid):
        best, chain = 0, []
        for pid, _w in prereqs[nid]:
            if pid in by_id:
                c = longest_chain(pid)
                if len(c) > best:
                    best, chain = len(c), c
        return chain + [nid]

    deepest = max(ids, key=lambda i: depth[i])
    print(f"\n[longest prereq chain] {depth[deepest]} nodes")
    for step in longest_chain(deepest):
        print(f"    {step}")

    # --- ancestors of target (nodes that are actually needed for the goal)
    anc = set()
    stack = [target]
    while stack:
        cur = stack.pop()
        for pid, _w in prereqs[cur]:
            if pid in by_id and pid not in anc:
                anc.add(pid)
                stack.append(pid)
    off_path = sorted(set(ids) - anc - {target})
    print(f"\n[on the goal path] {len(anc) + 1}/{len(ids)}")
    print(f"[NOT required for {target}] {len(off_path)}: {off_path}")

    # --- transitive prereq closure, for encompasses sanity
    trans = {}

    def closure(nid):
        if nid in trans:
            return trans[nid]
        acc = set()
        for pid, _w in prereqs[nid]:
            if pid in by_id:
                acc.add(pid)
                acc |= closure(pid)
        trans[nid] = acc
        return acc

    for i in ids:
        closure(i)

    print("\n[encompasses entries NOT in the transitive prereq closure]")
    any_bad = False
    for n in nodes:
        for e in n.get("encompasses", []):
            if e["id"] not in trans[n["id"]]:
                any_bad = True
                print(f"  {n['id']} -> {e['id']} (credit {e['credit']})")
    if not any_bad:
        print("  none")

    print("\n[credit weights outside 0.2..0.4]")
    bad = [(n["id"], e["id"], e["credit"]) for n in nodes for e in n.get("encompasses", [])
           if not (CREDIT_LO <= e["credit"] <= CREDIT_HI)]
    print(f"  {bad or 'none'}")

    print("\n[prereq weights outside 0..1]")
    badw = [(n["id"], e["id"], e["weight"]) for n in nodes for e in n.get("prereqs", [])
            if not (0.0 <= e["weight"] <= 1.0)]
    print(f"  {badw or 'none'}")

    print("\n[nodes with empty encompasses]")
    print(f"  {sorted(n['id'] for n in nodes if not n.get('encompasses'))}")

    # --- difficulty inversions
    print("\n[difficulty inversions: node easier than or equal to one of its own prereqs]")
    inv = []
    for n in nodes:
        for pid, w in prereqs[n["id"]]:
            if pid in by_id and n["difficulty_b"] <= by_id[pid]["difficulty_b"]:
                inv.append((n["id"], n["difficulty_b"], pid, by_id[pid]["difficulty_b"], w))
    for a, ab, b, bb, w in sorted(inv, key=lambda r: r[1] - r[3]):
        print(f"  {a} (b={ab}) <= prereq {b} (b={bb})  delta {ab - bb:+.1f}  prereq weight {w}")
    print(f"  total {len(inv)}")

    # --- difficulty vs depth correlation
    print("\n[difficulty by depth]")
    for lvl in sorted(set(depth.values())):
        row = [by_id[i]["difficulty_b"] for i in ids if depth[i] == lvl]
        print(f"  depth {lvl}: min {min(row):+.1f}  mean {sum(row)/len(row):+.2f}  max {max(row):+.1f}")

    # --- blame hints
    with_hint = sorted(n["id"] for n in nodes if n.get("blame_hint"))
    without = sorted(n["id"] for n in nodes if not n.get("blame_hint"))
    print(f"\n[blame_hint] {len(with_hint)}/{len(nodes)} present")
    print(f"  missing ({len(without)}): {without}")

    # --- fan-in / fan-out
    print("\n[fan-out: how many nodes each node gates]")
    for i in sorted(ids, key=lambda x: -len(children[x]))[:10]:
        print(f"  {i}: gates {len(children[i])} direct, prereq-count {len(prereqs[i])}")


if __name__ == "__main__":
    main()
