"""An executable checklist for the knowledge graph.

Modelled on `core_engine.md` section 7, but matched to what THIS engine actually consumes rather
than to that spec's axioms. Where the two disagree the disagreement is deliberate and is recorded
in `docs/audits/graph-invariants.md`; the largest one is transitive reduction, which we do not
enforce and must not, because our `status` reads DIRECT prerequisites only. See SHORTCUT_EDGE
below.

Two entry points:

    validate(graph, items_by_node=None) -> list[Problem]     importable, for tests
    python3 -m engine.graph_validate                          prints a pass/fail report

Severity decides the exit code, not whether a finding is interesting:

    error    the graph is wrong and something downstream will misbehave. Exit 1.
    warning  worth a human look, but a deliberate state of the graph today. Exit 0.
    info     recorded so it cannot be forgotten. Exit 0.

The point of the info tier is the six shortcut edges. They were measured (see the audit doc) and
kept on purpose, so failing on them would be wrong, but leaving them invisible is how they got
argued about from memory in the first place. They are reported on every run.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from engine.replay import load_graph
from engine.types import Graph

DEFAULT_GRAPH_PATH = "data/graph/nodes.json"
DEFAULT_ITEMS_PATH = "data/items/items.json"

# A node with fewer gradeable items than this can never leave the frontier: selection has nothing
# safe to serve it, so it stays unmastered forever and silently blocks everything above it. That
# is not hypothetical; it is what `alg.vectors` did before it had a bank.
MIN_GRADEABLE_ITEMS = 3

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Problem:
    """One finding. `node_id` is None for findings about the graph as a whole."""

    severity: str
    code: str
    message: str
    node_id: Optional[str] = None

    def __str__(self) -> str:
        where = f" [{self.node_id}]" if self.node_id else ""
        return f"{self.severity.upper():7s} {self.code}{where}: {self.message}"


# --------------------------------------------------------------------------- graph helpers

def _prereq_ids(graph: Graph, node_id: str) -> list[str]:
    return [p for p, _w in graph.prereqs(node_id)]


def _encompass_ids(graph: Graph, node_id: str) -> list[str]:
    return [e for e, _c in graph.encompasses(node_id)]


def _closure(graph: Graph, node_id: str, edges) -> set[str]:
    """Transitive closure of `node_id` under `edges`, excluding the node itself.

    Iterative rather than recursive: a cycle in the data must produce a Problem, not a
    RecursionError, and this function runs before acyclicity has been established.
    """
    seen: set[str] = set()
    stack = [n for n in edges(graph, node_id) if n in graph.nodes]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(n for n in edges(graph, cur) if n in graph.nodes)
    seen.discard(node_id)
    return seen


def _find_cycle(graph: Graph, edges) -> Optional[list[str]]:
    """A cycle under `edges` as a node list, or None. Iterative DFS with an explicit stack."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in graph.nodes}

    for root in sorted(graph.nodes):
        if colour[root] != WHITE:
            continue
        path: list[str] = []
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                colour[node] = BLACK
                path.pop()
                continue
            if colour[node] == GREY:
                # Should not happen: a GREY node is never re-pushed without being reported.
                continue
            if colour[node] == BLACK:
                continue
            colour[node] = GREY
            path.append(node)
            stack.append((node, True))
            for nxt in sorted(edges(graph, node)):
                if nxt not in graph.nodes:
                    continue
                if colour[nxt] == GREY:
                    return path[path.index(nxt):] + [nxt]
                if colour[nxt] == WHITE:
                    stack.append((nxt, False))
    return None


def _roots(graph: Graph) -> list[str]:
    return sorted(n for n in graph.nodes if not _prereq_ids(graph, n))


def _dependants(graph: Graph) -> dict[str, list[str]]:
    """node -> the nodes that list it as a direct prerequisite."""
    out: dict[str, list[str]] = {n: [] for n in graph.nodes}
    for node_id in graph.nodes:
        for prereq in _prereq_ids(graph, node_id):
            if prereq in out:
                out[prereq].append(node_id)
    return out


# --------------------------------------------------------------------------- item bank

def load_gradeable_counts(path: str | Path = DEFAULT_ITEMS_PATH) -> dict[str, int]:
    """node_id -> number of GRADEABLE items tagged to it.

    `engine.selection.load_items` drops the `gradeable` flag, and that flag is exactly what this
    check is about: an ungradeable item is not a usable repetition, it is a trap that marks a
    correct student wrong. So the raw file is read here rather than reusing that loader.
    """
    resolved = Path(path)
    if not resolved.is_absolute() and not resolved.exists():
        resolved = _REPO_ROOT / path
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    records = raw["items"] if isinstance(raw, dict) else raw

    counts: dict[str, int] = {}
    for rec in records:
        if not rec.get("gradeable"):
            continue
        counts[rec["node_id"]] = counts.get(rec["node_id"], 0) + 1
    return counts


# --------------------------------------------------------------------------- checks

def _check_structure(graph: Graph) -> list[Problem]:
    """Self loops, dangling references, and the two acyclicity axioms."""
    problems: list[Problem] = []

    for node_id in sorted(graph.nodes):
        for prereq in _prereq_ids(graph, node_id):
            if prereq == node_id:
                problems.append(Problem(
                    SEVERITY_ERROR, "SELF_LOOP",
                    "lists itself as a prerequisite", node_id))
            elif prereq not in graph.nodes:
                problems.append(Problem(
                    SEVERITY_ERROR, "DANGLING_PREREQ",
                    f"prerequisite {prereq!r} is not a node in the graph", node_id))

        for enc in _encompass_ids(graph, node_id):
            if enc == node_id:
                problems.append(Problem(
                    SEVERITY_ERROR, "SELF_LOOP",
                    "encompasses itself", node_id))
            elif enc not in graph.nodes:
                problems.append(Problem(
                    SEVERITY_ERROR, "DANGLING_ENCOMPASS",
                    f"encompasses {enc!r}, which is not a node in the graph", node_id))

    cycle = _find_cycle(graph, _prereq_ids)
    if cycle:
        problems.append(Problem(
            SEVERITY_ERROR, "PREREQ_CYCLE",
            "prerequisites are not a DAG: " + " -> ".join(cycle)))

    cycle = _find_cycle(graph, _encompass_ids)
    if cycle:
        problems.append(Problem(
            SEVERITY_ERROR, "ENCOMPASS_CYCLE",
            "encompassings are not a DAG: " + " -> ".join(cycle)))

    return problems


def _check_encompass_within_prereq_closure(graph: Graph) -> list[Problem]:
    """Every encompassed node must be a prerequisite ancestor.

    `core_engine.md` section 2.2 explicitly says the two relations are independent and that
    containment is NOT a rule at its scale. We require it anyway, because `apply_implicit` grants
    a node credit and refreshes its `last_seen` on the strength of an encompass edge. If the
    credited node is not an ancestor, we are keeping warm a skill that nothing gates on, and worse,
    the credit can carry a node the student never demonstrated. Inside a 37-node hand-authored
    slice the containment holds by construction and is cheap to keep.
    """
    problems: list[Problem] = []
    if _find_cycle(graph, _prereq_ids):
        return problems          # closure is meaningless until the DAG axiom holds

    for node_id in sorted(graph.nodes):
        ancestors = _closure(graph, node_id, _prereq_ids)
        for enc in _encompass_ids(graph, node_id):
            if enc in graph.nodes and enc not in ancestors:
                problems.append(Problem(
                    SEVERITY_ERROR, "ENCOMPASS_OUTSIDE_CLOSURE",
                    f"encompasses {enc!r}, which is not in its transitive prerequisite closure",
                    node_id))
    return problems


def _check_ranges(graph: Graph) -> list[Problem]:
    """Prereq weights in [0, 1]; encompass credits in (0, 1].

    Credit is a half-open interval on purpose: a zero-credit encompass edge is not a weaker edge,
    it is an edge that does nothing except refresh `last_seen`, which silently defeats decay.
    """
    problems: list[Problem] = []
    for node_id in sorted(graph.nodes):
        for prereq, weight in graph.prereqs(node_id):
            if not (0.0 <= float(weight) <= 1.0):
                problems.append(Problem(
                    SEVERITY_ERROR, "WEIGHT_RANGE",
                    f"prerequisite {prereq!r} has weight {weight!r}, outside [0, 1]", node_id))
        for enc, credit in graph.encompasses(node_id):
            if not (0.0 < float(credit) <= 1.0):
                problems.append(Problem(
                    SEVERITY_ERROR, "CREDIT_RANGE",
                    f"encompasses {enc!r} with credit {credit!r}, outside (0, 1]", node_id))
    return problems


def _check_reachability(graph: Graph) -> list[Problem]:
    """Everything is reachable forward from the roots, and everything feeds the target."""
    problems: list[Problem] = []

    roots = _roots(graph)
    if not roots:
        problems.append(Problem(
            SEVERITY_ERROR, "NO_ROOTS",
            "no node has an empty prereq list, so there is no entry point"))
        return problems

    dependants = _dependants(graph)
    reached = set(roots)
    stack = list(roots)
    while stack:
        for nxt in dependants[stack.pop()]:
            if nxt not in reached:
                reached.add(nxt)
                stack.append(nxt)

    for node_id in sorted(set(graph.nodes) - reached):
        problems.append(Problem(
            SEVERITY_ERROR, "UNREACHABLE_FROM_ROOTS",
            "cannot be reached by walking forward from any root", node_id))

    target = graph.target_node
    if not target:
        problems.append(Problem(
            SEVERITY_ERROR, "NO_TARGET", "the graph declares no target_node"))
        return problems
    if target not in graph.nodes:
        problems.append(Problem(
            SEVERITY_ERROR, "NO_TARGET",
            f"target_node {target!r} is not a node in the graph"))
        return problems

    # A node earns its place if the target depends on it, directly or transitively. Anything else
    # can be practised but never moves the student toward the goal.
    contributing = _closure(graph, target, _prereq_ids) | {target}
    for node_id in sorted(set(graph.nodes) - contributing):
        problems.append(Problem(
            SEVERITY_WARNING, "OFF_GOAL_PATH",
            f"the target {target!r} does not depend on it, directly or transitively", node_id))

    return problems


def _check_shortcut_edges(graph: Graph) -> list[Problem]:
    """Report, never fail on, a direct prereq edge that a longer prereq path also implies.

    `core_engine.md` section 2.1 makes transitive reduction a storage axiom, on the argument that
    a shortcut cannot change readiness. That argument needs ancestrally closed BINARY mastery
    (its section 3), which we do not have: our `status` checks direct prerequisites only, against
    a continuous decaying `p_eff`, and nothing ever rechecks a deeper ancestor. So here a shortcut
    is load bearing, and removing one lets a node unlock over a still-weak ancestor. All six were
    measured and kept; see docs/audits/graph-invariants.md. Reported so they stay visible.
    """
    problems: list[Problem] = []
    if _find_cycle(graph, _prereq_ids):
        return problems

    for node_id in sorted(graph.nodes):
        direct = [p for p in _prereq_ids(graph, node_id) if p in graph.nodes]
        for prereq in direct:
            via = [other for other in direct
                   if other != prereq and prereq in _closure(graph, other, _prereq_ids)]
            if via:
                problems.append(Problem(
                    SEVERITY_INFO, "SHORTCUT_EDGE",
                    f"direct prerequisite {prereq!r} is also implied through "
                    f"{', '.join(sorted(via))}", node_id))
    return problems


def _check_difficulty_monotonicity(graph: Graph) -> list[Problem]:
    """A node must be rated strictly harder than every one of its prerequisites.

    `difficulty_b` is not read by selection, which targets ITEM difficulty. This check is the
    reason the field is allowed to stay: an unvalidated number is how the three inversions got
    authored and survived. Either something checks it or it should not exist.
    """
    problems: list[Problem] = []
    for node_id in sorted(graph.nodes):
        node = graph.nodes[node_id]
        if "difficulty_b" not in node:
            problems.append(Problem(
                SEVERITY_ERROR, "MISSING_DIFFICULTY",
                "has no difficulty_b", node_id))
            continue
        try:
            own = float(node["difficulty_b"])
        except (TypeError, ValueError):
            problems.append(Problem(
                SEVERITY_ERROR, "MISSING_DIFFICULTY",
                f"difficulty_b {node['difficulty_b']!r} is not a number", node_id))
            continue

        for prereq in _prereq_ids(graph, node_id):
            if prereq not in graph.nodes:
                continue
            raw = graph.nodes[prereq].get("difficulty_b")
            if raw is None:
                continue
            if own <= float(raw):
                problems.append(Problem(
                    SEVERITY_ERROR, "DIFFICULTY_INVERSION",
                    f"difficulty_b {own:+.2f} is not above its prerequisite "
                    f"{prereq!r} at {float(raw):+.2f}", node_id))
    return problems


def _check_blame_hints(graph: Graph) -> list[Problem]:
    """Every node needs a blame_hint.

    engine.md section 5 and the section 16 measurement: the diagnosis call constrains
    `blamed_node` to an enum of real ids, and a node whose entry carries no fence attracts blame
    that belongs to a neighbour. A missing hint is not cosmetic, it corrupts the backward pass.
    """
    problems: list[Problem] = []
    for node_id in sorted(graph.nodes):
        hint = graph.nodes[node_id].get("blame_hint")
        if not isinstance(hint, str) or not hint.strip():
            problems.append(Problem(
                SEVERITY_ERROR, "MISSING_BLAME_HINT",
                "has no blame_hint, so it will attract blame belonging to its neighbours",
                node_id))
    return problems


def _check_item_coverage(graph: Graph, counts: dict[str, int]) -> list[Problem]:
    """Every node needs at least MIN_GRADEABLE_ITEMS gradeable items."""
    problems: list[Problem] = []
    for node_id in sorted(graph.nodes):
        have = counts.get(node_id, 0)
        if have < MIN_GRADEABLE_ITEMS:
            problems.append(Problem(
                SEVERITY_ERROR, "TOO_FEW_ITEMS",
                f"has {have} gradeable item(s), needs at least {MIN_GRADEABLE_ITEMS}; "
                "it can never leave the frontier and blocks everything above it",
                node_id))

    for node_id in sorted(set(counts) - set(graph.nodes)):
        problems.append(Problem(
            SEVERITY_WARNING, "ITEM_ORPHANED",
            f"{counts[node_id]} gradeable item(s) are tagged to {node_id!r}, "
            "which is not a node in the graph"))
    return problems


# --------------------------------------------------------------------------- entry points

def validate(
    graph: Graph,
    items_by_node: Optional[dict[str, int]] = None,
) -> list[Problem]:
    """Run every check. Returns findings sorted by severity, then code, then node.

    `items_by_node` maps node id to a count of GRADEABLE items. Pass it explicitly in tests;
    when omitted the shipped bank is read from disk, so the default run checks what actually
    ships. Pass an empty dict to assert on the failure, not to skip the check.
    """
    if items_by_node is None:
        items_by_node = load_gradeable_counts()

    problems: list[Problem] = []
    problems += _check_structure(graph)
    problems += _check_encompass_within_prereq_closure(graph)
    problems += _check_ranges(graph)
    problems += _check_reachability(graph)
    problems += _check_difficulty_monotonicity(graph)
    problems += _check_blame_hints(graph)
    problems += _check_item_coverage(graph, items_by_node)
    problems += _check_shortcut_edges(graph)

    return sorted(problems, key=lambda p: (
        _SEVERITY_ORDER.get(p.severity, 9), p.code, p.node_id or "", p.message))


def validate_file(
    graph_path: str | Path = DEFAULT_GRAPH_PATH,
    items_path: str | Path = DEFAULT_ITEMS_PATH,
) -> list[Problem]:
    """`validate`, plus the duplicate-id check, which needs the raw file.

    `load_graph` keys nodes by id, so a duplicate has already been collapsed (in fact it raises)
    by the time a `Graph` exists. Reading the raw list here turns that into a reported Problem
    like every other finding instead of an exception the CLI has to special-case.
    """
    resolved = Path(graph_path)
    if not resolved.is_absolute() and not resolved.exists():
        resolved = _REPO_ROOT / graph_path

    raw = json.loads(resolved.read_text(encoding="utf-8"))
    raw_nodes = raw.get("nodes", [])
    problems: list[Problem] = []
    if isinstance(raw_nodes, list):
        seen: set[str] = set()
        for node in raw_nodes:
            node_id = node.get("id")
            if node_id in seen:
                problems.append(Problem(
                    SEVERITY_ERROR, "DUPLICATE_ID",
                    f"node id {node_id!r} appears more than once in {resolved.name}", node_id))
            seen.add(node_id)

    if any(p.code == "DUPLICATE_ID" for p in problems):
        # `load_graph` refuses a duplicate outright, so report and stop rather than crash.
        return problems

    graph = load_graph(resolved)
    return problems + validate(graph, load_gradeable_counts(items_path))


def has_errors(problems: Iterable[Problem]) -> bool:
    return any(p.severity == SEVERITY_ERROR for p in problems)


def format_report(problems: list[Problem], graph: Optional[Graph] = None) -> str:
    """The printed report. Errors first, then warnings, then info, then the verdict."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("knowledge graph validation")
    if graph is not None:
        lines.append(f"{len(graph.nodes)} nodes, target {graph.target_node!r}")
    lines.append("=" * 78)

    for severity, label in ((SEVERITY_ERROR, "errors"),
                            (SEVERITY_WARNING, "warnings"),
                            (SEVERITY_INFO, "reported, not failing")):
        rows = [p for p in problems if p.severity == severity]
        lines.append("")
        lines.append(f"{label} ({len(rows)})")
        if not rows:
            lines.append("  none")
        for problem in rows:
            where = f" [{problem.node_id}]" if problem.node_id else ""
            lines.append(f"  {problem.code}{where}")
            lines.append(f"      {problem.message}")

    lines.append("")
    lines.append("-" * 78)
    errors = sum(1 for p in problems if p.severity == SEVERITY_ERROR)
    if errors:
        lines.append(f"FAIL: {errors} error(s)")
    else:
        lines.append("PASS: no errors")
    lines.append("-" * 78)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m engine.graph_validate",
        description="Validate the knowledge graph against the invariants the engine relies on.")
    parser.add_argument("--graph", default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--items", default=DEFAULT_ITEMS_PATH)
    args = parser.parse_args(argv)

    problems = validate_file(args.graph, args.items)

    graph = None
    if not any(p.code == "DUPLICATE_ID" for p in problems):
        graph = load_graph(args.graph)

    print(format_report(problems, graph))
    return 1 if has_errors(problems) else 0


if __name__ == "__main__":
    sys.exit(main())
