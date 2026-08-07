#!/usr/bin/env python3
"""
Experiment 1: does the photo pillar survive contact with real handwriting?

Runs the full production path end to end:

    phone photo -> Sarvam Vision (doc_ai digitise, content_type=handwritten)
                -> OCR markdown
                -> sarvam-105b diagnosis (the exact prompt from experiment 2)
                -> scored against the same ground truth

Because the cases are identical to experiment 2, the clean-text baseline is already known
(25/30, 83% on error cases). Any drop here is caused by OCR, not by the diagnosis model.
That decomposition is the entire reason this is worth running.

Usage:
    export SARVAM_API_KEY=...
    python3 make_worksheet.py           # print it, write the solutions by hand, photograph them
    python3 run.py --images ./photos    # files named c01.jpg, c05.png, ...
    python3 run.py --images ./photos --ocr-only    # stop after transcription
"""

import argparse
import difflib
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
EXP2 = HERE.parent / "exp2_diagnosis"

# Reuse experiment 2's prompt, parser and scorer verbatim. If the diagnosis logic drifts between
# the two experiments the comparison is meaningless, so there must be exactly one copy of it.
# Loaded by explicit path under a distinct name: both experiments have a run.py, so a plain
# `import run` resolves to whichever is on sys.path first, or to this file itself.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("exp2_diagnosis_run", EXP2 / "run.py")
exp2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exp2)

GRAPH = json.loads((HERE.parent.parent / "data" / "graph" / "nodes.json").read_text())
CASES = {c["id"]: c for c in json.loads((EXP2 / "cases.json").read_text())["cases"]}
VALID_IDS = {n["id"] for n in GRAPH["nodes"]}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".heic": "image/heic", ".webp": "image/webp"}


# ---------------------------------------------------------------- vision

def digitise(client, path, language, timeout=300):
    """Submit one image, poll to a terminal state, return (markdown, seconds, error)."""
    t0 = time.time()
    try:
        with open(path, "rb") as fh:
            job = client.doc_ai.digitise(
                file=[(path.name, fh, MIME.get(path.suffix.lower(), "image/jpeg"))],
                language=language,
                output_format="md",
                content_type="handwritten",
            )
    except Exception as e:  # noqa: BLE001
        return None, time.time() - t0, f"submit failed: {type(e).__name__}: {e}"

    job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    if not job_id:
        return None, time.time() - t0, f"no job id in response: {job}"

    state = None
    while time.time() - t0 < timeout:
        time.sleep(3)
        try:
            st = client.doc_ai.get_status(job_id=job_id)
        except Exception as e:  # noqa: BLE001
            return None, time.time() - t0, f"status failed: {type(e).__name__}: {e}"
        state = (getattr(st, "status", None) or getattr(st, "state", "") or "").lower()
        if state in ("completed", "succeeded", "success", "done"):
            break
        if state in ("failed", "error", "cancelled"):
            return None, time.time() - t0, f"job {state}: {st}"
    else:
        return None, time.time() - t0, f"timed out in state {state}"

    text, err = fetch_output(client, job_id)
    return text, time.time() - t0, err


def extract_digitise_text(payload):
    """Real shape: documents[].pages[].blocks[] with text and reading_order.

    Page-level `content` comes back null and the text lives on the blocks, so honour
    reading_order rather than trusting list order.
    """
    out = []
    for doc in payload.get("documents") or []:
        pages = sorted(doc.get("pages") or [],
                       key=lambda p: (p.get("page_num") or p.get("page_number") or 0))
        for p in pages:
            if p.get("content"):
                out.append(p["content"])
                continue
            blocks = sorted(p.get("blocks") or [], key=lambda b: (b.get("reading_order") or 0))
            out.extend(b["text"] for b in blocks if b.get("text"))
    return "\n".join(out).strip() or None


def fetch_output(client, job_id):
    """Two documented retrieval paths and neither is guaranteed, so try both."""
    try:
        res = client.doc_ai.get_results(job_id=job_id)
        payload = res.model_dump() if hasattr(res, "model_dump") else res
        text = extract_digitise_text(payload) or harvest_text(payload)
        if text:
            return text, None
    except Exception:  # noqa: BLE001
        pass
    try:
        dl = client.doc_ai.get_download_url(job_id=job_id)
        url = getattr(dl, "download_url", None) or getattr(dl, "url", None)
        if not url:
            return None, f"no download url: {dl}"
        blob = requests.get(url, timeout=120).content
        if blob[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                mds = [n for n in z.namelist() if n.lower().endswith((".md", ".txt"))]
                if not mds:
                    return None, f"zip has no markdown: {z.namelist()}"
                return "\n".join(z.read(n).decode("utf-8", "replace") for n in sorted(mds)), None
        return blob.decode("utf-8", "replace"), None
    except Exception as e:  # noqa: BLE001
        return None, f"download failed: {type(e).__name__}: {e}"


def harvest_text(obj, depth=0):
    """Response shape is undocumented, so walk it for the longest plausible text field."""
    if depth > 6:
        return None
    if isinstance(obj, str):
        return obj if len(obj) > 20 else None
    best = None
    if isinstance(obj, dict):
        for key in ("markdown", "md", "content", "text", "output", "data", "pages", "result"):
            if key in obj:
                got = harvest_text(obj[key], depth + 1)
                if got and (best is None or len(got) > len(best)):
                    best = got
        if best is None:
            for v in obj.values():
                got = harvest_text(v, depth + 1)
                if got and (best is None or len(got) > len(best)):
                    best = got
    elif isinstance(obj, list):
        chunks = [t for t in (harvest_text(v, depth + 1) for v in obj) if t]
        if chunks:
            best = "\n".join(chunks)
    return best


# ---------------------------------------------------------------- ocr scoring

# Students write several solutions on one page and photograph the page. Splitting the OCR by the
# case markers is closer to the real product than one photo per problem, and it additionally tests
# whether Vision preserves reading order down the page.
CASE_MARKER = re.compile(r"(?im)^[\s#*>|_-]*[\(\[]?\s*[cC(]\s*0?(\d{1,2})\s*[:;.\)\]]")


# Handwritten "c01" reliably OCRs as "Col" (zero to o, one to l), so the numbers in the labels
# cannot be trusted. The positional fallback splits on anything label-shaped and assigns the
# chunks to the expected cases in page order. In the real product this problem does not exist:
# the app knows which problem the student is on, so identity never comes off the page.
GENERIC_LABEL = re.compile(r"(?im)^[\s#*>|_\-\(\[]*[cC][A-Za-z0-9oOlI|]{1,3}\s*[:;.\)\]]\s*$")


def split_page(text, wanted_ids, expect=None):
    """Cut one page of OCR into per-case chunks. Returns (chunks, order, mode)."""
    text = text or ""
    hits = [(m.start(), int(m.group(1)), m.end()) for m in CASE_MARKER.finditer(text)]
    seen, ordered = set(), []
    for start, num, end in hits:
        if num not in seen:
            seen.add(num)
            ordered.append((start, num, end))

    if len(ordered) > 1:
        out = {}
        for i, (_, num, end) in enumerate(ordered):
            stop = ordered[i + 1][0] if i + 1 < len(ordered) else len(text)
            cid = next((c for c in wanted_ids if c.startswith(f"c{num:02d}")), None)
            if cid:
                out[cid] = text[end:stop].strip()
        if out:
            return out, [n for _, n, _ in ordered], "numbered"

    marks = [(m.start(), m.end()) for m in GENERIC_LABEL.finditer(text)]
    if not marks:
        return {}, [], "none"
    targets = list(expect) if expect else sorted(wanted_ids)[:len(marks)]
    out = {}
    for i, (start, end) in enumerate(marks):
        if i >= len(targets):
            break
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out[targets[i]] = text[end:stop].strip()
    return out, list(range(1, len(marks) + 1)), "positional"


def normalise(s):
    s = re.sub(r"[`*_$\\]", "", s or "")          # markdown and latex noise
    s = s.replace("−", "-").replace("×", "*")
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


MATH_TOKEN = re.compile(r"[0-9]+|[a-z]+|[+\-*/^=()\[\]]")


def ocr_metrics(truth, got):
    """Similarity plus the metric that actually matters: did the mathematical tokens survive?

    Prose similarity is misleading here. What breaks a diagnosis is a lost minus sign or a
    mangled exponent, so token recall is weighted separately.
    """
    t, g = normalise(truth), normalise(got)
    ratio = difflib.SequenceMatcher(None, t, g).ratio()
    tt, gt = MATH_TOKEN.findall(t), set(MATH_TOKEN.findall(g))
    recall = sum(1 for x in tt if x in gt) / len(tt) if tt else 0.0
    signs_truth = t.count("-")
    signs_got = g.count("-")
    return {"similarity": round(ratio, 3), "token_recall": round(recall, 3),
            "minus_signs_truth": signs_truth, "minus_signs_ocr": signs_got}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--images", help="folder of photos named c01.jpg, c05.png, ...")
    src.add_argument("--page", help="one photo holding several solutions labelled c01:, c02: ...")
    ap.add_argument("--expect", help="comma-separated case ids in page order, e.g. c01,c02,c03,c04")
    ap.add_argument("--language", default="en-IN")
    ap.add_argument("--ocr-only", action="store_true", help="skip diagnosis, just transcribe")
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        print("SARVAM_API_KEY is not set.")
        sys.exit(2)

    try:
        from sarvamai import SarvamAI
    except ImportError:
        print("pip install sarvamai")
        sys.exit(2)
    client = SarvamAI(api_subscription_key=api_key)

    transcripts = {}   # case_id -> (ocr_text, seconds)
    page_text = None

    if args.page:
        path = Path(args.page).expanduser().resolve()
        if not path.is_file():
            print(f"Not a file: {path}")
            sys.exit(2)
        print(f"Digitising the whole page: {path.name} ...", flush=True)
        page_text, secs, err = digitise(client, path, args.language)
        if err:
            print(f"VISION FAILED ({secs:.0f}s): {err}")
            sys.exit(1)
        print(f"ok, {secs:.0f}s, {len(page_text)} chars\n")
        print("-" * 78)
        print(page_text)
        print("-" * 78 + "\n")
        expect = [c.strip() for c in args.expect.split(",")] if args.expect else None
        if expect:
            expect = [next((c for c in CASES if c.startswith(e)), e) for e in expect]
        chunks, order, mode = split_page(page_text, CASES, expect)
        print(f"Case markers found: {len(order)}  (split mode: {mode})")
        if mode == "positional":
            print("  -> the cNN labels did not survive OCR, so chunks were assigned by page")
            print("     order. Handwritten 'c01' reads as 'Col'. Not a product problem: the")
            print("     real app knows which problem the student is on.")
        if not chunks:
            print("\nNo cNN: markers recognised in the OCR. Cannot attribute work to cases.")
            print("Inspect the transcription above; the labels may have been mangled.")
            sys.exit(1)
        # the page was one Vision call, so amortise its cost across the cases it yielded
        per = secs / max(len(chunks), 1)
        transcripts = {cid: (txt, per) for cid, txt in chunks.items()}
        print(f"Split into {len(chunks)} case(s): {', '.join(sorted(chunks))}\n")
    else:
        folder = Path(args.images).expanduser().resolve()
        if not folder.is_dir():
            print(f"Not a folder: {folder}")
            sys.exit(2)
        found = {}
        for p in sorted(folder.iterdir()):
            if p.suffix.lower() not in IMAGE_EXT:
                continue
            stem = p.stem.lower().split("-")[0].split("_")[0]
            match = next((cid for cid in CASES if cid.startswith(stem)), None)
            if match:
                found[match] = p
            else:
                print(f"  skipping {p.name}: no case id matches '{stem}'")
        if not found:
            print(f"No usable images in {folder}.")
            print("Name them after the case ids, e.g. c01.jpg. Run make_worksheet.py first.")
            sys.exit(1)
        print(f"Found {len(found)} of {len(CASES)} cases: {', '.join(sorted(found))}\n")
        for cid in sorted(found):
            print(f"[{cid}] {found[cid].name} ... ", end="", flush=True)
            text, secs, err = digitise(client, found[cid], args.language)
            if err:
                print(f"VISION FAILED ({secs:.0f}s): {err}")
                continue
            print(f"ok {secs:.0f}s")
            transcripts[cid] = (text, secs)

    rows, diag_rows = [], []
    for cid in sorted(transcripts):
        case = CASES[cid]
        text, secs = transcripts[cid]
        print(f"[{cid}] ", end="", flush=True)
        m = ocr_metrics(case["student_work"], text)
        print(f"similarity {m['similarity']:.2f}  token recall {m['token_recall']:.2f}")
        row = {"case_id": cid, "ocr_seconds": round(secs, 1), "ocr_text": text, **m}

        if not args.ocr_only:
            # Diagnose from the OCR text, exactly as production would.
            ocr_case = dict(case, student_work=text)
            content, lat, cerr, meta = exp2.call_model(
                exp2.build_messages(ocr_case, GRAPH["nodes"]), api_key, args.max_tokens, None)
            if not cerr and (meta.get("finish_reason") == "length" or not (content or "").strip()):
                content, l2, cerr, meta = exp2.call_model(
                    exp2.build_messages(ocr_case, GRAPH["nodes"]), api_key, args.max_tokens, "low")
                lat += l2
            parsed = None
            if not cerr:
                parsed, _ = exp2.extract_json(content)
                if parsed is None:
                    parsed = exp2.salvage_json(content)
            scored = exp2.score_one(case, parsed, VALID_IDS)
            scored["from"] = "ocr"
            diag_rows.append(scored)
            row["diagnosis"] = scored
            mark = "PASS" if scored["verdict"] in exp2.GOOD else "FAIL"
            print(f"         diagnosis: {scored.get('predicted') or '(no error)'}  "
                  f"[{mark} {scored['verdict']}]  {lat:.0f}s")
        rows.append(row)

    # ---- report
    ok_ocr = [r for r in rows if "ocr_error" not in r]
    print("\n" + "=" * 78)
    print("OCR FIDELITY")
    print("=" * 78)
    if ok_ocr:
        print(f"{'case':<10} {'sim':>6} {'tokens':>8} {'minus -':>10} {'secs':>6}")
        for r in ok_ocr:
            flag = "" if r["minus_signs_ocr"] == r["minus_signs_truth"] else "  <-- SIGN LOST"
            print(f"{r['case_id']:<10} {r['similarity']:>6.2f} {r['token_recall']:>8.2f} "
                  f"{r['minus_signs_ocr']:>4}/{r['minus_signs_truth']:<5} {r['ocr_seconds']:>6.0f}{flag}")
        print(f"\nmean similarity   {sum(r['similarity'] for r in ok_ocr)/len(ok_ocr):.2f}")
        print(f"mean token recall {sum(r['token_recall'] for r in ok_ocr)/len(ok_ocr):.2f}")
        lost = [r for r in ok_ocr if r["minus_signs_ocr"] != r["minus_signs_truth"]]
        if lost:
            print(f"\n{len(lost)}/{len(ok_ocr)} lost or gained a minus sign. This is the failure that")
            print("matters most: sign errors are the single most common misconception we diagnose,")
            print("so an OCR that drops them cannot support blame propagation on sign nodes.")
    failed = [r for r in rows if "ocr_error" in r]
    if failed:
        print(f"\n{len(failed)} image(s) failed Vision entirely: "
              f"{', '.join(r['case_id'] for r in failed)}")

    if diag_rows:
        errs = [r for r in diag_rows if r["blame_type"] != "control"]
        n = sum(1 for r in errs if r["verdict"] in exp2.GOOD)
        print("\n" + "=" * 78)
        print("END TO END (photo -> diagnosis)")
        print("=" * 78)
        for r in diag_rows:
            mark = "PASS" if r["verdict"] in exp2.GOOD else "FAIL"
            print(f"{r['case_id']:<10} {r['blame_type']:<8} expected {str(r['true_node']):<26} "
                  f"got {str(r['predicted']):<26} {mark}")
        if errs:
            print(f"\nend-to-end on error cases: {n}/{len(errs)} = {100*n/len(errs):.0f}%")
            print("clean-text baseline (experiment 2): 83%")
            print("The gap between those two numbers is the cost of OCR. That is the finding.")

    out = HERE / f"results{'-' + args.tag if args.tag else ''}.json"
    out.write_text(json.dumps({"language": args.language, "rows": rows}, indent=2))
    print(f"\nWritten to {out}")
    print("Read the ocr_text fields. The number tells you whether it works; the text tells you why.")


if __name__ == "__main__":
    main()
