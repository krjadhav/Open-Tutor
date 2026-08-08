"""LLM diagnosis: which knowledge-graph node is responsible for this student's error?

**This module never decides whether the student is right.** `services.grading` does that, and
`diagnose` is only ever called after the CAS has already returned `correct=False`. The reason is
measured, not stylistic (learning-design.md section 14 finding 2): with prompt-enforced JSON the
model scored 83% on error cases but only 4/6 on the controls, and the worst single outcome in the
whole experiment was it inventing an `alg.sign-distribution` error inside `x - 5 + x + 2`, a
correct answer that was merely unsimplified. Gating on the CAS deletes that entire class of
failure for free. Section 16 later got controls to 6/6 with tool calling, which makes the model
less fragile but does not make the gate less free, so the gate stays.

The call itself is experiment 2b verbatim, because that configuration is the one with numbers
attached (section 16, 36 calls):

  - **Forced tool call.** `tool_choice` pins `report_diagnosis`. Parse failures went from 1 to 8
    per 12 calls to 0 of 36, and mean latency from 23s to 3.2s, apparently because the tool
    schema suppresses the long free-text reasoning that was eating the token budget.
  - **`blamed_node` constrained to an enum of the real node ids.** A hallucinated id stops being
    a thing that can happen, rather than a thing we detect afterwards.
  - **`blame_hint` rendered into the node list.** A prompt-only one-line discriminator per node,
    kept out of `title` so the UI still says "Power rule". Sharpening two of these moved case c09
    from 0/3 to 3/3 and overall accuracy from 83% to 90%.

What section 16 could NOT fix: **`seed` does not pin the output.** Two identical passes at the
same seed and temperature 0 agreed on only 11 of 12 cases. So anything shown on stage must be
pre-computed: `build_cache` populates `data/demo/diagnosis_cache.json` and `diagnose` serves from
it when `cache_path` is given.

One more thing worth carrying into the UI, from section 16: in the cases where the model routed
blame to the wrong node, its `student_message` was still right every time. The explanation is
more trustworthy than the routing. Show the message; treat the graph update as the softer half.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from engine.types import Graph, Item                      # noqa: E402

MODEL = "sarvam-105b"
DEFAULT_SEED = 42
DEFAULT_MAX_TOKENS = 4000
DEFAULT_CACHE = "data/demo/diagnosis_cache.json"

# --------------------------------------------------------------------------- the demo case
# Pinned here so the demo and the cache cannot drift apart: the cache key is derived from these
# exact strings. This is the climax of the walkthrough, the quotient rule is applied perfectly
# and the failure is pure algebra, so it must resolve to alg.sign-distribution every single time.

DEMO_ITEM = Item(
    item_id="demo-quotient-sign",
    node_id="der.quotient-rule",
    stem_latex=r"Differentiate $f(x) = \frac{2x + 1}{x - 3}$.",
    answer_latex=r"-\frac{7}{(x-3)^{2}}",
    answer_sympy="-7/(x - 3)**2",
    answer_kind="expr",
    difficulty_b=0.0,
    source="generated",
)
DEMO_PROBLEM = "Differentiate f(x) = (2x + 1) / (x - 3)."
DEMO_STUDENT_WORK = (
    "f'(x) = [ (2)(x-3) - (2x+1)(1) ] / (x-3)^2\n"
    "= [ 2x - 6 - 2x + 1 ] / (x-3)^2\n"
    "= -5 / (x-3)^2"
)
DEMO_EXPECTED_ANSWER = "-7/(x-3)^2"
DEMO_EXPECTED_NODE = "alg.sign-distribution"


# --------------------------------------------------------------------------- result

@dataclass(frozen=True)
class Diagnosis:
    """What the model reported, plus how we got it.

    `correct` is the model's opinion and is NOT authoritative. It is kept because it is a useful
    disagreement signal: the CAS said wrong, so a `correct=True` here means the model could not
    find the error and the message should be treated with suspicion.

    `confidence` is likewise not a gate. Section 14 finding 3 measured 0.97 when right against
    0.93 to 0.95 when wrong: not separable. Cap the blame update instead (BLAME_MAX_B_DELTA).

    `latency_s` on a cache hit is the latency that was recorded when the entry was built, not the
    microseconds the file read took. It is reported honestly alongside `from_cache=True`.
    """
    correct: bool
    failed_step: Optional[str]
    blamed_node: Optional[str]
    misconception_tag: Optional[str]
    student_message: str
    confidence: float
    latency_s: float
    from_cache: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict, *, from_cache: bool = False) -> "Diagnosis":
        return cls(
            correct=bool(d.get("correct", False)),
            failed_step=d.get("failed_step"),
            blamed_node=d.get("blamed_node"),
            misconception_tag=d.get("misconception_tag"),
            student_message=d.get("student_message") or "",
            confidence=float(d.get("confidence") or 0.0),
            latency_s=float(d.get("latency_s") or 0.0),
            from_cache=from_cache,
            error=d.get("error"),
        )


# --------------------------------------------------------------------------- prompt

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

USER_TEMPLATE = """AVAILABLE NODES
{node_list}

PROBLEM (tagged to node: {item_node})
{problem}

STUDENT'S WORK (OCR transcription)
{student_work}

EXPECTED ANSWER
{correct_answer}

Diagnose, then call report_diagnosis."""


def node_ids(graph: Graph) -> list[str]:
    """Deterministic id order. It becomes the tool enum, so it must not wobble between calls."""
    return list(graph.nodes.keys())


def render_nodes(graph: Graph) -> str:
    """One line per node, with the blame_hint appended.

    Section 16: broad titles attract blame belonging to other nodes. `alg.sign-distribution` was
    being read as a generic "sign error" bucket and swallowed a gradient-ascent error whole. The
    hint is prompt-only and deliberately not part of `title`, which the UI still shows.
    """
    lines = []
    for nid, node in graph.nodes.items():
        line = f"  {nid}: {node.get('title', nid)}"
        hint = node.get("blame_hint")
        if hint:
            line += f" [{hint}]"
        lines.append(line)
    return "\n".join(lines)


def build_tool(graph: Graph) -> dict:
    ids = node_ids(graph)
    return {
        "type": "function",
        "function": {
            "name": "report_diagnosis",
            "description": "Report which knowledge-graph node is responsible for the "
                           "student's error.",
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
                        "description": "The exact line of the student's work where it first goes "
                                       "wrong.",
                    },
                    "blamed_node": {
                        # The enum is the point: the model cannot invent a node id, so the
                        # HALLUCINATED_ID failure mode becomes structurally impossible.
                        "type": ["string", "null"],
                        "enum": ids + [None],
                        "description": "The node at fault. Null if the working is correct. This "
                                       "is often a prerequisite rather than the problem's own "
                                       "topic.",
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


def build_messages(item: Item, student_work: str, expected_answer: str,
                   graph: Graph, *, problem: Optional[str] = None) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER_TEMPLATE.format(
            node_list=render_nodes(graph),
            item_node=item.node_id,
            problem=problem or item.stem_latex,
            student_work=student_work,
            correct_answer=expected_answer,
        )},
    ]


# --------------------------------------------------------------------------- cache

def cache_key(item_id: str, student_work: str) -> str:
    """Primary key: this item, this working."""
    return hashlib.sha1(
        f"{item_id}\x00{_canon(student_work)}".encode("utf-8")).hexdigest()[:16]


def work_key(student_work: str) -> str:
    """Secondary key: this working, whatever the item is called.

    The demo item does not exist in `items.json` and may be given a different id by whoever wires
    the UI. A demo that silently falls through to a live call because an id was renamed is exactly
    the coin flip section 16 told us to avoid, so the working alone is also indexed.
    """
    return hashlib.sha1(_canon(student_work).encode("utf-8")).hexdigest()[:16]


def _canon(s: str) -> str:
    """Whitespace-FREE form, so any spacing of the same working hits the same cache entry.

    Collapsing runs of whitespace is not enough. OCR, a typed answer and a hand-written demo
    string differ by spaces INSIDE the expression, not just between tokens: "[ (2)(x-3)" against
    "[(2)(x-3)" collapses to two different strings and misses. Spacing carries no mathematical
    meaning here and this value is only ever used as a lookup key, so remove it entirely.
    """
    return re.sub(r"\s+", "", (s or "").strip())


def load_cache(cache_path: str | Path) -> dict:
    p = _resolve(cache_path)
    if not p.exists():
        return {"entries": {}, "by_work": {}}
    try:
        d = json.loads(p.read_text())
    except Exception:                                    # noqa: BLE001
        return {"entries": {}, "by_work": {}}
    d.setdefault("entries", {})
    d.setdefault("by_work", {})
    return d


def cache_lookup(cache_path: str | Path, item_id: str,
                 student_work: str) -> Optional[Diagnosis]:
    cache = load_cache(cache_path)
    entry = cache["entries"].get(cache_key(item_id, student_work))
    if entry is None:
        alias = cache["by_work"].get(work_key(student_work))
        entry = cache["entries"].get(alias) if alias else None
    if entry is None:
        return None
    return Diagnosis.from_dict(entry["diagnosis"], from_cache=True)


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = _REPO_ROOT / path
    return p


# --------------------------------------------------------------------------- the call

def diagnose(item: Item, student_work: str, expected_answer: str, graph: Graph,
             api_key: Optional[str] = None, *, cache_path: str | Path | None = None,
             seed: int = DEFAULT_SEED, model: str = MODEL,
             max_tokens: int = DEFAULT_MAX_TOKENS, retries: int = 3,
             problem: Optional[str] = None) -> Diagnosis:
    """Diagnose a WRONG answer. Only call this after `grading.grade` returned correct=False.

    With `cache_path` set, a stored entry is served without touching the network, which is how
    the demo stays deterministic. A miss falls through to a live call rather than failing, so
    development still works; if you need the guarantee that no call happens, check
    `cache_lookup` yourself first.
    """
    if cache_path is not None:
        hit = cache_lookup(cache_path, item.item_id, student_work)
        if hit is not None:
            return hit

    if not api_key:
        return Diagnosis(False, None, None, None, "", 0.0, 0.0, False,
                         error="no cached diagnosis and no api key")

    args, latency, err = _call(build_messages(item, student_work, expected_answer, graph,
                                              problem=problem),
                               build_tool(graph), api_key, seed, model, max_tokens, retries)
    if args is None:
        return Diagnosis(False, None, None, None, "", 0.0, latency, False, error=err)

    blamed = args.get("blamed_node")
    if isinstance(blamed, str) and blamed.strip().lower() in ("", "null", "none"):
        blamed = None
    if blamed is not None and blamed not in graph.nodes:
        # The enum should make this impossible. If it ever happens, drop the routing and keep
        # the message, which section 16 found is the more trustworthy half anyway.
        err = f"model returned unknown node id {blamed!r}"
        blamed = None

    return Diagnosis(
        correct=bool(args.get("correct", False)),
        failed_step=args.get("failed_step"),
        blamed_node=blamed,
        misconception_tag=args.get("misconception_tag"),
        student_message=args.get("student_message") or "",
        confidence=float(args.get("confidence") or 0.0),
        latency_s=round(latency, 2),
        from_cache=False,
        error=err,
    )


def _call(messages: list[dict], tool: dict, api_key: str, seed: int, model: str,
          max_tokens: int, retries: int):
    """(tool arguments, latency, error). Never raises."""
    try:
        from sarvamai import SarvamAI
    except ImportError as e:                             # noqa: BLE001
        return None, 0.0, f"sarvamai not installed: {e}"

    client = SarvamAI(api_subscription_key=api_key)
    last = None
    for attempt in range(retries):
        try:
            t0 = time.time()
            r = client.chat.completions(
                messages=messages,
                model=model,
                temperature=0,
                seed=seed,
                max_tokens=max_tokens,
                tools=[tool],
                tool_choice={"type": "function",
                             "function": {"name": "report_diagnosis"}},
            )
            latency = time.time() - t0
            d = r.model_dump() if hasattr(r, "model_dump") else r
            choice = (d.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls:
                last = "no tool_call returned"
                time.sleep(2 ** attempt)
                continue
            args = (calls[0].get("function") or {}).get("arguments")
            if isinstance(args, str):
                args = json.loads(args)
            return args, latency, None
        except Exception as e:                           # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    return None, 0.0, last


# --------------------------------------------------------------------------- cache building

def build_cache(entries: Iterable[dict], graph: Graph, api_key: str,
                cache_path: str | Path = DEFAULT_CACHE, *, runs: int = 3,
                verbose: bool = True) -> dict:
    """Run each entry live `runs` times and store the best response.

    Each entry is a dict: `item` (Item), `student_work`, `expected_answer`, and optionally
    `problem`, `expect_node` and `label`.

    "Best" is defined so that it cannot quietly launder a bad result: if `expect_node` is given
    we keep a run that matched it and record the agreement rate honestly in `stability`, so
    anyone reading the file can see whether the live model actually agrees with the cached
    answer or whether we cherry-picked one run out of three. If no run matches, the modal answer
    is stored and `stability` says so.
    """
    cache = load_cache(cache_path)
    summary = {}

    for entry in entries:
        item: Item = entry["item"]
        work = entry["student_work"]
        expected = entry["expected_answer"]
        results = []
        for i in range(runs):
            d = diagnose(item, work, expected, graph, api_key,
                         cache_path=None, seed=DEFAULT_SEED + i,
                         problem=entry.get("problem"))
            results.append(d)
            if verbose:
                print(f"  run {i + 1}/{runs}: {d.blamed_node} "
                      f"({d.latency_s}s){' ERROR ' + d.error if d.error else ''}")

        usable = [d for d in results if d.error is None and d.student_message]
        if not usable:
            raise RuntimeError(f"every run failed for {item.item_id}: "
                               f"{[d.error for d in results]}")

        counts = Counter(d.blamed_node for d in usable)
        want = entry.get("expect_node")
        matched = [d for d in usable if d.blamed_node == want] if want else []
        best = matched[0] if matched else next(
            d for d in usable if d.blamed_node == counts.most_common(1)[0][0])
        agree = counts[best.blamed_node]

        key = cache_key(item.item_id, work)
        cache["entries"][key] = {
            "label": entry.get("label", item.item_id),
            "item_id": item.item_id,
            "item_node": item.node_id,
            "problem": entry.get("problem") or item.stem_latex,
            "student_work": work,
            "expected_answer": expected,
            "expect_node": want,
            "stability": {
                "runs": len(usable),
                "agreement": f"{agree}/{len(usable)}",
                "blamed_nodes": dict(counts),
                "latencies_s": [d.latency_s for d in usable],
            },
            "diagnosis": {k: v for k, v in best.to_dict().items()
                          if k not in ("from_cache",)},
        }
        cache["by_work"][work_key(work)] = key
        summary[item.item_id] = cache["entries"][key]["stability"]

    cache["note"] = (
        "Pre-computed diagnoses for the demo. learning-design.md section 16: `seed` does NOT pin "
        "the model's output, so anything shown on stage is served from here rather than from a "
        "live call. `stability` records what the live model actually did across runs; read it "
        "before trusting the cached answer to reproduce."
    )
    p = _resolve(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n")
    return summary


def load_graph(path: str | Path = "data/graph/nodes.json") -> Graph:
    """Convenience loader for the cache builder and the tests.

    `nodes.json` is a list; `Graph.nodes` is a dict keyed by id. The engine may grow its own
    loader; this one exists so that building the demo cache does not depend on it.
    """
    raw = json.loads(_resolve(path).read_text())
    return Graph(nodes={n["id"]: n for n in raw["nodes"]},
                 target_node=raw.get("target_node"))


def _main() -> None:
    """`python3 -m services.diagnose --build` rebuilds the demo cache. Needs SARVAM_API_KEY."""
    import argparse
    import os

    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="rebuild data/demo/diagnosis_cache.json")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    a = ap.parse_args()

    key = os.environ.get("SARVAM_API_KEY")
    graph = load_graph()
    if not a.build:
        print(f"graph: {len(graph.nodes)} nodes; cache: {a.cache}")
        return
    if not key:
        raise SystemExit("SARVAM_API_KEY is not set")
    print(f"building {a.cache} with {a.runs} runs per entry")
    out = build_cache(
        [{"item": DEMO_ITEM, "student_work": DEMO_STUDENT_WORK,
          "expected_answer": DEMO_EXPECTED_ANSWER, "problem": DEMO_PROBLEM,
          "expect_node": DEMO_EXPECTED_NODE, "label": "demo climax: quotient rule, sign error"}],
        graph, key, a.cache, runs=a.runs)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    _main()
