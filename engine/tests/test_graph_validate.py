"""Tests for the graph validator.

Two jobs, and they are different jobs:

  - The SHIPPED graph must pass. `data/graph/nodes.json` is load bearing in the engine, the
    selection layer, the seeded demo and the API, so a regression in it is a regression
    everywhere. Those tests run against the real file and the real item bank.
  - Each CHECK must actually fire. A validator that returns an empty list on a broken graph is
    worse than no validator, because it is evidence of correctness that is not evidence of
    anything. So every check gets a deliberately broken fixture.

The fixtures below are minimal graphs built inline rather than copies of the real one, so that a
future edit to the real graph cannot silently make a check untestable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.graph_validate import (                                          # noqa: E402
    MIN_GRADEABLE_ITEMS,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    Problem,
    format_report,
    has_errors,
    load_gradeable_counts,
    validate,
    validate_file,
)
from engine.replay import load_graph                                         # noqa: E402
from engine.types import Graph                                              # noqa: E402

GRAPH_PATH = ROOT / "data" / "graph" / "nodes.json"
ITEMS_PATH = ROOT / "data" / "items" / "items.json"

# The six shortcut edges, as (node, redundant direct prerequisite). Measured and deliberately
# kept; see docs/audits/graph-invariants.md. Named here so that removing one is a test failure
# and not a quiet change of behaviour.
SHORTCUT_EDGES = {
    ("der.definition", "alg.fraction-arithmetic"),
    ("der.higher-order", "der.power-rule"),
    ("mv.chain-rule-multivar", "der.chain-rule"),
    ("mv.directional-derivative", "alg.vectors"),
    ("opt.critical-points", "alg.factoring"),
    ("ai.gradient-descent-step", "alg.vectors"),
}


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def real_graph() -> Graph:
    return load_graph(GRAPH_PATH)


@pytest.fixture(scope="module")
def real_counts() -> dict[str, int]:
    return load_gradeable_counts(ITEMS_PATH)


@pytest.fixture(scope="module")
def real_problems(real_graph, real_counts) -> list[Problem]:
    return validate(real_graph, real_counts)


def node(node_id, *, prereqs=(), encompasses=(), difficulty=0.0, hint="a hint"):
    """One node dict in the on-disk shape. `prereqs`/`encompasses` are (id, number) pairs."""
    return {
        "id": node_id,
        "title": node_id,
        "kind": "skill",
        "difficulty_b": difficulty,
        "blame_hint": hint,
        "prereqs": [{"id": i, "weight": w} for i, w in prereqs],
        "encompasses": [{"id": i, "credit": c} for i, c in encompasses],
    }


def make_graph(nodes, target=None) -> Graph:
    return Graph(nodes={n["id"]: n for n in nodes}, target_node=target)


def plenty(*node_ids) -> dict[str, int]:
    """An item bank that satisfies the coverage check, so other checks can be tested alone."""
    return {n: MIN_GRADEABLE_ITEMS for n in node_ids}


def codes(problems, severity=None) -> set[str]:
    return {p.code for p in problems if severity is None or p.severity == severity}


def find(problems, code) -> list[Problem]:
    return [p for p in problems if p.code == code]


# --------------------------------------------------------------------------- the shipped graph

class TestShippedGraph:
    """The real graph, the real item bank. These are the regression tests that matter."""

    def test_no_errors(self, real_problems):
        errors = [p for p in real_problems if p.severity == SEVERITY_ERROR]
        assert errors == [], "\n".join(str(p) for p in errors)

    def test_has_errors_agrees(self, real_problems):
        assert has_errors(real_problems) is False

    def test_validate_file_agrees_with_validate(self, real_problems):
        assert validate_file(GRAPH_PATH, ITEMS_PATH) == real_problems

    def test_reports_exactly_the_six_shortcut_edges(self, real_problems):
        """Reported, never failed on, and all six still present.

        If this fails with fewer than six, someone has transitively reduced the graph. Read
        docs/audits/graph-invariants.md before assuming that was an improvement: each of these
        edges was measured to change a node's status under a decayed ancestor.
        """
        shortcuts = find(real_problems, "SHORTCUT_EDGE")
        assert all(p.severity == SEVERITY_INFO for p in shortcuts)

        found = set()
        for problem in shortcuts:
            for _, prereq in SHORTCUT_EDGES:
                if f"{prereq!r}" in problem.message and problem.node_id:
                    found.add((problem.node_id, prereq))
        assert found == SHORTCUT_EDGES

    def test_difficulty_is_monotonic(self, real_graph, real_problems):
        """The three authored inversions are fixed and the field is now checked.

        This is what earns `difficulty_b` its place in the schema: selection never reads it, so
        this test is the only thing standing between it and being wrong again.
        """
        assert find(real_problems, "DIFFICULTY_INVERSION") == []
        for node_id, data in real_graph.nodes.items():
            for prereq, _w in real_graph.prereqs(node_id):
                assert data["difficulty_b"] > real_graph.nodes[prereq]["difficulty_b"], node_id

    @pytest.mark.parametrize("node_id, prereq, expected", [
        ("alg.solving-equations", "alg.factoring", -0.7),
        ("der.slope-interpretation", "der.definition", 0.6),
        ("der.constant-multiple-sum", "der.power-rule", -0.1),
    ])
    def test_the_three_corrected_values(self, real_graph, node_id, prereq, expected):
        assert real_graph.nodes[node_id]["difficulty_b"] == pytest.approx(expected)
        assert (real_graph.nodes[node_id]["difficulty_b"]
                > real_graph.nodes[prereq]["difficulty_b"])

    def test_every_node_has_a_blame_hint(self, real_graph, real_problems):
        assert find(real_problems, "MISSING_BLAME_HINT") == []
        for node_id, data in real_graph.nodes.items():
            assert data["blame_hint"].strip(), node_id

    def test_every_node_has_enough_gradeable_items(self, real_graph, real_counts, real_problems):
        assert find(real_problems, "TOO_FEW_ITEMS") == []
        for node_id in real_graph.nodes:
            assert real_counts.get(node_id, 0) >= MIN_GRADEABLE_ITEMS, node_id

    def test_graph_is_still_a_dag_with_no_dangling_ids(self, real_problems):
        for code in ("PREREQ_CYCLE", "ENCOMPASS_CYCLE", "SELF_LOOP",
                     "DANGLING_PREREQ", "DANGLING_ENCOMPASS", "DUPLICATE_ID"):
            assert find(real_problems, code) == [], code

    def test_no_orphaned_items(self, real_problems):
        assert find(real_problems, "ITEM_ORPHANED") == []

    def test_encompassings_lie_inside_the_prereq_closure(self, real_problems):
        assert find(real_problems, "ENCOMPASS_OUTSIDE_CLOSURE") == []

    def test_off_goal_path_nodes_are_warnings_not_errors(self, real_problems):
        """The slice deliberately carries topics the target does not need (trig, quotient rule).

        They exist so a real student's errors have somewhere honest to land. Warning, not error.
        """
        off = find(real_problems, "OFF_GOAL_PATH")
        assert off, "expected the known off-path nodes to still be reported"
        assert all(p.severity == SEVERITY_WARNING for p in off)

    def test_problems_are_sorted_errors_first(self, real_problems):
        rank = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
        seen = [rank[p.severity] for p in real_problems]
        assert seen == sorted(seen)


# --------------------------------------------------------------------------- each check fires

class TestStructuralChecks:

    def test_self_loop_in_prereqs(self):
        g = make_graph([node("a", prereqs=[("a", 1.0)])], target="a")
        assert "SELF_LOOP" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    def test_self_loop_in_encompasses(self):
        g = make_graph([node("a", encompasses=[("a", 0.3)])], target="a")
        assert "SELF_LOOP" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    def test_dangling_prereq(self):
        g = make_graph([node("a", prereqs=[("ghost", 1.0)])], target="a")
        problems = validate(g, plenty("a"))
        assert "DANGLING_PREREQ" in codes(problems, SEVERITY_ERROR)
        assert "ghost" in find(problems, "DANGLING_PREREQ")[0].message

    def test_dangling_encompass(self):
        g = make_graph([node("a", encompasses=[("ghost", 0.3)])], target="a")
        assert "DANGLING_ENCOMPASS" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    def test_prereq_cycle(self):
        g = make_graph([
            node("a", prereqs=[("c", 1.0)]),
            node("b", prereqs=[("a", 1.0)]),
            node("c", prereqs=[("b", 1.0)]),
        ], target="a")
        assert "PREREQ_CYCLE" in codes(validate(g, plenty("a", "b", "c")), SEVERITY_ERROR)

    def test_encompass_cycle(self):
        g = make_graph([
            node("root", difficulty=0.0),
            node("a", prereqs=[("root", 1.0)], encompasses=[("b", 0.3)], difficulty=1.0),
            node("b", prereqs=[("root", 1.0)], encompasses=[("a", 0.3)], difficulty=1.0),
        ], target="a")
        assert "ENCOMPASS_CYCLE" in codes(validate(g, plenty("root", "a", "b")), SEVERITY_ERROR)

    def test_a_long_prereq_cycle_does_not_blow_the_stack(self):
        """Cycle detection runs BEFORE acyclicity is known, so it must not recurse."""
        ids = [f"n{i}" for i in range(600)]
        nodes = [node(ids[i], prereqs=[(ids[i - 1], 1.0)]) for i in range(len(ids))]
        g = make_graph(nodes, target=ids[0])
        assert "PREREQ_CYCLE" in codes(validate(g, plenty(*ids)), SEVERITY_ERROR)

    def test_duplicate_id_is_reported_not_raised(self, tmp_path):
        path = tmp_path / "nodes.json"
        path.write_text(json.dumps({
            "target_node": "a",
            "nodes": [node("a"), node("a")],
        }), encoding="utf-8")
        problems = validate_file(path, ITEMS_PATH)
        assert "DUPLICATE_ID" in codes(problems, SEVERITY_ERROR)

    def test_a_clean_minimal_graph_has_no_errors(self):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("mid", prereqs=[("root", 0.9)], encompasses=[("root", 0.3)], difficulty=0.0),
            node("top", prereqs=[("mid", 1.0)], encompasses=[("mid", 0.3)], difficulty=1.0),
        ], target="top")
        problems = validate(g, plenty("root", "mid", "top"))
        assert not has_errors(problems), "\n".join(str(p) for p in problems)


class TestSemanticChecks:

    def test_encompass_outside_the_prereq_closure(self):
        """`b` credits `other`, which it does not depend on. Credit would keep a skill warm
        that nothing gates on, and can carry a node the student never demonstrated."""
        g = make_graph([
            node("root", difficulty=-1.0),
            node("other", difficulty=-1.0),
            node("b", prereqs=[("root", 1.0)], encompasses=[("other", 0.3)], difficulty=0.0),
        ], target="b")
        problems = validate(g, plenty("root", "other", "b"))
        assert "ENCOMPASS_OUTSIDE_CLOSURE" in codes(problems, SEVERITY_ERROR)

    def test_encompass_through_a_longer_prereq_path_is_accepted(self):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("mid", prereqs=[("root", 1.0)], difficulty=0.0),
            node("top", prereqs=[("mid", 1.0)], encompasses=[("root", 0.3)], difficulty=1.0),
        ], target="top")
        assert find(validate(g, plenty("root", "mid", "top")),
                    "ENCOMPASS_OUTSIDE_CLOSURE") == []

    @pytest.mark.parametrize("weight", [-0.1, 1.5])
    def test_prereq_weight_out_of_range(self, weight):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("a", prereqs=[("root", weight)], difficulty=0.0),
        ], target="a")
        assert "WEIGHT_RANGE" in codes(validate(g, plenty("root", "a")), SEVERITY_ERROR)

    def test_prereq_weight_zero_and_one_are_allowed(self):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("a", prereqs=[("root", 0.0)], difficulty=0.0),
            node("b", prereqs=[("root", 1.0)], difficulty=0.0),
        ], target="a")
        assert find(validate(g, plenty("root", "a", "b")), "WEIGHT_RANGE") == []

    @pytest.mark.parametrize("credit", [0.0, -0.2, 1.5])
    def test_encompass_credit_out_of_range(self, credit):
        """Zero is excluded deliberately: it refreshes last_seen while granting nothing, which
        defeats decay silently."""
        g = make_graph([
            node("root", difficulty=-1.0),
            node("a", prereqs=[("root", 1.0)], encompasses=[("root", credit)], difficulty=0.0),
        ], target="a")
        assert "CREDIT_RANGE" in codes(validate(g, plenty("root", "a")), SEVERITY_ERROR)

    def test_encompass_credit_of_one_is_allowed(self):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("a", prereqs=[("root", 1.0)], encompasses=[("root", 1.0)], difficulty=0.0),
        ], target="a")
        assert find(validate(g, plenty("root", "a")), "CREDIT_RANGE") == []


class TestReachabilityChecks:

    def test_no_roots(self):
        g = make_graph([
            node("a", prereqs=[("b", 1.0)]),
            node("b", prereqs=[("a", 1.0)]),
        ], target="a")
        assert "NO_ROOTS" in codes(validate(g, plenty("a", "b")), SEVERITY_ERROR)

    def test_unreachable_from_roots(self):
        """A component behind a cycle cannot be walked forward from any root."""
        g = make_graph([
            node("root", difficulty=-1.0),
            node("x", prereqs=[("y", 1.0)], difficulty=0.0),
            node("y", prereqs=[("x", 1.0)], difficulty=0.0),
        ], target="root")
        problems = validate(g, plenty("root", "x", "y"))
        assert {p.node_id for p in find(problems, "UNREACHABLE_FROM_ROOTS")} == {"x", "y"}

    def test_missing_target(self):
        g = make_graph([node("a")], target=None)
        assert "NO_TARGET" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    def test_target_not_in_graph(self):
        g = make_graph([node("a")], target="ghost")
        assert "NO_TARGET" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    def test_off_goal_path_is_a_warning(self):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("target", prereqs=[("root", 1.0)], difficulty=1.0),
            node("aside", prereqs=[("root", 1.0)], difficulty=1.0),
        ], target="target")
        problems = validate(g, plenty("root", "target", "aside"))
        off = find(problems, "OFF_GOAL_PATH")
        assert [p.node_id for p in off] == ["aside"]
        assert off[0].severity == SEVERITY_WARNING
        assert not has_errors(problems)


class TestShortcutReporting:

    def test_shortcut_is_reported_at_info_and_never_fails(self):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("mid", prereqs=[("root", 1.0)], difficulty=0.0),
            node("top", prereqs=[("mid", 1.0), ("root", 1.0)], difficulty=1.0),
        ], target="top")
        problems = validate(g, plenty("root", "mid", "top"))
        shortcuts = find(problems, "SHORTCUT_EDGE")
        assert len(shortcuts) == 1
        assert shortcuts[0].node_id == "top"
        assert shortcuts[0].severity == SEVERITY_INFO
        assert not has_errors(problems)

    def test_a_reduced_graph_reports_no_shortcut(self):
        g = make_graph([
            node("root", difficulty=-1.0),
            node("mid", prereqs=[("root", 1.0)], difficulty=0.0),
            node("top", prereqs=[("mid", 1.0)], difficulty=1.0),
        ], target="top")
        assert find(validate(g, plenty("root", "mid", "top")), "SHORTCUT_EDGE") == []

    def test_two_independent_prereqs_are_not_a_shortcut(self):
        g = make_graph([
            node("p", difficulty=-1.0),
            node("q", difficulty=-1.0),
            node("top", prereqs=[("p", 1.0), ("q", 1.0)], difficulty=1.0),
        ], target="top")
        assert find(validate(g, plenty("p", "q", "top")), "SHORTCUT_EDGE") == []


class TestAuthoringChecks:

    def test_difficulty_inversion(self):
        g = make_graph([
            node("root", difficulty=0.5),
            node("a", prereqs=[("root", 1.0)], difficulty=0.1),
        ], target="a")
        assert "DIFFICULTY_INVERSION" in codes(validate(g, plenty("root", "a")), SEVERITY_ERROR)

    def test_equal_difficulty_is_an_inversion(self):
        """Equal is not ordered. A node that adds a step is harder than the step it adds to."""
        g = make_graph([
            node("root", difficulty=0.3),
            node("a", prereqs=[("root", 1.0)], difficulty=0.3),
        ], target="a")
        assert "DIFFICULTY_INVERSION" in codes(validate(g, plenty("root", "a")), SEVERITY_ERROR)

    def test_missing_difficulty(self):
        broken = node("a")
        del broken["difficulty_b"]
        g = make_graph([broken], target="a")
        assert "MISSING_DIFFICULTY" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    def test_non_numeric_difficulty(self):
        broken = node("a")
        broken["difficulty_b"] = "hard"
        g = make_graph([broken], target="a")
        assert "MISSING_DIFFICULTY" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    @pytest.mark.parametrize("hint", [None, "", "   "])
    def test_missing_blame_hint(self, hint):
        broken = node("a", hint=hint)
        g = make_graph([broken], target="a")
        assert "MISSING_BLAME_HINT" in codes(validate(g, plenty("a")), SEVERITY_ERROR)

    def test_blame_hint_key_absent(self):
        broken = node("a")
        del broken["blame_hint"]
        g = make_graph([broken], target="a")
        assert "MISSING_BLAME_HINT" in codes(validate(g, plenty("a")), SEVERITY_ERROR)


class TestItemCoverage:

    @pytest.mark.parametrize("have", [0, 1, 2])
    def test_too_few_gradeable_items(self, have):
        g = make_graph([node("a")], target="a")
        problems = validate(g, {"a": have})
        assert "TOO_FEW_ITEMS" in codes(problems, SEVERITY_ERROR)
        assert str(have) in find(problems, "TOO_FEW_ITEMS")[0].message

    def test_exactly_three_is_enough(self):
        g = make_graph([node("a")], target="a")
        assert find(validate(g, {"a": MIN_GRADEABLE_ITEMS}), "TOO_FEW_ITEMS") == []

    def test_a_node_with_no_items_at_all(self):
        """The alg.vectors failure mode: no bank, so it never leaves the frontier."""
        g = make_graph([
            node("root", difficulty=-1.0),
            node("a", prereqs=[("root", 1.0)], difficulty=0.0),
        ], target="a")
        problems = validate(g, {"root": 5})
        assert [p.node_id for p in find(problems, "TOO_FEW_ITEMS")] == ["a"]

    def test_items_tagged_to_an_unknown_node_are_warned_about(self):
        g = make_graph([node("a")], target="a")
        problems = validate(g, {"a": 5, "retired.node": 4})
        orphans = find(problems, "ITEM_ORPHANED")
        assert len(orphans) == 1
        assert orphans[0].severity == SEVERITY_WARNING
        assert not has_errors(problems)

    def test_gradeable_counts_exclude_ungradeable_items(self, tmp_path):
        path = tmp_path / "items.json"
        path.write_text(json.dumps({"items": [
            {"item_id": "i1", "node_id": "a", "gradeable": True},
            {"item_id": "i2", "node_id": "a", "gradeable": False},
            {"item_id": "i3", "node_id": "a"},
            {"item_id": "i4", "node_id": "b", "gradeable": True},
        ]}), encoding="utf-8")
        assert load_gradeable_counts(path) == {"a": 1, "b": 1}

    def test_default_item_path_reads_the_shipped_bank(self, real_graph):
        """`validate` with no counts must check what actually ships, not skip the check."""
        problems = validate(real_graph)
        assert find(problems, "TOO_FEW_ITEMS") == []


# --------------------------------------------------------------------------- the CLI

class TestCommandLine:

    def test_runs_as_a_module_and_passes_on_the_shipped_graph(self):
        result = subprocess.run(
            [sys.executable, "-m", "engine.graph_validate"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS: no errors" in result.stdout

    def test_reports_the_shortcuts_on_stdout(self):
        result = subprocess.run(
            [sys.executable, "-m", "engine.graph_validate"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.stdout.count("SHORTCUT_EDGE") == len(SHORTCUT_EDGES)

    def test_exits_nonzero_on_a_broken_graph(self, tmp_path):
        path = tmp_path / "nodes.json"
        path.write_text(json.dumps({
            "target_node": "a",
            "nodes": [node("a", prereqs=[("ghost", 1.0)])],
        }), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "engine.graph_validate", "--graph", str(path)],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "FAIL" in result.stdout
        assert "DANGLING_PREREQ" in result.stdout

    def test_report_names_every_finding(self, real_graph, real_problems):
        report = format_report(real_problems, real_graph)
        for problem in real_problems:
            assert problem.code in report
        assert "PASS: no errors" in report

    def test_report_says_fail_when_there_are_errors(self):
        problems = [Problem(SEVERITY_ERROR, "X", "broken", "a")]
        assert "FAIL: 1 error(s)" in format_report(problems)
