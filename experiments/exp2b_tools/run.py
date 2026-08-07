#!/usr/bin/env python3
"""
Experiment 2b: can forced tool-calling and `seed` harden the diagnosis path?

Experiment 2 lost at least one case to JSON parsing in every single run, and the same case
flipped verdict between runs at temperature 0. Both are fixable at the API level rather than
with more prompt engineering, and both sit directly under the feature the product is built on.

Three questions, one run:
  1. Does forcing a tool call eliminate the PARSE_FAIL class entirely?
  2. Does constraining blamed_node to an enum of real node ids kill hallucinated ids?
  3. Does `seed` make the diagnosis reproducible, so the demo does not ride a coin flip?

Passes A and B are identical (same seed). Any disagreement between them is nondeterminism.
Pass C uses a different seed, which confirms the parameter is actually honoured rather than
silently ignored.

Usage:
    export SARVAM_API_KEY=...
    python3 run.py
"""

import argparse
import importlib.util
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP2 = HERE.parent / "exp2_diagnosis"

_spec = importlib.util.spec_from_file_location("exp2_diagnosis_run", EXP2 / "run.py")
exp2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exp2)

GRAPH = json.loads((HERE.parent.parent / "data" / "graph" / "nodes.json").read_text())
NODES = GRAPH["nodes"]
NODE_IDS = [n["id"] for n in NODES]
VALID_IDS = set(NODE_IDS)
CASES = json.loads((EXP2 / "cases.json").read_text())["cases"]

TOOL = {
    "type": "function",
    "function": {
        "name": "report_diagnosis",
        "description": "Report which knowledge-graph node is responsible for the student's error.",
        "parameters": {
            "type": "object",
            "properties": {
                "correct": {
                    "type": "boolean",
                    "description": "True if the student's working is mathematically correct, "
                                   "including when it is unsimplified or in a different form.",
                },
                "failed_step": {
                    "type": ["string", "null"],
                    "description": "The exact line of the student's work where it first goes wrong.",
                },
                "blamed_node": {
                    # The enum is the point: the model cannot invent a node id, so the
                    # HALLUCINATED_ID failure mode becomes structurally impossible.
                    "type": ["string", "null"],
                    "enum": NODE_IDS + [None],
                    "description": "The node at fault. Null if the working is correct. This is "
                                   "often a prerequisite rather than the problem's own topic.",
                },
                "misconception_tag": {"type": ["string", "null"]},
                "student_message": {
                    "type": "string",
                    "description": "One or two sentences to the student: what they got right, "
                                   "then what went wrong.",
                },
                "confidence": {"type": "number"},
            },
            "required": ["correct", "blamed_node", "student_message", "confidence"],
        },
    },
}

SYSTEM = """You are the diagnostic engine of a mathematics tutoring system.

You are given a problem, a student's handwritten working (as transcribed by OCR, so it may be
slightly noisy), and the knowledge-graph nodes the system tracks.

Identify the single node responsible for the student's error, then call report_diagnosis.

Rules:
1. The problem is tagged to a topic node, but the error is frequently NOT in that topic. Most
   calculus errors are algebra, sign, fraction or trigonometry errors. Blame the node actually at
   fault, even when it is a prerequisite the student was assumed to know.
2. Do not shift blame downward when the surface topic genuinely is the problem. If the student
   misapplied the rule itself, blame the rule.
3. If the working is mathematically correct, set correct=true and blamed_node=null, even if it is
   unsimplified or written in a different form from the expected answer. Never invent an error."""


def render_nodes():
    """blame_hint is prompt-only. Broad titles attract blame belonging to other nodes, and
    appending a one-line discriminator measurably fixes it (c09 went from 0/3 to 3/3)."""
    out = []
    for n in NODES:
        line = f"  {n['id']}: {n['title']}"
        if n.get("blame_hint"):
            line += f" [{n['blame_hint']}]"
        out.append(line)
    return "\n".join(out)


def build(case):
    node_list = render_nodes()
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": exp2.USER_TEMPLATE.format(
            node_list=node_list, item_node=case["item_node"], problem=case["problem"],
            student_work=case["student_work"], correct_answer=case["correct_answer"],
        ).replace("Diagnose. JSON only.", "Diagnose, then call report_diagnosis.")},
    ]


def call_tool(client, case, seed, max_tokens, retries=3):
    """Returns (parsed_args, latency, error, meta)."""
    last = None
    for attempt in range(retries):
        try:
            t0 = time.time()
            r = client.chat.completions(
                messages=build(case),
                model="sarvam-105b",
                temperature=0,
                seed=seed,
                max_tokens=max_tokens,
                tools=[TOOL],
                tool_choice={"type": "function", "function": {"name": "report_diagnosis"}},
            )
            lat = time.time() - t0
            d = r.model_dump() if hasattr(r, "model_dump") else r
            choice = (d.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            meta = {"finish_reason": choice.get("finish_reason")}
            calls = msg.get("tool_calls") or []
            if not calls:
                # fall back to whatever text came back, so a missing tool call is visible
                # as its own failure mode rather than being hidden as a parse error
                txt = msg.get("content")
                parsed, _ = exp2.extract_json(txt)
                return parsed, lat, ("no tool_call returned" if parsed is None
                                     else "no tool_call, recovered from content"), meta
            args = (calls[0].get("function") or {}).get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError as e:
                    salvaged = exp2.salvage_json(args)
                    return salvaged, lat, f"tool args not valid json: {e}", meta
            return args, lat, None, meta
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    return None, 0, last, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed-a", type=int, default=42)
    ap.add_argument("--seed-c", type=int, default=1234)
    args = ap.parse_args()

    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        print("SARVAM_API_KEY is not set.")
        sys.exit(2)
    from sarvamai import SarvamAI
    client = SarvamAI(api_subscription_key=key)

    passes = [("A", args.seed_a), ("B", args.seed_a), ("C", args.seed_c)]
    jobs = [(name, seed, case) for name, seed in passes for case in CASES]
    print(f"{len(CASES)} cases x 3 passes (seeds {args.seed_a}, {args.seed_a}, {args.seed_c}) "
          f"= {len(jobs)} calls, forced tool call\n")

    def work(job):
        name, seed, case = job
        parsed, lat, err, meta = call_tool(client, case, seed, args.max_tokens)
        scored = exp2.score_one(case, parsed, VALID_IDS)
        return {"pass": name, "seed": seed, "case_id": case["id"], "scored": scored,
                "error": err, "latency": lat, "meta": meta, "args": parsed}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        res = list(ex.map(work, jobs))

    by = {p: {r["case_id"]: r for r in res if r["pass"] == p} for p, _ in passes}

    # ---- 1. structured output reliability
    errs = [r for r in res if r["error"]]
    print("=" * 78)
    print("1. STRUCTURED OUTPUT")
    print("=" * 78)
    print(f"clean tool calls           : {len(res) - len(errs)}/{len(res)}")
    if errs:
        for e in Counter(r["error"] for r in errs).most_common():
            print(f"  {e[1]}x  {e[0]}")
    halluc = [r for r in res if r["scored"]["verdict"] == "HALLUCINATED_ID"]
    pf = [r for r in res if r["scored"]["verdict"] == "PARSE_FAIL"]
    print(f"hallucinated node ids      : {len(halluc)}/{len(res)}  (enum-constrained)")
    print(f"PARSE_FAIL                 : {len(pf)}/{len(res)}")
    print("experiment 2 baseline      : 1 to 8 parse failures per 12-call run")

    # ---- 2. determinism
    print("\n" + "=" * 78)
    print("2. DETERMINISM")
    print("=" * 78)
    same_seed_match = diff_seed_match = 0
    for c in CASES:
        cid = c["id"]
        a, b, cc = by["A"][cid]["scored"], by["B"][cid]["scored"], by["C"][cid]["scored"]
        ab = a["predicted"] == b["predicted"]
        ac = a["predicted"] == cc["predicted"]
        same_seed_match += ab
        diff_seed_match += ac
        flag = "" if ab else "   <-- SAME SEED, DIFFERENT ANSWER"
        print(f"{cid:<30} A={str(a['predicted']):<26} B={'same' if ab else str(b['predicted']):<10}{flag}")
    n = len(CASES)
    print(f"\nsame seed  ({args.seed_a} vs {args.seed_a}) agreement : {same_seed_match}/{n}")
    print(f"diff seed  ({args.seed_a} vs {args.seed_c}) agreement : {diff_seed_match}/{n}")
    if same_seed_match == n:
        print("-> seed gives reproducible diagnoses. Demo output can be trusted to repeat.")
    else:
        print("-> seed does NOT pin the output. Pre-cache anything shown on stage.")

    # ---- 3. accuracy
    print("\n" + "=" * 78)
    print("3. ACCURACY  (vs experiment 2: 83% error cases, 4/6 controls)")
    print("=" * 78)
    tot = ok = ctot = cok = 0
    for c in CASES:
        cid = c["id"]
        verdicts = [by[p][cid]["scored"]["verdict"] for p, _ in passes]
        good = sum(1 for v in verdicts if v in exp2.GOOD)
        if c["blame_type"] == "control":
            ctot += 3
            cok += good
        else:
            tot += 3
            ok += good
        marks = " ".join("P" if v in exp2.GOOD else "F" for v in verdicts)
        note = "" if good == 3 else "  " + ",".join(sorted(set(v for v in verdicts if v not in exp2.GOOD)))
        print(f"{cid:<30} {c['blame_type']:<8} {marks}  {good}/3{note}")
    print(f"\nerror cases : {ok}/{tot} = {100*ok/tot:.0f}%")
    print(f"controls    : {cok}/{ctot} = {100*cok/ctot:.0f}%")

    lat = [r["latency"] for r in res if r["latency"]]
    print(f"\nlatency: mean {sum(lat)/len(lat):.1f}s, max {max(lat):.1f}s")

    out = HERE / "results.json"
    out.write_text(json.dumps({"passes": [{"name": p, "seed": s} for p, s in passes],
                               "results": res}, indent=2, default=str))
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
