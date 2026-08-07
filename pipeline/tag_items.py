#!/usr/bin/env python3
"""Tag raw OpenStax exercises onto knowledge-graph nodes with sarvam-105b.

This is the half of "textbook conversion" the product actually consumes. Graph extraction is a
pitch slide; this is required, because nothing downstream works without items bound to nodes.

Uses the pattern proven in experiment 2b: a forced tool call with node_id constrained to an enum
of real ids, which made invalid values structurally impossible (0/36) and ran at ~3s per call.

    export SARVAM_API_KEY=...
    python3 tag_items.py --limit 40      # try a sample first
    python3 tag_items.py                 # full run
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "data" / "graph" / "nodes.json").read_text())
NODES = GRAPH["nodes"]
NODE_IDS = [n["id"] for n in NODES]
RAW = ROOT / "data" / "items" / "raw_items.json"
OUT = ROOT / "data" / "items" / "tagged_items.json"

TOOL = {
    "type": "function",
    "function": {
        "name": "tag_exercise",
        "description": "Assign a calculus exercise to the knowledge-graph node it primarily trains.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "enum": NODE_IDS,
                    "description": "The single node this exercise primarily trains.",
                },
                "encompasses": {
                    "type": "array",
                    "items": {"type": "string", "enum": NODE_IDS},
                    "description": "Other nodes a correct solution necessarily exercises. These "
                                   "earn partial implicit credit, so list only genuine component "
                                   "skills, not every loosely related topic. Usually 1 to 3.",
                },
                "difficulty": {
                    "type": "number",
                    "description": "-2 (trivial drill) to +2 (hard multi-step). Most routine "
                                   "exercises sit between -1 and +1.",
                },
                "answer_is_checkable": {
                    "type": "boolean",
                    "description": "True only if the answer is a single expression or value a "
                                   "computer algebra system could compare. False for proofs, "
                                   "explanations, sketches, or multi-part answers.",
                },
                "confidence": {"type": "number"},
            },
            "required": ["node_id", "encompasses", "difficulty", "answer_is_checkable", "confidence"],
        },
    },
}

SYSTEM = """You classify calculus exercises against a fixed knowledge graph.

Assign the node the exercise PRIMARILY trains: the skill a student must have to solve it, not
every topic it touches. A chain rule problem trains the chain rule even though it also uses the
power rule; the power rule belongs in `encompasses`.

Be strict about `answer_is_checkable`. Our grader is a computer algebra system, so anything that
is a proof, an explanation, a sketch, an interval description, or a multi-part answer is NOT
checkable. When in doubt, say false."""


def build(item, node_lines):
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"""AVAILABLE NODES
{node_lines}

SECTION: {item['section']} ({item['section_hint']})

EXERCISE
{item['stem_latex']}

PUBLISHED ANSWER
{item.get('answer_latex')}

Classify it, then call tag_exercise."""},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        print("SARVAM_API_KEY is not set.")
        sys.exit(2)
    from sarvamai import SarvamAI
    client = SarvamAI(api_subscription_key=key)

    node_lines = "\n".join(
        f"  {n['id']}: {n['title']}" + (f" [{n['blame_hint']}]" if n.get("blame_hint") else "")
        for n in NODES)

    raw = json.loads(RAW.read_text())
    items = [i for i in raw["items"] if i["has_answer"] and not i["needs_figure"]]
    if args.limit:
        items = items[:args.limit]
    print(f"tagging {len(items)} items\n")

    def work(item):
        for attempt in range(3):
            try:
                r = client.chat.completions(
                    messages=build(item, node_lines), model="sarvam-105b",
                    temperature=0, max_tokens=1500,
                    tools=[TOOL],
                    tool_choice={"type": "function", "function": {"name": "tag_exercise"}},
                )
                d = r.model_dump() if hasattr(r, "model_dump") else r
                msg = (d.get("choices") or [{}])[0].get("message") or {}
                calls = msg.get("tool_calls") or []
                if not calls:
                    return {**item, "tag_error": "no tool call"}
                a = (calls[0].get("function") or {}).get("arguments")
                if isinstance(a, str):
                    a = json.loads(a)
                return {**item, "node_id": a.get("node_id"),
                        "encompasses": [e for e in (a.get("encompasses") or []) if e != a.get("node_id")],
                        "difficulty_b": a.get("difficulty"),
                        "answer_is_checkable": a.get("answer_is_checkable"),
                        "tag_confidence": a.get("confidence")}
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    return {**item, "tag_error": f"{type(e).__name__}: {e}"}
                time.sleep(2 ** attempt)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        tagged = list(ex.map(work, items))
    print(f"done in {time.time()-t0:.0f}s\n")

    failed = [t for t in tagged if t.get("tag_error")]
    ok = [t for t in tagged if not t.get("tag_error")]
    checkable = [t for t in ok if t.get("answer_is_checkable")]
    low = [t for t in ok if (t.get("tag_confidence") or 0) < 0.75]

    print(f"tagged            : {len(ok)}/{len(tagged)}")
    print(f"CAS-checkable     : {len(checkable)}")
    print(f"low confidence    : {len(low)}  (review these by hand)")
    if failed:
        print(f"failed            : {len(failed)}")

    print("\nitems per node (checkable only):")
    counts = Counter(t["node_id"] for t in checkable)
    for n in NODES:
        c = counts.get(n["id"], 0)
        bar = "#" * min(c, 40)
        flag = "   <-- NO ITEMS" if c == 0 else ("   <-- thin" if c < 3 else "")
        print(f"  {n['id']:<32} {c:3d} {bar}{flag}")

    OUT.write_text(json.dumps({
        "licence": raw["licence"], "attribution": raw["attribution"],
        "counts": {"tagged": len(ok), "checkable": len(checkable), "low_confidence": len(low)},
        "items": tagged,
    }, indent=2))
    print(f"\nwritten to {OUT}")

    empty = [n["id"] for n in NODES if counts.get(n["id"], 0) == 0]
    if empty:
        print(f"\n{len(empty)} nodes have no checkable items. Every one of these needs generated")
        print("drills before it can appear in a daily set:")
        for e in empty:
            print(f"  {e}")


if __name__ == "__main__":
    main()
