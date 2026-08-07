#!/usr/bin/env python3
"""Pull exercises and their published answers from OpenStax into a raw item bank.

Licence: OpenStax Calculus is CC BY-NC-SA 4.0. Fine for the hackathon with attribution, NOT for a
paid product. Every item is stamped source="openstax" so the commercial migration is a delete and
regenerate rather than a re-architecture. See learning-design.md section 12.2.

Only odd-numbered exercises carry published answers, and an item without a checkable answer is
useless to us because the CAS is the grader. So `has_answer` is the filter that matters, not the
count of exercises scraped.

    python3 fetch_openstax.py                  # writes data/items/raw_items.json
"""

import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mathml import html_to_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "items"
CACHE = ROOT / ".cache" / "openstax"

V1 = "https://openstax.org/books/calculus-volume-1/pages/"
V3 = "https://openstax.org/books/calculus-volume-3/pages/"

# (url, answer-page url, a hint for the tagger about which part of the graph this covers)
SECTIONS = [
    (V1 + "2-2-the-limit-of-a-function", V1 + "chapter-2", "limits, informal idea"),
    (V1 + "2-3-the-limit-laws", V1 + "chapter-2", "limit laws, direct substitution, 0/0 factoring"),
    (V1 + "2-4-continuity", V1 + "chapter-2", "continuity"),
    (V1 + "3-1-defining-the-derivative", V1 + "chapter-3", "difference quotient, tangent slope"),
    (V1 + "3-3-differentiation-rules", V1 + "chapter-3", "power, constant multiple, sum, product, quotient rules"),
    (V1 + "3-5-derivatives-of-trigonometric-functions", V1 + "chapter-3", "trig derivatives"),
    (V1 + "3-6-the-chain-rule", V1 + "chapter-3", "chain rule"),
    (V1 + "3-8-implicit-differentiation", V1 + "chapter-3", "implicit differentiation"),
    (V1 + "3-9-derivatives-of-exponential-and-logarithmic-functions", V1 + "chapter-3", "exp and log derivatives"),
    (V1 + "4-3-maxima-and-minima", V1 + "chapter-4", "critical points, local extrema"),
    (V3 + "4-3-partial-derivatives", V3 + "chapter-4", "partial derivatives"),
    (V3 + "4-5-the-chain-rule", V3 + "chapter-4", "multivariable chain rule"),
    (V3 + "4-6-directional-derivatives-and-the-gradient", V3 + "chapter-4", "gradient, directional derivative"),
]


def get(url):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (re.sub(r"[^a-z0-9]+", "_", url.lower().split("/pages/")[-1]) +
                   ("_v3" if "volume-3" in url else "") + ".html")
    if key.exists():
        return key.read_text(encoding="utf-8", errors="replace")
    r = requests.get(url, timeout=60, headers={"User-Agent": "open-tutor-research/0.1"})
    if r.status_code != 200:
        return None
    # OpenStax serves UTF-8 but does not always declare it, so requests falls back to
    # ISO-8859-1 and every prime, minus and partial-derivative sign turns to mojibake.
    r.encoding = "utf-8"
    key.write_text(r.text, encoding="utf-8")
    time.sleep(1)   # be polite
    return r.text


def parse_answers(html):
    """exercise id -> answer text, from a chapter answer-key page."""
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for node in soup.select('div[data-type=solution][id$="-solution"]'):
        ex_id = node.get("id")[: -len("-solution")]
        frag = node.__copy__()
        num_el = frag.select_one(".os-number")
        num = num_el.get_text(strip=True) if num_el else None
        for a in frag.select("a.os-number, span.os-number, span.os-divider"):
            a.decompose()
        txt = html_to_text(str(frag))
        if txt:
            # keep the number so the caller can verify the answer belongs to the question
            out[ex_id] = {"answer": txt, "number": num}
    return out


# OpenStax groups exercises under a shared instruction ("For the following exercises, find the
# derivative"), so a scraped stem is often bare: "$h(x)=x^3 f(x)$" with no verb. Unusable in front
# of a student, and it would make the diagnosis guess at the task. Reattach the group instruction.
IMPERATIVES = re.compile(
    r"\b(find|evaluate|determine|compute|calculate|decide|solve|use|show|explain|sketch|"
    r"state|write|differentiate|prove|classify|estimate|give|verify|express)\b", re.I)


def group_instruction(ex):
    for para in ex.find_all_previous("p"):
        txt = html_to_text(str(para))
        if txt.lower().startswith("for the following exercises"):
            return txt.rstrip(":. ") + "."
    return None


def parse_section(html, answers, section_slug, hint):
    soup = BeautifulSoup(html, "html.parser")
    items, skipped, mismatched, bare = [], 0, [], 0
    for ex in soup.select("div[data-type=exercise]"):
        prob = ex.select_one("div[data-type=problem]")
        if not prob:
            continue
        num_el = prob.select_one(".os-number")
        number = num_el.get_text(strip=True) if num_el else None
        container = prob.select_one(".os-problem-container") or prob
        stem = html_to_text(str(container))
        if not stem or len(stem) < 8:
            continue
        # [T] marks exercises that need a graphing calculator; useless for a typed-answer flow
        if stem.startswith("[T]") or "[T]" in stem[:12]:
            skipped += 1
            continue
        instruction = None
        if not IMPERATIVES.search(stem):
            instruction = group_instruction(ex)
            if not instruction:
                bare += 1
                continue      # no task and no instruction: we cannot ask this of a student
        ex_id = ex.get("id")
        rec = answers.get(ex_id)
        ans = None
        if rec:
            # An id match is not enough: verify the answer key's exercise number agrees with the
            # question's. A silently mismatched answer would poison the CAS grader.
            if rec["number"] and number and rec["number"] != number:
                mismatched.append((number, rec["number"]))
            else:
                ans = rec["answer"]
        items.append({
            "openstax_id": ex_id,
            "number": number,
            "stem_latex": (f"{instruction} {stem}" if instruction else stem),
            "stem_raw": stem,
            "group_instruction": instruction,
            "answer_latex": ans,
            "has_answer": ans is not None,
            "needs_figure": bool(re.search(r"\b(graph|figure|following table|shown)\b", stem, re.I)),
            "section": section_slug,
            "section_hint": hint,
            "source": "openstax",
            "source_url": section_slug,
        })
    return items, skipped, mismatched, bare


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    answer_cache, all_items = {}, []
    print(f"{len(SECTIONS)} sections\n")
    for url, ans_url, hint in SECTIONS:
        slug = url.split("/pages/")[-1]
        html = get(url)
        if not html:
            print(f"  {slug:60s} FETCH FAILED (check the slug)")
            continue
        if ans_url not in answer_cache:
            answer_cache[ans_url] = parse_answers(get(ans_url))
        items, skipped, mismatched, bare = parse_section(html, answer_cache[ans_url], slug, hint)
        withans = sum(1 for i in items if i["has_answer"])
        all_items.extend(items)
        print(f"  {slug:60s} {len(items):4d} items, {withans:4d} with answers"
              + (f", {skipped} [T] skipped" if skipped else "")
              + (f", {len(mismatched)} ANSWER MISMATCH dropped" if mismatched else "")
              + (f", {bare} bare dropped" if bare else ""))

    usable = [i for i in all_items if i["has_answer"] and not i["needs_figure"]]
    path = OUT / "raw_items.json"
    path.write_text(json.dumps({
        "licence": "OpenStax Calculus, CC BY-NC-SA 4.0. Non-commercial use only.",
        "attribution": "Exercises from OpenStax Calculus Volumes 1 and 3, openstax.org",
        "total": len(all_items), "with_answers": len(usable), "items": all_items,
    }, indent=2))

    figs = sum(1 for i in all_items if i["has_answer"] and i["needs_figure"])
    print(f"\n{len(all_items)} exercises, {len(usable)} usable "
          f"(has a published answer and needs no figure; {figs} figure-based excluded)")
    print(f"written to {path}")
    if len(usable) < 60:
        print("\nFewer than 60 usable items. Add sections, or accept a thinner bank.")
    print("\nNote: the algebra and trig prerequisite nodes will have almost no items here.")
    print("A calculus textbook does not drill sign distribution. Those need generated drills.")


if __name__ == "__main__":
    main()
