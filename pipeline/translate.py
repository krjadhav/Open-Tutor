"""Offline translation pipeline for Open Tutor.

The one trap (learning-design.md section 13): never hand LaTeX to a translation model.
It reflows, re-orders or silently mangles the maths and nobody notices until a judge does.

So every string goes through the same three steps:

    "Differentiate $f(x) = x\\sin x$ using the product rule"
      -> extract_math   -> "Differentiate {{m1}} using the product rule"  (translate this)
      -> translate prose only
      -> restore_math   -> "{{m1}} का अवकलन गुणनफल नियम से कीजिए"  with {{m1}} put back verbatim

Verification is non negotiable. Every {{mN}} that went into the translator must come back
out exactly once. A string whose placeholders are missing, duplicated or invented FAILS and
is reported, never shipped.

Output is written to data/i18n/<lang>.json, shaped for the `translations` table described in
learning-design.md section 12.1:

    {"lang": "hi-IN", "entries": {"<entity_type>:<entity_id>": "<translated text>"}}

Usage:
    SARVAM_API_KEY=... python3 -m pipeline.translate --lang hi-IN
    python3 -m pipeline.translate --dry-run          # no network, echoes the templates
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODES_PATH = ROOT / "data" / "graph" / "nodes.json"
ITEMS_PATH = ROOT / "data" / "items" / "items.json"
I18N_DIR = ROOT / "data" / "i18n"

DEFAULT_MODEL = "sarvam-translate:v1"
SOURCE_LANG = "en-IN"

# {{m1}} for extracted maths, {{goal}} / {{count}} for runtime interpolation in UI strings.
# Both kinds are opaque to the translator and both are verified the same way.
PLACEHOLDER_RE = re.compile(r"\{\{[A-Za-z][A-Za-z0-9_]*\}\}")
LOOSE_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


# --------------------------------------------------------------------------------------
# Maths templating
# --------------------------------------------------------------------------------------


def _dollar_positions(text: str) -> list[int]:
    """Indices of every unescaped '$' in text.

    A backslash escapes the next character, so '\\$' is a literal dollar and '\\\\' is a
    LaTeX line break whose second backslash cannot escape a following '$'.
    """
    positions: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "$":
            positions.append(i)
        i += 1
    return positions


def extract_math(text: str) -> tuple[str, list[str]]:
    """Replace every $...$ span with {{m1}}, {{m2}} ... and return (template, math).

    The returned math strings keep their '$' delimiters, so restoring is a plain
    substitution. Escaped dollars are left alone. If the text has an odd number of
    delimiters the trailing unmatched '$' is treated as literal prose, which keeps the
    round trip exact even on malformed textbook stems.

    restore_math(*extract_math(t)) == t for every t.
    """
    positions = _dollar_positions(text)
    pairs = [(positions[i], positions[i + 1]) for i in range(0, len(positions) - 1, 2)]
    if not pairs:
        return text, []

    out: list[str] = []
    math: list[str] = []
    last = 0
    for index, (start, end) in enumerate(pairs, start=1):
        out.append(text[last:start])
        out.append("{{m%d}}" % index)
        math.append(text[start : end + 1])
        last = end + 1
    out.append(text[last:])
    return "".join(out), math


def restore_math(template: str, math: list[str]) -> str:
    """Put the extracted $...$ spans back into a (possibly translated) template."""
    result = template
    for index, span in enumerate(math, start=1):
        result = result.replace("{{m%d}}" % index, span)
    return result


# Function names that are part of an expression, not English prose, so scanning an
# expression should keep going when it meets one.
_EXPR_WORDS = {"sqrt", "log", "ln", "exp", "sin", "cos", "tan", "abs", "max", "min"}
# `output = ...`, `L = ...`, `w=2`. The maths that is NOT wrapped in dollars.
_ASSIGNMENT_RE = re.compile(r"(?<![A-Za-z0-9_}])[A-Za-z_][A-Za-z0-9_]*\s*=\s*")
# `∂L/∂b` written as literal Unicode rather than LaTeX.
_PARTIAL_RE = re.compile(r"∂[A-Za-z0-9_]*(?:\s*/\s*∂[A-Za-z0-9_]*)+")


def _expression_span_end(text: str, start: int) -> int:
    """End of the expression beginning at `start`, scanning whitespace-delimited tokens.

    Stops as soon as English prose resumes: a token of two or more letters that is not a
    known function name. Also stops at a placeholder, which must never be swallowed.
    """
    i = start
    n = len(text)
    end = start
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        j = i
        while j < n and not text[j].isspace():
            j += 1
        token = text[i:j]
        if not token:
            break
        if "{{" in token or "}}" in token:
            break
        core = token.strip(".,;:?!")
        if len(core) >= 2 and core.isalpha() and core.lower() not in _EXPR_WORDS:
            break
        end = j
        i = j
    return end


def extract_code_spans(template: str, start: int = 0) -> tuple[str, list[str]]:
    """Mask raw, non-LaTeX maths so the translator cannot touch it either.

    Section 13 says do not hand LaTeX to a translation model. The same is true of any
    notation, and some generated stems carry a plain-text context expression outside the
    dollars, for example "Two-layer network: output = log(w2*(w1*3) + 1)**2.".

    That is not hypothetical. Left unprotected, Sarvam returned `((w1*2 + w2)*2 - ...)**3`
    for a source that said `((w1*2 + w2)**2 - ...)**3` (an exponent silently turned into a
    multiplication) and rendered `log(` as `लॉग(`. Both would have shipped as a wrong
    problem statement, in the demo language, which is precisely the failure section 13 warns
    about.

    Numbering continues from `start` so one flat span list restores maths, code and named
    placeholders together.
    """
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(template):
        assignment = _ASSIGNMENT_RE.search(template, cursor)
        partial = _PARTIAL_RE.search(template, cursor)
        if assignment is None and partial is None:
            break
        if partial is not None and (assignment is None or partial.start() < assignment.start()):
            ranges.append((partial.start(), partial.end()))
            cursor = partial.end()
            continue
        body_end = _expression_span_end(template, assignment.end())
        if body_end <= assignment.end():
            cursor = assignment.end()  # a bare '=' with no expression after it
            continue
        # Sentence punctuation belongs to the prose, not to the expression.
        trimmed = template[assignment.start() : body_end].rstrip().rstrip(".,;:")
        ranges.append((assignment.start(), assignment.start() + len(trimmed)))
        cursor = body_end

    out: list[str] = []
    spans: list[str] = []
    last = 0
    for slot, (begin, finish) in enumerate(ranges, start=start + 1):
        out.append(template[last:begin])
        out.append("{{m%d}}" % slot)
        spans.append(template[begin:finish])
        last = finish
    out.append(template[last:])
    return "".join(out), spans


def mask_named_placeholders(template: str, start: int = 0) -> tuple[str, list[str]]:
    """Rename readable runtime placeholders to opaque {{mN}} slots.

    en.json uses readable names such as {{goal}} and {{count}} because a developer has to
    read them. Sarvam translates the word inside the braces: "On your path to {{goal}}"
    came back as "{{लक्ष्य}} की ओर अपने रास्ते पर", which would silently break interpolation.
    {{mN}} is opaque and survives intact, so we mask on the way in and unmask on the way
    out using exactly the same substitution as the maths.

    `start` continues the numbering after the maths spans already extracted, so one flat
    span list restores both. Returns (template, masked placeholders in slot order).
    """
    slot = start
    masked: list[str] = []

    def replace(match: re.Match[str]) -> str:
        nonlocal slot
        original = match.group(0)
        if re.fullmatch(r"\{\{m\d+\}\}", original):
            return original
        slot += 1
        masked.append(original)
        return "{{m%d}}" % slot

    return PLACEHOLDER_RE.sub(replace, template), masked


def protect(text: str) -> tuple[str, list[str], list[str]]:
    """Everything the translator must not see, masked into one flat {{mN}} span list.

    Returns (template, spans, math) where `spans` restores the template exactly and `math`
    is just the $...$ subset, kept separately so the shipped string can be checked against
    the maths of its English source.
    """
    template, math = extract_math(text)
    template, code = extract_code_spans(template, start=len(math))
    template, named = mask_named_placeholders(template, start=len(math) + len(code))
    return template, math + code + named, math


def placeholders(text: str) -> list[str]:
    """Every {{...}} placeholder in text, in order of appearance, duplicates included."""
    return PLACEHOLDER_RE.findall(text)


def normalise_placeholders(text: str) -> str:
    """Repair the two ways a translator commonly perturbs a placeholder.

    It may add whitespace inside the braces, or render the digit in Devanagari numerals.
    Both are recoverable and unambiguous, so we repair rather than fail. Anything else
    (dropped, duplicated, reworded) is a genuine failure and is left to fail loudly.
    """

    def repair(match: re.Match[str]) -> str:
        return "{{%s}}" % match.group(1)

    text = text.translate(DEVANAGARI_DIGITS)
    return LOOSE_PLACEHOLDER_RE.sub(repair, text)


def verify_placeholders(source: str, translated: str) -> list[str]:
    """Return a list of problems. Empty list means the translation is safe to ship.

    Checks that every placeholder that went in came back out exactly once each, and that
    the translator did not invent any new ones.
    """
    problems: list[str] = []
    want = placeholders(source)
    got = placeholders(translated)

    want_counts: dict[str, int] = {}
    for name in want:
        want_counts[name] = want_counts.get(name, 0) + 1
    got_counts: dict[str, int] = {}
    for name in got:
        got_counts[name] = got_counts.get(name, 0) + 1

    for name, expected in sorted(want_counts.items()):
        actual = got_counts.get(name, 0)
        if actual == 0:
            problems.append("placeholder %s was dropped" % name)
        elif actual != expected:
            problems.append(
                "placeholder %s appears %d times, expected %d" % (name, actual, expected)
            )
    for name in sorted(got_counts):
        if name not in want_counts:
            problems.append("translator invented placeholder %s" % name)
    return problems


# --------------------------------------------------------------------------------------
# Terminology
# --------------------------------------------------------------------------------------

# Sarvam translates general prose well but drifts on maths terminology, and worse, drifts
# inconsistently across a session: "chain rule" came back as both चेन नियम and श्रृंखला नियम,
# "derivative" as both व्युत्पन्न and अवकलज. Section 13 warns that inconsistent wording for the
# same concept reads as broken. These are conservative noun-phrase swaps towards the terms an
# Indian school textbook uses, applied to the translated template before the maths goes back
# in, so a substitution can never touch a formula. Every application is counted and reported.
GLOSSARY: list[tuple[str, str]] = [
    ("उत्पाद नियम", "गुणनफल नियम"),  # product rule, not "product" as in merchandise
    ("गुणन नियम", "गुणनफल नियम"),
    ("चेन नियम", "श्रृंखला नियम"),  # chain rule, matching बहुचर श्रृंखला नियम
    ("अव्यक्त विभेदन", "अंतर्निहित अवकलन"),  # implicit differentiation
    ("विभेदन", "अवकलन"),  # differentiation, not "discrimination"
    ("को अलग करें", "का अवकलन कीजिए"),  # "differentiate", not "separate"
    ("को अलग कीजिए", "का अवकलन कीजिए"),
    ("व्युत्पन्न", "अवकलज"),  # derivative
    ("महत्वपूर्ण बिंदु", "क्रांतिक बिंदु"),  # critical point
    ("निरंतरता", "सांतत्य"),  # continuity
    ("पैरामीटरों", "प्राचलों"),  # parameters, came back both ways in the same run
    ("पैरामीटर", "प्राचल"),
    ("मास्टरड", "महारत हासिल"),  # "mastered" came back transliterated, not translated
    # "Quadratic plus ..." came back as वर्गमूल (square root) in two of the gradient descent
    # stems while every other "quadratic" became द्विघात, and the wrong rendering differed
    # between two runs of the same input. Both observed forms are corrected here; neither is
    # a phrase that could arise legitimately from this item bank.
    ("वर्गमूल प्लस", "द्विघात और"),
    ("वर्गमूल धनात्मक", "द्विघात और"),
    ("दिनों की लकीर", "दिन लगातार"),  # streak, not "a drawn line"
    # Register: sarvam-translate:v1 ignores mode="formal" and mixes आप with तुम/करो inside the
    # same string set. A UI that switches politeness level between two buttons reads as broken,
    # so the informal imperatives are normalised to the आप forms used everywhere else.
    ("तुम्हारी", "आपकी"),
    ("तुम्हारा", "आपका"),
    ("फोटो लो", "फोटो लें"),
    ("कोशिश करो", "कोशिश करें"),
    ("वापस आना", "वापस आइए"),
]


def apply_glossary(text: str) -> tuple[str, int]:
    """Normalise maths terminology and register in translated Hindi.

    Returns (text, substitutions). Runs on the translated template, before the maths goes
    back in, so a substitution can never reach inside a formula.
    """
    count = 0
    for source, target in GLOSSARY:
        if source == target:
            continue
        occurrences = text.count(source)
        if occurrences:
            text = text.replace(source, target)
            count += occurrences
    return text, count


def match_terminal_punctuation(source: str, translated: str) -> str:
    """Make sentence-final punctuation follow the English source, in Devanagari convention.

    Sarvam ends most outputs with "।" regardless of the input, which makes node titles and
    button labels read as sentences next to the ones it left bare, and it sometimes ends a
    Hindi sentence with a Latin full stop. Both are cosmetic on their own and both are
    obvious on a projector.
    """
    stripped = source.rstrip()
    if not stripped or stripped[-1] not in ".?!।":
        return re.sub(r"\s*[।.]+$", "", translated.rstrip())
    if stripped[-1] == ".":
        return re.sub(r"\s*\.$", "।", translated.rstrip())
    return translated


# --------------------------------------------------------------------------------------
# Sarvam translate
# --------------------------------------------------------------------------------------


def _looks_untranslated(source: str, translated: str) -> bool:
    """True if a string with real prose in it came back with no Indic script at all.

    This is the "never let a failed item silently become English" guard: some failure modes
    return the input verbatim with a 200, which would otherwise ship as a translation.
    """
    prose = PLACEHOLDER_RE.sub(" ", source)
    letters = re.sub(r"[^A-Za-z]", "", prose)
    if len(letters) < 3:
        return False
    return not DEVANAGARI_RE.search(translated)


def translate_batch(
    strings: list[str],
    target_lang: str,
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    source_lang: str = SOURCE_LANG,
    max_workers: int = 3,
    retries: int = 6,
    client: object | None = None,
) -> list[str | None]:
    """Translate strings into target_lang, preserving order.

    Returns a list the same length as the input. An entry is None when that string could
    not be translated after `retries` attempts. None is deliberate: the caller must decide
    what to do with a failure, and the one thing it must never do is ship the English.

    Identical inputs are translated once and shared, which cuts the 132 generated item
    stems down to 37 distinct prose templates.
    """
    if not strings:
        return []

    if client is None:
        from sarvamai import SarvamAI  # imported lazily so the tests need no SDK config

        client = SarvamAI(api_subscription_key=api_key)

    unique = sorted(set(strings))

    def translate_one(text: str) -> str | None:
        for attempt in range(retries):
            try:
                response = client.text.translate(
                    input=text,
                    source_language_code=source_lang,
                    target_language_code=target_lang,
                    model=model,
                )
                out = (getattr(response, "translated_text", None) or "").strip()
                if not out:
                    raise RuntimeError("empty translated_text")
                if _looks_untranslated(text, out):
                    raise RuntimeError("response came back in English")
                return normalise_placeholders(out)
            except Exception as exc:  # noqa: BLE001 - any failure is worth one more try
                if attempt == retries - 1:
                    print(
                        "  translate failed after %d attempts: %r (%s)"
                        % (retries, text[:70], str(exc)[:120]),
                        file=sys.stderr,
                    )
                    return None
                # Sarvam rate limits hard on burst; back off much further on a 429 than on
                # a transient network error, otherwise every retry is spent on the same 429.
                rate_limited = "429" in str(exc) or "rate_limit" in str(exc)
                base = 5.0 if rate_limited else 0.7
                time.sleep((base * (2**attempt)) + random.uniform(0, 0.4))
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(translate_one, unique))

    lookup = dict(zip(unique, results))
    return [lookup[s] for s in strings]


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------


def collect_source_strings() -> list[tuple[str, str]]:
    """Everything that needs translating, as (translations-table key, English text).

    Key format is "<entity_type>:<entity_id>" per learning-design.md section 12.1, with
    entity_type in {node_title, ui, item_stem}.

    blame_hint is deliberately excluded: it is prompt-only disambiguation for the diagnosis
    call and is never shown to a student, so translating it would be waste and would risk
    degrading the diagnosis prompt.
    """
    pairs: list[tuple[str, str]] = []

    nodes = json.loads(NODES_PATH.read_text(encoding="utf-8"))
    for node in nodes["nodes"]:
        pairs.append(("node_title:%s" % node["id"], node["title"]))

    ui = json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8"))
    for key, text in ui["entries"].items():
        pairs.append((key, text))

    items = json.loads(ITEMS_PATH.read_text(encoding="utf-8"))
    for item in items["items"]:
        # openstax stems are CC BY-NC-SA and there are 244 of them; the demo runs on the
        # 132 generated drills, so only those are worth the translation budget.
        if item.get("source") != "generated":
            continue
        pairs.append(("item_stem:%s" % item["item_id"], item["stem_latex"]))

    return pairs


def build(
    target_lang: str,
    api_key: str,
    *,
    dry_run: bool = False,
    model: str = DEFAULT_MODEL,
    out_path: Path | None = None,
) -> dict:
    pairs = collect_source_strings()
    templates: list[str] = []
    all_spans: list[list[str]] = []
    math_spans: list[list[str]] = []
    for _, english in pairs:
        template, spans, math = protect(english)
        assert restore_math(template, spans) == english, "round trip broke on source"
        templates.append(template)
        math_spans.append(math)
        all_spans.append(spans)

    distinct = len(set(templates))
    print(
        "%d strings, %d distinct prose templates, %d maths spans templated out"
        % (len(pairs), distinct, sum(len(m) for m in math_spans))
    )

    if dry_run:
        translated = ["[%s] %s" % (target_lang, t) for t in templates]
    else:
        translated = translate_batch(templates, target_lang, api_key, model=model)

    entries: dict[str, str] = {}
    failures: list[tuple[str, str]] = []
    glossary_hits = 0

    for (key, english), template, math, spans, hindi in zip(
        pairs, templates, math_spans, all_spans, translated
    ):
        if hindi is None:
            failures.append((key, "translation API failed"))
            continue
        problems = verify_placeholders(template, hindi)
        if problems:
            failures.append((key, "; ".join(problems)))
            continue
        hindi, hits = apply_glossary(hindi)
        glossary_hits += hits
        hindi = match_terminal_punctuation(template, hindi)
        restored = restore_math(hindi, spans)
        # Belt and braces: the maths in the shipped string must be exactly the maths that
        # came out of the English source, same spans, same count.
        if sorted(extract_math(restored)[1]) != sorted(math):
            failures.append((key, "maths spans differ after restore"))
            continue
        entries[key] = restored

    payload = {"lang": target_lang, "entries": entries}

    if out_path is None:
        out_path = I18N_DIR / ("%s.json" % target_lang.split("-")[0])
    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print("translated %d/%d, %d glossary fixes" % (len(entries), len(pairs), glossary_hits))
    if failures:
        print("FAILED %d strings, none shipped:" % len(failures))
        for key, reason in failures:
            print("  %s: %s" % (key, reason))
    else:
        print("no verification failures")
    if not dry_run:
        print("wrote %s" % out_path)

    return {"payload": payload, "failures": failures, "glossary_hits": glossary_hits}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default="hi-IN", help="target language code")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="no network, echo templates")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    api_key = os.environ.get("SARVAM_API_KEY", "")
    if not api_key and not args.dry_run:
        print("SARVAM_API_KEY is not set", file=sys.stderr)
        return 2

    result = build(
        args.lang, api_key, dry_run=args.dry_run, model=args.model, out_path=args.out
    )
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
