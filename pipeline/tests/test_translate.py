"""Tests for the translation pipeline. No network: every "translation" here is faked.

The property under test is the one from learning-design.md section 13: maths must go
through a translation untouched. If any of these fail, Hindi output is not safe to ship.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.translate import (  # noqa: E402
    ITEMS_PATH,
    I18N_DIR,
    NODES_PATH,
    apply_glossary,
    extract_code_spans,
    extract_math,
    mask_named_placeholders,
    match_terminal_punctuation,
    normalise_placeholders,
    placeholders,
    protect,
    restore_math,
    translate_batch,
    verify_placeholders,
)


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


ITEMS = load(ITEMS_PATH)["items"]
NODES = load(NODES_PATH)["nodes"]
EN = load(I18N_DIR / "en.json")["entries"]
HI = load(I18N_DIR / "hi.json")["entries"]

ALL_STEMS = [item["stem_latex"] for item in ITEMS]
GENERATED_STEMS = {i["item_id"]: i["stem_latex"] for i in ITEMS if i["source"] == "generated"}
NODE_TITLES = {n["id"]: n["title"] for n in NODES}


# --------------------------------------------------------------------------------------
# extract_math / restore_math round trip
# --------------------------------------------------------------------------------------


def test_item_bank_is_the_expected_size():
    assert len(ALL_STEMS) == len(ITEMS)
    assert len(GENERATED_STEMS) == sum(1 for i in ITEMS if i['source'] == 'generated')
    # derived, not hardcoded: a graph change should fail the COVERAGE test below,
    # which names the missing translation, not this one, which just says a number moved
    assert len(NODE_TITLES) == len(NODES)


@pytest.mark.parametrize("stem", ALL_STEMS, ids=[i["item_id"] for i in ITEMS])
def test_round_trip_is_exact_for_every_item_stem(stem):
    """restore_math(extract_math(t)) == t for all 376 stems, openstax included."""
    template, math = extract_math(stem)
    assert restore_math(template, math) == stem


def test_round_trip_over_every_string_the_pipeline_touches():
    corpus = ALL_STEMS + list(NODE_TITLES.values()) + list(EN.values())
    for text in corpus:
        template, math = extract_math(text)
        assert restore_math(template, math) == text, text


def test_templates_hide_every_dollar_span_from_the_translator():
    """No LaTeX must survive into the string that is sent to the translation model."""
    for stem in ALL_STEMS:
        template, math = extract_math(stem)
        # A template may keep one unmatched literal '$' (two malformed openstax stems have
        # an odd number of delimiters), but never a complete $...$ pair.
        assert template.count("$") == len(_unmatched_dollars(stem))
        assert len(placeholders(template)) == len(math)
        for span in math:
            assert span.startswith("$") and span.endswith("$")


def _unmatched_dollars(text: str) -> list[int]:
    from pipeline.translate import _dollar_positions

    positions = _dollar_positions(text)
    return positions[len(positions) - (len(positions) % 2) :]


def test_text_with_no_maths_is_left_alone():
    text = "Come back tomorrow to keep your streak"
    assert extract_math(text) == (text, [])


def test_escaped_dollars_are_not_delimiters():
    text = r"A price of \$5 and a formula $x^2$ and another \$7"
    template, math = extract_math(text)
    assert math == ["$x^2$"]
    assert template == r"A price of \$5 and a formula {{m1}} and another \$7"
    assert restore_math(template, math) == text


def test_odd_number_of_delimiters_still_round_trips():
    text = "Decide if $f(x)$ is continuous at $x=0"
    template, math = extract_math(text)
    assert math == ["$f(x)$"]
    assert restore_math(template, math) == text


def test_placeholders_are_numbered_in_order():
    text = r"If $u = x^2$ and $v = \sin x$, find $\frac{du}{dv}$."
    template, math = extract_math(text)
    assert template == "If {{m1}} and {{m2}}, find {{m3}}."
    assert len(math) == 3
    assert restore_math(template, math) == text


def test_repeated_span_is_restored_at_every_position():
    text = "Compare $x^2$ with $x^2$."
    template, math = extract_math(text)
    assert template == "Compare {{m1}} with {{m2}}."
    assert restore_math(template, math) == text


# --------------------------------------------------------------------------------------
# Placeholders survive a translation, and a bad translation is caught
# --------------------------------------------------------------------------------------


def reorder_words(template: str) -> str:
    """A fake translation: reverses the word order, keeping placeholders intact.

    Real Hindi is verb-final, so the placeholders genuinely do move relative to the prose.
    This is the harmless case, and it must pass verification.
    """
    return " ".join(reversed(template.split(" ")))


def drop_first_placeholder(template: str) -> str:
    """A fake broken translation: the model swallowed a placeholder."""
    return re.sub(r"\{\{m\d+\}\}", "", template, count=1)


def duplicate_first_placeholder(template: str) -> str:
    first = placeholders(template)[0]
    return template.replace(first, first + " " + first, 1)


def test_placeholders_survive_a_reordering_translation():
    checked = 0
    for stem in GENERATED_STEMS.values():
        template, math = extract_math(stem)
        translated = reorder_words(template)
        assert verify_placeholders(template, translated) == [], stem
        restored = restore_math(translated, math)
        # every maths span is back, exactly once, unaltered
        assert sorted(extract_math(restored)[1]) == sorted(math)
        checked += 1
    assert checked == len(GENERATED_STEMS)


def test_a_dropped_placeholder_is_reported_as_a_failure():
    template, _ = extract_math("Differentiate $x \\sin x$ using the product rule")
    broken = drop_first_placeholder(template)
    problems = verify_placeholders(template, broken)
    assert problems == ["placeholder {{m1}} was dropped"]


def test_every_generated_stem_detects_a_dropped_placeholder():
    """The check must not be accidentally satisfiable on any real stem."""
    for stem in GENERATED_STEMS.values():
        template, math = extract_math(stem)
        if not math:
            continue
        problems = verify_placeholders(template, drop_first_placeholder(template))
        assert problems, stem


def test_a_duplicated_placeholder_is_reported_as_a_failure():
    template = "Find {{m1}} for {{m2}}."
    problems = verify_placeholders(template, duplicate_first_placeholder(template))
    assert problems == ["placeholder {{m1}} appears 2 times, expected 1"]


def test_an_invented_placeholder_is_reported_as_a_failure():
    problems = verify_placeholders("Simplify {{m1}}.", "{{m1}} और {{m2}} को सरल कीजिए।")
    assert problems == ["translator invented placeholder {{m2}}"]


def test_a_clean_translation_reports_no_problems():
    assert verify_placeholders("Simplify {{m1}}.", "{{m1}} को सरल कीजिए।") == []


def test_placeholder_whitespace_and_devanagari_digits_are_repaired():
    assert normalise_placeholders("{{ m1 }} को सरल कीजिए") == "{{m1}} को सरल कीजिए"
    assert normalise_placeholders("{{m१}} को सरल कीजिए") == "{{m1}} को सरल कीजिए"
    assert verify_placeholders("Simplify {{m1}}.", normalise_placeholders("{{ m1 }} सरल")) == []


# --------------------------------------------------------------------------------------
# Named runtime placeholders
# --------------------------------------------------------------------------------------


def test_named_placeholders_are_masked_and_restored():
    template, math = extract_math("On your path to {{goal}}")
    masked, named = mask_named_placeholders(template, start=len(math))
    assert masked == "On your path to {{m1}}"
    assert named == ["{{goal}}"]
    assert restore_math(masked, math + named) == "On your path to {{goal}}"


def test_masking_continues_the_numbering_after_the_maths():
    text = "Simplify $x^2$ for {{goal}}"
    template, math = extract_math(text)
    masked, named = mask_named_placeholders(template, start=len(math))
    assert masked == "Simplify {{m1}} for {{m2}}"
    assert restore_math(masked, math + named) == text


def test_masking_leaves_existing_math_placeholders_alone():
    masked, named = mask_named_placeholders("Find {{m1}} for {{m2}}.", start=2)
    assert masked == "Find {{m1}} for {{m2}}."
    assert named == []


# --------------------------------------------------------------------------------------
# Raw notation that is not wrapped in dollars
# --------------------------------------------------------------------------------------


def test_code_spans_are_masked_out_of_a_generated_stem():
    template, spans = extract_code_spans("Two-layer network: output = log(w2*(w1*3) + 1)**2.")
    assert template == "Two-layer network: {{m1}}."
    assert spans == ["output = log(w2*(w1*3) + 1)**2"]


def test_code_span_stops_where_english_prose_resumes():
    template, spans = extract_code_spans("Loss L = (a*x - 7)^2 for a model parameter a.")
    assert template == "Loss {{m1}} for a model parameter a."
    assert spans == ["L = (a*x - 7)^2"]


def test_unicode_partial_derivative_is_masked():
    template, spans = extract_code_spans("Loss L = (a*x + b - 10)^2, find ∂L/∂b.")
    assert template == "Loss {{m1}}, find {{m2}}."
    assert spans == ["L = (a*x + b - 10)^2", "∂L/∂b"]


def test_code_span_numbering_continues_from_start():
    template, spans = extract_code_spans("output = w**2 here", start=4)
    assert template == "{{m5}} here"
    assert spans == ["output = w**2"]


def test_code_span_extraction_never_swallows_a_placeholder():
    template, spans = extract_code_spans("The loss is L = {{m1}} today", start=1)
    assert "{{m1}}" in template
    assert all("{{" not in span for span in spans)


def test_prose_with_no_notation_is_left_alone():
    for text in ["Here is the first step.", "Come back tomorrow", "Which idea applies here?"]:
        assert extract_code_spans(text) == (text, [])


def test_protect_round_trips_every_string_the_pipeline_touches():
    corpus = ALL_STEMS + list(NODE_TITLES.values()) + list(EN.values())
    for text in corpus:
        template, spans, math = protect(text)
        assert restore_math(template, spans) == text, text
        assert math == extract_math(text)[1]


def test_protect_leaves_no_notation_in_the_string_sent_to_the_translator():
    """The template must not contain LaTeX commands, exponent operators or partials."""
    for stem in GENERATED_STEMS.values():
        template, _, _ = protect(stem)
        assert "\\" not in template, stem
        assert "**" not in template, stem
        assert "∂" not in template, stem
        assert "$" not in template, stem


def test_protect_survives_a_reordering_translation_with_notation_intact():
    for stem in GENERATED_STEMS.values():
        template, spans, _ = protect(stem)
        translated = reorder_words(template)
        assert verify_placeholders(template, translated) == [], stem
        restored = restore_math(translated, spans)
        for span in spans:
            assert restored.count(span) == stem.count(span), stem


# --------------------------------------------------------------------------------------
# translate_batch behaviour, with a fake client
# --------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, text: str):
        self.translated_text = text


class FakeClient:
    """Stands in for SarvamAI. Counts calls so deduplication is observable."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[str] = []
        self.text = self

    def translate(self, *, input, source_language_code, target_language_code, model):
        self.calls.append(input)
        return FakeResponse(self.behaviour(input, len(self.calls)))


def test_translate_batch_preserves_order_and_deduplicates():
    client = FakeClient(lambda text, _n: "हिन्दी " + text)
    strings = ["Simplify {{m1}}.", "Factor {{m1}}.", "Simplify {{m1}}."]
    out = translate_batch(strings, "hi-IN", "key", client=client, max_workers=1)
    assert out == ["हिन्दी Simplify {{m1}}.", "हिन्दी Factor {{m1}}.", "हिन्दी Simplify {{m1}}."]
    assert len(client.calls) == 2  # the repeat was translated once


def test_translate_batch_returns_none_rather_than_english_on_failure():
    """A failed item must never silently ship as English."""

    def always_fails(text, _n):
        raise RuntimeError("boom")

    client = FakeClient(always_fails)
    out = translate_batch(["Simplify {{m1}}."], "hi-IN", "key", client=client, retries=1)
    assert out == [None]


def test_translate_batch_rejects_a_response_that_came_back_in_english():
    client = FakeClient(lambda text, _n: text)  # echoes the input, a real Sarvam failure mode
    out = translate_batch(["Factor {{m1}}."], "hi-IN", "key", client=client, retries=1)
    assert out == [None]


def test_translate_batch_rejects_an_empty_response():
    client = FakeClient(lambda text, _n: "")
    out = translate_batch(["Factor {{m1}}."], "hi-IN", "key", client=client, retries=1)
    assert out == [None]


def test_translate_batch_retries_before_giving_up():
    def fails_once(text, n):
        if n == 1:
            raise RuntimeError("transient")
        return "सरल कीजिए {{m1}}"

    client = FakeClient(fails_once)
    out = translate_batch(["Simplify {{m1}}."], "hi-IN", "key", client=client, retries=3)
    assert out == ["सरल कीजिए {{m1}}"]
    assert len(client.calls) == 2


def test_translate_batch_does_not_flag_a_placeholder_only_string():
    """A string with no real prose has nothing to translate, so no Devanagari is expected."""
    client = FakeClient(lambda text, _n: text)
    assert translate_batch(["{{m1}}"], "hi-IN", "key", client=client, retries=1) == ["{{m1}}"]


def test_translate_batch_on_empty_input():
    assert translate_batch([], "hi-IN", "key", client=FakeClient(lambda t, n: t)) == []


# --------------------------------------------------------------------------------------
# The shipped hi.json
# --------------------------------------------------------------------------------------


def english_source_for(key: str) -> str:
    entity_type, entity_id = key.split(":", 1)
    if entity_type == "node_title":
        return NODE_TITLES[entity_id]
    if entity_type == "item_stem":
        return GENERATED_STEMS[entity_id]
    if entity_type == "ui":
        return EN[key]
    raise AssertionError("unknown entity_type %r in key %r" % (entity_type, key))


def test_hi_json_has_the_shape_the_translations_table_expects():
    payload = load(I18N_DIR / "hi.json")
    assert set(payload) == {"lang", "entries"}
    assert payload["lang"] == "hi-IN"
    for key in payload["entries"]:
        entity_type, entity_id = key.split(":", 1)
        assert entity_type in {"node_title", "ui", "item_stem"}
        assert entity_id


def test_hi_json_covers_everything_that_was_meant_to_be_translated():
    assert {k for k in HI if k.startswith("node_title:")} == {
        "node_title:%s" % node_id for node_id in NODE_TITLES
    }
    assert {k for k in HI if k.startswith("item_stem:")} == {
        "item_stem:%s" % item_id for item_id in GENERATED_STEMS
    }
    assert {k for k in HI if k.startswith("ui:")} == set(EN)
    assert len(HI) == len(NODE_TITLES) + len(GENERATED_STEMS) + len(EN)


def test_no_openstax_stems_were_translated():
    openstax = {i["item_id"] for i in ITEMS if i["source"] == "openstax"}
    assert not any(k.split(":", 1)[1] in openstax for k in HI if k.startswith("item_stem:"))


def test_blame_hints_were_not_translated():
    """blame_hint is prompt-only and never shown to a student."""
    hints = {n["blame_hint"] for n in NODES if n.get("blame_hint")}
    assert hints, "expected some blame hints in the fixture"
    assert not any(k.startswith("blame_hint") for k in HI)
    for hint in hints:
        assert hint not in HI.values()


def test_every_shipped_entry_has_the_same_maths_spans_as_its_english_source():
    """The whole point of the exercise. Same spans, same count, byte identical."""
    checked = 0
    for key, hindi in HI.items():
        english = english_source_for(key)
        _, expected = extract_math(english)
        _, actual = extract_math(hindi)
        assert sorted(actual) == sorted(expected), key
        for span in expected:
            assert hindi.count(span) == english.count(span), key
        checked += 1
    assert checked == len(HI)


def test_every_shipped_entry_keeps_its_named_runtime_placeholders():
    for key, hindi in HI.items():
        english = english_source_for(key)
        template, math = extract_math(english)
        _, named = mask_named_placeholders(template, start=len(math))
        for name in named:
            assert hindi.count(name) == english.count(name), key


def test_every_shipped_entry_keeps_its_raw_notation_verbatim():
    """The non-LaTeX notation too: `output = ...`, `∂L/∂b`, `w=2, y_true=5`."""
    checked = 0
    for key, hindi in HI.items():
        english = english_source_for(key)
        _, spans, _ = protect(english)
        for span in spans:
            assert hindi.count(span) == english.count(span), (key, span)
            checked += 1
    assert checked >= 221  # at least every maths span, plus the code and named spans


def test_no_shipped_entry_leaked_a_template_placeholder():
    """Every {{mN}} must have been substituted back before shipping."""
    for key, hindi in HI.items():
        assert not re.search(r"\{\{m\d+\}\}", hindi), key


def test_no_shipped_entry_is_still_english():
    """Guards the "a failed item silently becomes English" regression."""
    devanagari = re.compile(r"[ऀ-ॿ]")
    for key, hindi in HI.items():
        english = english_source_for(key)
        template, _ = extract_math(english)
        prose = re.sub(r"[^A-Za-z]", "", re.sub(r"\{\{[^}]*\}\}", " ", template))
        if len(prose) < 3:
            continue
        assert devanagari.search(hindi), key
        assert hindi != english, key


# --------------------------------------------------------------------------------------
# Glossary and punctuation post-processing
# --------------------------------------------------------------------------------------


def test_glossary_normalises_maths_terminology():
    text, hits = apply_glossary("{{m1}} का उत्पाद नियम और चेन नियम")
    assert text == "{{m1}} का गुणनफल नियम और श्रृंखला नियम"
    assert hits == 2


def test_glossary_cannot_touch_a_formula():
    """It runs on the template, so the maths is not even present when it runs."""
    template = "{{m1}} का उत्पाद नियम"
    text, _ = apply_glossary(template)
    assert restore_math(text, ["$x \\cdot \\sin x$"]) == "$x \\cdot \\sin x$ का गुणनफल नियम"


def test_glossary_leaves_clean_text_alone():
    assert apply_glossary("{{m1}} को सरल कीजिए") == ("{{m1}} को सरल कीजिए", 0)


def test_terminal_danda_is_dropped_only_when_the_source_has_no_full_stop():
    assert match_terminal_punctuation("Take a photo", "एक फोटो लें।") == "एक फोटो लें"
    assert match_terminal_punctuation("Simplify {{m1}}.", "{{m1}} सरल कीजिए।") == "{{m1}} सरल कीजिए।"
    assert match_terminal_punctuation("Is this right?", "क्या ये सही है?") == "क्या ये सही है?"


def test_shipped_titles_do_not_end_in_a_stray_danda():
    for key, hindi in HI.items():
        if not key.startswith("node_title:"):
            continue
        assert not hindi.rstrip().endswith("।"), key
