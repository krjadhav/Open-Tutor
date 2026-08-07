#!/usr/bin/env python3
"""
Experiment 2: can Sarvam-105B attribute a student's error to the right knowledge-graph node?

This is the go/no-go test for the whole product thesis. If the model cannot reliably say
"your chain rule is fine, you dropped a negative sign", there is no blame propagation and
the pitch needs reframing.

Usage:
    export SARVAM_API_KEY=...
    python3 run.py                 # one pass over all cases
    python3 run.py --repeats 3     # stability check, majority vote reported
    python3 run.py --dry-run       # print the prompt for one case, no API call
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
GRAPH_PATH = ROOT / "data" / "graph" / "nodes.json"
CASES_PATH = HERE / "cases.json"
RESULTS_PATH = HERE / "results.json"

API_URL = "https://api.sarvam.ai/v1/chat/completions"
MODEL = "sarvam-105b"


# ---------------------------------------------------------------- graph checks

def validate_dag(nodes):
    """Cheap sanity pass. An LLM-built graph will have cycles; a hand-built one can too."""
    ids = {n["id"] for n in nodes}
    problems = []

    for n in nodes:
        for p in n.get("prereqs", []) + n.get("encompasses", []):
            if p["id"] not in ids:
                problems.append(f"{n['id']} references unknown node {p['id']}")

    colour = {}

    def visit(nid, stack):
        if colour.get(nid) == "done":
            return
        if colour.get(nid) == "open":
            problems.append("cycle: " + " -> ".join(stack + [nid]))
            return
        colour[nid] = "open"
        node = next((x for x in nodes if x["id"] == nid), None)
        if node:
            for p in node.get("prereqs", []):
                if p["id"] in ids:
                    visit(p["id"], stack + [nid])
        colour[nid] = "done"

    for n in nodes:
        visit(n["id"], [])
    return problems


# ---------------------------------------------------------------- prompt

SYSTEM_PROMPT = """You are the diagnostic engine of a mathematics tutoring system.

You are given a problem, a student's handwritten working (as transcribed by OCR, so it may be
slightly noisy), and the list of knowledge-graph nodes the system tracks.

Your job is to identify the single node responsible for the student's error.

Rules:
1. The problem is tagged to a topic node, but the error is frequently NOT in that topic. Most
   calculus errors are algebra, sign, fraction or trigonometry errors. Blame the node that is
   actually at fault, even when it is a prerequisite the student was assumed to know.
2. Equally, do not shift blame downward when the surface topic genuinely is the problem. If the
   student misapplied the rule itself, blame the rule.
3. If the working is mathematically correct, return null for blamed_node, even if it is
   unsimplified or written in a different form from the expected answer. Never invent an error.
4. blamed_node MUST be one of the node ids listed. Do not invent ids.

Respond with a single JSON object and nothing else. No markdown fences, no commentary.

{
  "correct": true | false,
  "failed_step": "quote the exact line of the student's work where it first goes wrong, or null",
  "blamed_node": "<node id>" | null,
  "misconception_tag": "<short-kebab-case-label>" | null,
  "student_message": "<one or two sentences addressed to the student: what they got right, then what went wrong>",
  "confidence": 0.0 to 1.0
}"""

USER_TEMPLATE = """AVAILABLE NODES
{node_list}

PROBLEM (tagged to node: {item_node})
{problem}

STUDENT'S WORK (OCR transcription)
{student_work}

EXPECTED ANSWER
{correct_answer}

Diagnose. JSON only."""


def build_messages(case, nodes):
    node_list = "\n".join(f"  {n['id']}: {n['title']}" for n in nodes)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(
            node_list=node_list,
            item_node=case["item_node"],
            problem=case["problem"],
            student_work=case["student_work"],
            correct_answer=case["correct_answer"],
        )},
    ]


# ---------------------------------------------------------------- api

def extract_json(text):
    """Sarvam has no documented JSON mode, so parse defensively.

    Whatever we learn here applies verbatim to production: the same parser will sit in the
    diagnosis path.
    """
    if not text:
        return None, "empty response"
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        pass
    # first balanced object
    start = cleaned.find("{")
    if start == -1:
        return None, "no json object found"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start:i + 1]), None
                except json.JSONDecodeError as e:
                    return None, f"malformed json: {e}"
    return None, "unbalanced braces"


def salvage_json(text):
    """Pull the fields out of a truncated JSON object.

    Sarvam runs thinking mode by default and reasoning_content is billed against max_tokens,
    so a tight budget yields finish_reason=length with the object cut off mid-string. The
    blamed_node is emitted early, so it usually survives. Production needs this same repair
    path, which is why it lives here rather than in a notebook.
    """
    if not text:
        return None
    out = {}
    m = re.search(r'"blamed_node"\s*:\s*(?:null|"([^"]*)")', text)
    if not m:
        return None
    out["blamed_node"] = m.group(1)  # None when the literal null matched
    m = re.search(r'"correct"\s*:\s*(true|false)', text)
    if m:
        out["correct"] = m.group(1) == "true"
    m = re.search(r'"confidence"\s*:\s*([0-9.]+)', text)
    if m:
        out["confidence"] = float(m.group(1))
    for field in ("failed_step", "misconception_tag", "student_message"):
        m = re.search(rf'"{field}"\s*:\s*(?:null|"((?:[^"\\]|\\.)*)")', text)
        if m:
            out[field] = m.group(1)
    out["_salvaged"] = True
    return out


def call_model(messages, api_key, max_tokens, reasoning_effort, max_retries=3):
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort

    last = None
    for attempt in range(max_retries):
        try:
            t0 = time.time()
            r = requests.post(API_URL, headers=headers, json=payload, timeout=180)
            latency = time.time() - t0
            if r.status_code == 200:
                body = r.json()
                choice = body["choices"][0]
                msg = choice.get("message", {})
                usage = body.get("usage", {}) or {}
                meta = {
                    "finish_reason": choice.get("finish_reason"),
                    "reasoning_content": msg.get("reasoning_content"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                }
                return msg.get("content"), latency, None, meta
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            return None, 0, last, {}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    return None, 0, last, {}


# ---------------------------------------------------------------- scoring

def score_one(case, parsed, valid_ids):
    """Returns a dict of booleans/labels for one (case, response) pair."""
    out = {
        "case_id": case["id"],
        "blame_type": case["blame_type"],
        "true_node": case["true_node"],
        "item_node": case["item_node"],
    }
    if parsed is None:
        out.update(predicted=None, verdict="PARSE_FAIL", confidence=None)
        return out

    pred = parsed.get("blamed_node")
    if isinstance(pred, str) and pred.strip().lower() in ("null", "none", ""):
        pred = None
    out["predicted"] = pred
    out["confidence"] = parsed.get("confidence")
    out["misconception_tag"] = parsed.get("misconception_tag")
    out["student_message"] = parsed.get("student_message")
    out["model_says_correct"] = parsed.get("correct")

    if pred is not None and pred not in valid_ids:
        out["verdict"] = "HALLUCINATED_ID"
        return out

    if case["blame_type"] == "control":
        # must report no error
        out["verdict"] = "OK" if pred is None else "FALSE_POSITIVE"
        return out

    if pred is None:
        out["verdict"] = "MISSED_ERROR"
    elif pred == case["true_node"]:
        out["verdict"] = "OK"
    elif pred in case.get("acceptable_nodes", []):
        out["verdict"] = "OK_ACCEPTABLE"
    elif pred == case["item_node"] and case["blame_type"] == "prereq":
        out["verdict"] = "BLAMED_SURFACE_TOPIC"   # the exact failure mode we are testing for
    else:
        out["verdict"] = "WRONG_NODE"
    return out


GOOD = {"OK", "OK_ACCEPTABLE"}


def summarise(rows, repeats):
    print("\n" + "=" * 78)
    print("PER-CASE RESULT" + (f"  (majority over {repeats} runs)" if repeats > 1 else ""))
    print("=" * 78)
    print(f"{'case':<28} {'type':<8} {'expected':<26} {'got':<26} verdict")
    print("-" * 78)
    for r in rows:
        exp = r["true_node"] or "(no error)"
        got = r["predicted"] or "(no error)"
        mark = "PASS" if r["verdict"] in GOOD else "FAIL"
        print(f"{r['case_id']:<28} {r['blame_type']:<8} {exp:<26} {got:<26} {mark} {r['verdict']}")

    errs = [r for r in rows if r["blame_type"] != "control"]
    ctrls = [r for r in rows if r["blame_type"] == "control"]
    prereq = [r for r in errs if r["blame_type"] == "prereq"]
    topic = [r for r in errs if r["blame_type"] == "topic"]

    def rate(rs):
        return sum(1 for r in rs if r["verdict"] in GOOD), len(rs)

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for label, rs in [
        ("Overall (error cases)", errs),
        ("  prereq-blame  (must look past the topic)", prereq),
        ("  topic-blame   (must not over-shift)", topic),
        ("Controls (must report no error)", ctrls),
    ]:
        n, d = rate(rs)
        pct = f"{100*n/d:.0f}%" if d else "n/a"
        print(f"{label:<45} {n}/{d}  {pct}")

    print("\nFailure modes:")
    for verdict, count in Counter(r["verdict"] for r in rows if r["verdict"] not in GOOD).most_common():
        print(f"  {verdict:<24} {count}")
    if not any(r["verdict"] not in GOOD for r in rows):
        print("  none")

    confs = [(r["confidence"], r["verdict"] in GOOD) for r in rows if isinstance(r.get("confidence"), (int, float))]
    if confs:
        ok = [c for c, g in confs if g]
        bad = [c for c, g in confs if not g]
        print("\nConfidence calibration:")
        print(f"  mean confidence when right : {sum(ok)/len(ok):.2f} (n={len(ok)})" if ok else "  no correct cases")
        print(f"  mean confidence when wrong : {sum(bad)/len(bad):.2f} (n={len(bad)})" if bad else "  no wrong cases")
        if ok and bad and sum(ok)/len(ok) - sum(bad)/len(bad) > 0.15:
            print("  -> separable. A confidence threshold can gate blame propagation.")
        elif ok and bad:
            print("  -> NOT separable. Confidence is not usable as a gate; act on blame only when it is cheap to be wrong.")

    n, d = rate(errs)
    print("\n" + "=" * 78)
    if d and n / d >= 0.7:
        print(f"GO. {n}/{d} on error cases. Blame propagation is real; build it.")
    elif d:
        print(f"NO-GO as specified. {n}/{d} on error cases (threshold was 7/10).")
        print("Read the failure modes above before reframing. Often the fix is the prompt, not the idea.")
    print("=" * 78)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1, help="runs per case; majority vote reported")
    ap.add_argument("--dry-run", action="store_true", help="print one prompt and exit")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=4000,
                    help="Sarvam bills reasoning_content against this. Too low and content comes back empty.")
    ap.add_argument("--reasoning", default=None, choices=["low", "medium", "high"],
                    help="reasoning_effort. Omit for the model default. The API rejects 'none', so "
                         "thinking cannot be disabled, only turned down: 'low' is the latency floor.")
    ap.add_argument("--tag", default="", help="label for this run, used in the results filename")
    args = ap.parse_args()

    graph = json.loads(GRAPH_PATH.read_text())
    nodes = graph["nodes"]
    cases = json.loads(CASES_PATH.read_text())["cases"]
    valid_ids = {n["id"] for n in nodes}

    print(f"Graph: {len(nodes)} nodes, target {graph['target_node']}")
    problems = validate_dag(nodes)
    if problems:
        print("GRAPH PROBLEMS:")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("Graph validates as a DAG with no dangling references.")

    # sanity: every ground-truth node exists
    for c in cases:
        for nid in [c["true_node"], c["item_node"]] + c.get("acceptable_nodes", []):
            if nid is not None and nid not in valid_ids:
                print(f"CASE ERROR: {c['id']} references unknown node {nid}")
                sys.exit(1)
    print(f"Cases: {len(cases)} ({sum(1 for c in cases if c['blame_type']=='prereq')} prereq-blame, "
          f"{sum(1 for c in cases if c['blame_type']=='topic')} topic-blame, "
          f"{sum(1 for c in cases if c['blame_type']=='control')} control)")

    if args.dry_run:
        msgs = build_messages(cases[0], nodes)
        print("\n--- SYSTEM ---\n" + msgs[0]["content"])
        print("\n--- USER ---\n" + msgs[1]["content"])
        return

    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        print("\nSARVAM_API_KEY is not set. Run:  export SARVAM_API_KEY=...")
        sys.exit(2)

    jobs = [(c, r) for c in cases for r in range(args.repeats)]
    print(f"\nRunning {len(jobs)} calls against {MODEL} ...")

    def work(job):
        case, run_idx = job
        msgs = build_messages(case, nodes)
        content, latency, err, meta = call_model(
            msgs, api_key, args.max_tokens, args.reasoning)

        # Starter tier caps max_tokens at 4096 and thinking is billed against it, so a hard
        # case can burn the whole budget reasoning and return empty content. We cannot buy more
        # tokens, so trade reasoning depth for them instead: retry once at reasoning_effort=low.
        retried = False
        if not err and (meta.get("finish_reason") == "length" or not (content or "").strip()):
            retried = True
            c2, l2, e2, m2 = call_model(msgs, api_key, args.max_tokens, "low")
            if not e2 and (c2 or "").strip():
                content, latency, meta = c2, latency + l2, m2

        if err:
            return {"case": case, "run": run_idx, "error": err, "parsed": None,
                    "raw": None, "latency": 0, "meta": {}, "retried": retried}
        parsed, perr = extract_json(content)
        if parsed is None:
            recovered = salvage_json(content)
            if recovered is not None:
                parsed, perr = recovered, f"salvaged after: {perr}"
        return {"case": case, "run": run_idx, "error": perr, "parsed": parsed,
                "raw": content, "latency": latency, "meta": meta, "retried": retried}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        raw_results = list(ex.map(work, jobs))

    api_errors = [r for r in raw_results if r["parsed"] is None]
    if api_errors:
        print(f"\n{len(api_errors)} call(s) failed or returned unparseable output:")
        for r in api_errors[:5]:
            print(f"  {r['case']['id']}: {r['error']}")

    # Transport health, separate from diagnosis quality. These are different problems.
    truncated = [r for r in raw_results if (r.get("meta") or {}).get("finish_reason") == "length"]
    salvaged = [r for r in raw_results if (r.get("parsed") or {}).get("_salvaged")]
    rtoks = [m for m in ((r.get("meta") or {}).get("reasoning_tokens") for r in raw_results) if m]
    print("\nTransport health")
    print(f"  hit the token ceiling (finish_reason=length) : {len(truncated)}/{len(raw_results)}")
    print(f"  recovered by the salvage parser              : {len(salvaged)}/{len(raw_results)}")
    if rtoks:
        print(f"  reasoning tokens: mean {sum(rtoks)/len(rtoks):.0f}, max {max(rtoks)}"
              f"  (billed against max_tokens={args.max_tokens})")
    if truncated:
        print(f"  -> raise --max-tokens or pass --reasoning none")

    # majority vote per case
    by_case = {}
    for r in raw_results:
        by_case.setdefault(r["case"]["id"], []).append(r)

    rows = []
    for case in cases:
        runs = by_case[case["id"]]
        scored = [score_one(case, r["parsed"], valid_ids) for r in runs]
        if args.repeats == 1:
            rows.append(scored[0])
        else:
            preds = Counter(s["predicted"] for s in scored)
            winner = preds.most_common(1)[0][0]
            pick = next(s for s in scored if s["predicted"] == winner)
            pick["agreement"] = f"{preds[winner]}/{len(scored)}"
            rows.append(pick)

    latencies = [r["latency"] for r in raw_results if r["latency"]]
    if latencies:
        print(f"\nLatency: mean {sum(latencies)/len(latencies):.1f}s, max {max(latencies):.1f}s")
        if max(latencies) > 12:
            print("  -> too slow to block the UI on. Diagnose asynchronously and reveal when ready.")

    summarise(rows, args.repeats)

    out_path = HERE / (f"results{'-' + args.tag if args.tag else ''}.json")
    out_path.write_text(json.dumps({
        "model": MODEL,
        "repeats": args.repeats,
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.reasoning,
        "rows": rows,
        "raw": [{"case_id": r["case"]["id"], "run": r["run"], "raw": r["raw"],
                 "error": r["error"], "latency": r["latency"], "meta": r.get("meta")}
                for r in raw_results],
    }, indent=2))
    print(f"\nFull output written to {out_path}")
    print("Read the student_message fields in results.json. Accuracy is necessary but the")
    print("message is what the judge actually sees, so it has to read like a good tutor.")


if __name__ == "__main__":
    main()
