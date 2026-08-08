"""Diagnosis tests.

Everything here except the last test is offline. The cache path is tested by making the network
call itself an assertion failure: if `diagnose` reaches `_call` on a cache hit, the test fails
rather than quietly costing 4 seconds and a coin flip. That is the property the demo depends on
(learning-design.md section 16: `seed` does not pin the output).

The single live test is skipped without SARVAM_API_KEY, so `python3 -m pytest services/tests -q`
passes on a machine with no key at all.
"""

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.types import Item                              # noqa: E402
from services import diagnose as dg                        # noqa: E402

CACHE = _ROOT / "data" / "demo" / "diagnosis_cache.json"


@pytest.fixture
def graph():
    return dg.load_graph()


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to reach the API is a test failure."""
    def boom(*a, **kw):
        raise AssertionError("network call attempted; this path must be served from cache")
    monkeypatch.setattr(dg, "_call", boom)


# --------------------------------------------------------------------------- the demo case

def test_demo_case_is_cached(no_network, graph):
    d = dg.diagnose(dg.DEMO_ITEM, dg.DEMO_STUDENT_WORK, dg.DEMO_EXPECTED_ANSWER, graph,
                    api_key="not-used", cache_path=CACHE)
    assert d.from_cache is True
    assert d.error is None
    assert d.blamed_node == dg.DEMO_EXPECTED_NODE == "alg.sign-distribution"
    assert d.correct is False
    assert d.student_message.strip()
    assert 0.0 <= d.confidence <= 1.0


def test_demo_case_is_cached_without_an_api_key(no_network, graph):
    """The demo must not depend on a key being present on the laptop that runs it."""
    d = dg.diagnose(dg.DEMO_ITEM, dg.DEMO_STUDENT_WORK, dg.DEMO_EXPECTED_ANSWER, graph,
                    api_key=None, cache_path=CACHE)
    assert d.from_cache and d.blamed_node == "alg.sign-distribution"


def test_cache_hit_survives_reindented_working(no_network, graph):
    """OCR and the UI will not hand us byte-identical whitespace."""
    messy = "  f'(x) = [ (2)(x-3) - (2x+1)(1) ] / (x-3)^2\n\n\t= [ 2x - 6 - 2x + 1 ] / (x-3)^2\n" \
            "   = -5 / (x-3)^2  "
    d = dg.diagnose(dg.DEMO_ITEM, messy, dg.DEMO_EXPECTED_ANSWER, graph,
                    api_key=None, cache_path=CACHE)
    assert d.from_cache and d.blamed_node == "alg.sign-distribution"


def test_cache_hit_survives_a_renamed_item(no_network, graph):
    """Secondary index on the working alone. Whoever wires the UI may give the demo item a
    different id; the demo must not silently fall through to a live call because of that."""
    renamed = Item("some-other-id", "der.quotient-rule", "stem")
    d = dg.diagnose(renamed, dg.DEMO_STUDENT_WORK, dg.DEMO_EXPECTED_ANSWER, graph,
                    api_key=None, cache_path=CACHE)
    assert d.from_cache and d.blamed_node == "alg.sign-distribution"


def test_cached_entry_records_its_own_stability():
    """The cache is evidence, not decoration: it has to say how the live model behaved."""
    import json
    cache = json.loads(CACHE.read_text())
    entry = next(iter(cache["entries"].values()))
    assert entry["expect_node"] == "alg.sign-distribution"
    assert entry["diagnosis"]["blamed_node"] == "alg.sign-distribution"
    agreed, total = entry["stability"]["agreement"].split("/")
    assert int(total) >= 3 and int(agreed) >= 1


# --------------------------------------------------------------------------- misses and errors

def test_cache_miss_without_a_key_returns_an_error_not_an_exception(graph):
    unknown = Item("nope", "der.chain-rule", "stem")
    d = dg.diagnose(unknown, "some working the cache has never seen", "x", graph,
                    api_key=None, cache_path=CACHE)
    assert d.from_cache is False
    assert d.error and "api key" in d.error
    assert d.blamed_node is None


def test_missing_cache_file_is_not_fatal(graph, tmp_path):
    d = dg.diagnose(dg.DEMO_ITEM, dg.DEMO_STUDENT_WORK, "x", graph, api_key=None,
                    cache_path=tmp_path / "does-not-exist.json")
    assert d.error and d.from_cache is False


# --------------------------------------------------------------------------- prompt shape

def test_tool_enum_is_the_real_node_ids(graph):
    tool = dg.build_tool(graph)
    enum = tool["function"]["parameters"]["properties"]["blamed_node"]["enum"]
    assert set(enum) == set(graph.nodes) | {None}
    assert "alg.sign-distribution" in enum
    assert tool["function"]["parameters"]["required"] == [
        "correct", "blamed_node", "student_message", "confidence"]


def test_blame_hints_are_rendered_into_the_node_list(graph):
    """Section 16: this one line moved c09 from 0/3 to 3/3 and accuracy from 83% to 90%."""
    rendered = dg.render_nodes(graph)
    hinted = [n for n in graph.nodes.values() if n.get("blame_hint")]
    assert len(hinted) >= 12
    for node in hinted:
        assert f"[{node['blame_hint']}]" in rendered
    assert "alg.sign-distribution: Distributing a negative" in rendered


def test_system_prompt_never_asks_the_model_to_decide_correctness(graph):
    """A guard on the architecture, not on the wording. `grading` owns correctness."""
    msgs = dg.build_messages(dg.DEMO_ITEM, dg.DEMO_STUDENT_WORK, dg.DEMO_EXPECTED_ANSWER, graph)
    assert "Never invent an error." in msgs[0]["content"]
    assert dg.DEMO_STUDENT_WORK in msgs[1]["content"]
    assert "der.quotient-rule" in msgs[1]["content"]


def test_unknown_node_id_is_dropped_but_the_message_is_kept(monkeypatch, graph):
    """Structurally impossible via the enum, so this is belt and braces. Section 16 found the
    student_message is right even when the routing is wrong, so the message survives."""
    monkeypatch.setattr(dg, "_call", lambda *a, **kw: (
        {"correct": False, "blamed_node": "alg.invented-node", "student_message": "hello",
         "confidence": 0.9}, 1.0, None))
    d = dg.diagnose(dg.DEMO_ITEM, "work", "ans", graph, api_key="k")
    assert d.blamed_node is None
    assert d.student_message == "hello"
    assert d.error and "unknown node id" in d.error


# --------------------------------------------------------------------------- live

@pytest.mark.skipif(not os.environ.get("SARVAM_API_KEY"),
                    reason="needs SARVAM_API_KEY; the rest of the suite is offline")
def test_live_diagnosis_of_the_demo_case(graph):
    """One real call. Asserts shape and node validity, NOT a specific node: section 16 measured
    that the same input can route differently between runs, and a test that fails on a coin flip
    is worse than no test. Stability is recorded in the cache file instead."""
    d = dg.diagnose(dg.DEMO_ITEM, dg.DEMO_STUDENT_WORK, dg.DEMO_EXPECTED_ANSWER, graph,
                    api_key=os.environ["SARVAM_API_KEY"], problem=dg.DEMO_PROBLEM)
    assert d.error is None, d.error
    assert d.from_cache is False
    assert d.blamed_node in graph.nodes
    assert d.student_message.strip()
    assert d.latency_s > 0
