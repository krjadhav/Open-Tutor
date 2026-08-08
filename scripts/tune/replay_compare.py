#!/usr/bin/env python3
"""Replay one attempt log under two engine behaviours and print them side by side.

This is what the event log is FOR.

`engine/replay.py` argues the case at length: attempts are the source of truth and `NodeState` is
derived, so that when we change LAMBDA_BLAME, the implicit-credit rule or a mastery threshold, we
can replay real history under the old and new constants and compare. That argument is only worth
anything if there is a tool that actually does it. This is that tool, and it is meant to be the
one we reach for every time a tuning question comes up, not a script written for one change and
thrown away.

The comparison is deliberately narrow and demo-shaped, because those are the facts we have agreed
to protect:

  - how many nodes sit in each status, and which nodes moved
  - `der.quotient-rule`, which the demo's climax problem hangs off, and the margin by which its
    prereqs clear PREREQ_READY
  - the open blockers and how many times each fired
  - accuracy
  - the composed daily set, item by item
  - whether either blame cap engaged, and how far off engaging it was

Usage
-----
    python3 scripts/tune/replay_compare.py
    python3 scripts/tune/replay_compare.py --nodes
    python3 scripts/tune/replay_compare.py --baseline transitive_credit=off \\
                                           --candidate transitive_credit=on
    python3 scripts/tune/replay_compare.py --history data/demo/history.json --json

`--baseline` and `--candidate` take `flag=on|off` pairs resolved against `MasteryConfig`'s fields,
so a new behaviour flag becomes comparable here the moment it is added to the config, with no edit
to this file. `off` (all flags false, the shipped-before behaviour) and `on` (all flags true) are
accepted as whole-config shorthands.

Both variants run in ONE process. The config is injected, never patched onto a module constant,
so neither run can contaminate the other and the two states are directly comparable.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.mastery import (                                                  # noqa: E402
    BlameDelta,
    apply_attempt,
    apply_direct,
    blame_delta,
    is_due,
    p_eff,
    status,
    using,
)
from engine.replay import load_graph, replay                                  # noqa: E402
from engine.selection import compose_daily_set, load_items                    # noqa: E402
from engine.types import (                                                    # noqa: E402
    LAMBDA_BLAME,
    PREREQ_READY,
    Attempt,
    Graph,
    MasteryConfig,
    NodeState,
    add_days,
)

DEFAULT_HISTORY = ROOT / "data" / "demo" / "history.json"
DEFAULT_GRAPH = ROOT / "data" / "graph" / "nodes.json"
DEFAULT_ITEMS = ROOT / "data" / "items" / "items.json"

# The node the demo's climax problem is tagged to. Kept as a constant rather than a flag because
# every report below wants it, and a tuning run that quietly stopped watching it would be useless.
FOCUS_NODE = "der.quotient-rule"

STATUSES = ("mastered", "learning", "frontier", "locked")

LABEL = 32          # width of the row-label column
COL = 46            # width of each comparison column


# --------------------------------------------------------------------------- history loading

def load_attempts(path: Path) -> tuple[list[Attempt], list[dict]]:
    """Read an attempt log. Returns `(attempts, raw_rows)`.

    Raw rows are kept alongside because some of the facts we compare are properties of the LOG
    (accuracy, how many times a misconception fired) rather than of the replayed state, and
    reading them off the log is the honest source: they must not change when the engine does, and
    printing them from the log is what makes that visible.

    Only the fields `Attempt` declares are passed through, so a log carrying extra UI columns
    (`failed_step`, `session`, ...) loads unchanged.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw["attempts"] if isinstance(raw, dict) else raw
    known = {f.name for f in dataclasses.fields(Attempt)}
    attempts = []
    for row in rows:
        kwargs = {k: v for k, v in row.items() if k in known}
        kwargs["ts"] = datetime.fromisoformat(row["ts"])
        kwargs["used_nodes"] = tuple(row.get("used_nodes") or ())
        attempts.append(Attempt(**kwargs))
    return attempts, rows


def anchor_now(path: Path, rows: Sequence[dict]) -> datetime:
    """The instant to evaluate state at: the log's declared anchor, else its last timestamp.

    Never `now()`. The engine contract forbids reading the clock, and a comparison whose result
    depended on when it was run would not be a comparison.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("anchor_today"):
        return datetime.fromisoformat(raw["anchor_today"])
    return datetime.fromisoformat(rows[-1]["ts"])


def misconception_counts(rows: Iterable[dict]) -> dict[tuple[str, str], int]:
    """(blamed node, tag) -> how many times the log blamed it. A property of the log."""
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        tag, blamed = row.get("misconception_tag"), row.get("blamed_node")
        if tag and blamed:
            counts[(blamed, tag)] = counts.get((blamed, tag), 0) + 1
    return counts


# --------------------------------------------------------------------------- config parsing

WHOLE_CONFIGS = {
    "off": {name: False for name in (f.name for f in dataclasses.fields(MasteryConfig))},
    "on": {name: True for name in (f.name for f in dataclasses.fields(MasteryConfig))},
    # What the engine currently ships, whatever that happens to be. Read off the dataclass rather
    # than spelled out, so it tracks the defaults instead of drifting from them.
    "shipped": {f.name: f.default for f in dataclasses.fields(MasteryConfig)},
}
TRUTHY = {"on": True, "true": True, "yes": True, "1": True}
FALSY = {"off": False, "false": False, "no": False, "0": False}


def parse_config(spec: str) -> MasteryConfig:
    """`"off"`, `"on"`, or a comma-separated `flag=on|off` list, resolved against MasteryConfig.

    Field names are read off the dataclass rather than listed here, so adding a behaviour flag to
    the config makes it comparable from the command line with no change to this script. That is
    the difference between a tuning tool and a one-off.
    """
    fields = {f.name for f in dataclasses.fields(MasteryConfig)}
    values: dict[str, bool] = {}
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if part in WHOLE_CONFIGS:
            values.update(WHOLE_CONFIGS[part])
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError(
                f"{part!r}: expected 'flag=on|off', or one of {sorted(WHOLE_CONFIGS)}")
        name, _, raw = part.partition("=")
        name, raw = name.strip(), raw.strip().lower()
        if name not in fields:
            raise argparse.ArgumentTypeError(
                f"unknown behaviour flag {name!r}; known flags: {sorted(fields)}")
        if raw not in TRUTHY and raw not in FALSY:
            raise argparse.ArgumentTypeError(f"{part!r}: value must be on/off")
        values[name] = TRUTHY.get(raw, False)
    return MasteryConfig(**values)


def describe(config: MasteryConfig) -> str:
    on = [f.name for f in dataclasses.fields(MasteryConfig) if getattr(config, f.name)]
    return ", ".join(on) if on else "none (legacy)"


# --------------------------------------------------------------------------- one run

@dataclass(frozen=True)
class Snapshot:
    """Everything one behaviour produced from one log. Comparable, printable, JSON-able."""
    label: str
    config: MasteryConfig
    now: datetime
    states: dict[str, NodeState]
    statuses: dict[str, str]
    by_status: dict[str, list[str]]
    blockers: dict[str, tuple[str, ...]]
    due: list[str]
    daily_set: list[tuple[str, str, str, float]]        # (slot, item_id, node_id, p_success)
    focus_margin: list[tuple[str, float]]               # (prereq, p_eff - PREREQ_READY)
    blames: list[tuple[str, str, BlameDelta]] = field(default_factory=list)

    @property
    def focus_status(self) -> str:
        return self.statuses.get(FOCUS_NODE, "-")


def run(
    label: str,
    config: MasteryConfig,
    attempts: Sequence[Attempt],
    graph: Graph,
    items,
    now: datetime,
) -> Snapshot:
    """Replay `attempts` under `config` and collect every fact we compare.

    The replay goes through `engine.replay.replay` verbatim, with the behaviour injected via
    `engine.mastery.using`. Reimplementing the fold here would be the obvious shortcut and the
    obvious bug: a tuning tool that measures a slightly different engine than the one that ships
    tells you nothing.
    """
    with using(config):
        states = replay(attempts, graph)
        statuses = {node_id: status(node_id, graph, states, now) for node_id in graph.nodes}
        entries = compose_daily_set(graph, states, items, now)

    by_status: dict[str, list[str]] = {name: [] for name in STATUSES}
    for node_id, name in statuses.items():
        by_status.setdefault(name, []).append(node_id)
    for ids in by_status.values():
        ids.sort()

    return Snapshot(
        label=label,
        config=config,
        now=now,
        states=states,
        statuses=statuses,
        by_status=by_status,
        blockers={n: states[n].misconceptions for n in sorted(graph.nodes)
                  if states[n].misconceptions},
        due=sorted(n for n in graph.nodes if is_due(states[n], now)),
        daily_set=[(e.slot, e.item.item_id, e.item.node_id, e.predicted_success)
                   for e in entries],
        focus_margin=[(prereq, p_eff(states[prereq], now) - PREREQ_READY)
                      for prereq, _w in graph.prereqs(FOCUS_NODE)],
        blames=blame_records(attempts, graph, config),
    )


def blame_records(
    attempts: Sequence[Attempt],
    graph: Graph,
    config: MasteryConfig,
) -> list[tuple[str, str, BlameDelta]]:
    """Every blame in the log with the delta it produced and what limited it.

    A cap nobody can watch engage is how BLAME_MAX_B_DELTA stayed inert unnoticed, so this
    re-walks the log and asks `blame_delta` the same question `apply_blame` asks. The state handed
    to it mirrors `apply_attempt`'s sequencing exactly: direct evidence lands on the attempted
    node BEFORE the blame is sized, which matters whenever the blamed node is the attempted one.
    """
    records: list[tuple[str, str, BlameDelta]] = []
    states: dict[str, NodeState] = {node_id: NodeState(node_id) for node_id in graph.nodes}
    for attempt in sorted(attempts, key=lambda a: (a.ts, a.attempt_id)):
        if attempt.blamed_node and not attempt.correct:
            mid = apply_direct(states, attempt.node_id, False, attempt.quality, attempt.ts)
            records.append((
                attempt.attempt_id,
                attempt.blamed_node,
                blame_delta(mid, graph, attempt.blamed_node, attempt.blame_confidence,
                            attempt.ts, config),
            ))
        states = apply_attempt(states, graph, attempt, attempt.ts, config=config)
    return records


# --------------------------------------------------------------------------- reporting

def _cell(text: str) -> str:
    """Fixed-width column. Long values are elided rather than allowed to shift the columns:
    two rows that no longer line up cannot be compared by eye, which is the whole point here."""
    if len(text) > COL - 2:
        text = text[:COL - 3] + "~"
    return f"{text:<{COL}}"


def _line(label: str, old: str, new: str, out) -> None:
    marker = "  " if old == new else " *"
    print(f"{label:<{LABEL}}{_cell(old)}{_cell(new)}{marker}", file=out)


def _header(baseline: Snapshot, candidate: Snapshot, out) -> None:
    print(f"{'':<{LABEL}}{_cell(baseline.label)}{_cell(candidate.label)}", file=out)
    print(f"{'':<{LABEL}}{_cell(describe(baseline.config))}{_cell(describe(candidate.config))}",
          file=out)
    print("-" * (LABEL + 2 * COL + 2), file=out)


def horizon_statuses(
    snapshot: Snapshot,
    graph: Graph,
    hours: Sequence[float],
) -> dict[float, dict[str, str]]:
    """Statuses re-derived at `now + h` hours, for each `h`. No re-replay: status is a pure
    function of state and time, so this is decay carried a little further forward."""
    return {
        h: {node_id: status(node_id, graph, snapshot.states, add_days(snapshot.now, h / 24.0))
            for node_id in graph.nodes}
        for h in hours
    }


def _horizon_report(
    baseline: Snapshot,
    candidate: Snapshot,
    graph: Graph,
    hours: Sequence[float],
    out,
) -> list[str]:
    """How the two behaviours diverge as the state ages, and why this section exists at all.

    A comparison taken only at the log's anchor instant is a comparison at ONE point on a decay
    curve. That is how a change can leave every headline number identical and still be broken: a
    node sitting 0.003 above PREREQ_READY reads as "unchanged" at the anchor and as LOCKED an hour
    later. An hour is not a hypothetical. `app/state.py` shifts the whole seeded log onto the real
    date by WHOLE DAYS and then evaluates at the real time of day, so the app routinely sits an
    hour or two past where `seed_demo.py --verify` looks.

    Hours, not days, because that is the resolution the margins actually live at: at a stability
    of about 3.4 days, p_eff falls roughly 0.009 per hour.
    """
    print("\nstatus at later instants (the app evaluates past the log's anchor)", file=out)
    old_by_hour = horizon_statuses(baseline, graph, hours)
    new_by_hour = horizon_statuses(candidate, graph, hours)
    problems: list[str] = []
    for h in hours:
        old, new = old_by_hour[h], new_by_hour[h]
        moved = sorted(n for n in old if old[n] != new[n])
        _line(f"  +{h:g}h  {FOCUS_NODE}", old.get(FOCUS_NODE, "-"), new.get(FOCUS_NODE, "-"), out)
        if moved:
            summary = ", ".join(f"{n} {old[n]}->{new[n]}" for n in moved[:4])
            print(f"{'':<{LABEL}}{len(moved)} node(s) differ: {summary}"
                  f"{' ...' if len(moved) > 4 else ''}", file=out)
            problems.append(f"at +{h:g}h, {len(moved)} node(s) differ: {summary}")
    return problems


def report(
    baseline: Snapshot,
    candidate: Snapshot,
    rows: Sequence[dict],
    graph: Graph,
    horizons: Sequence[float] = (0, 1, 6, 24),      # hours past the log's anchor
    show_nodes: bool = False,
    out=sys.stdout,
) -> bool:
    """Print the side-by-side comparison. Returns True when nothing demo-critical moved.

    Rows that differ are marked with `*` in the right margin, so a clean run is a page with no
    stars in it and a scan for stars is a complete review.
    """
    correct = sum(1 for r in rows if r["correct"])
    accuracy = f"{correct}/{len(rows)} = {100.0 * correct / len(rows):.1f}%"
    counts = misconception_counts(rows)

    print(f"\nlog          {len(rows)} attempts, {rows[0]['ts'][:10]} to {rows[-1]['ts'][:10]}",
          file=out)
    print(f"evaluated at {baseline.now.isoformat()}\n", file=out)

    _header(baseline, candidate, out)

    print("\nnodes by status", file=out)
    for name in STATUSES:
        _line(f"  {name}",
              str(len(baseline.by_status.get(name, []))),
              str(len(candidate.by_status.get(name, []))), out)

    print("\nstatus changes", file=out)
    moved = [n for n in sorted(baseline.statuses)
             if baseline.statuses[n] != candidate.statuses.get(n)]
    if not moved:
        print("  none", file=out)
    for node_id in moved:
        _line(f"  {node_id}", baseline.statuses[node_id], candidate.statuses[node_id], out)

    print(f"\n{FOCUS_NODE}", file=out)
    _line("  status", baseline.focus_status, candidate.focus_status, out)
    _line("  p", f"{baseline.states[FOCUS_NODE].p:.3f}",
          f"{candidate.states[FOCUS_NODE].p:.3f}", out)
    print("  prereq margin over PREREQ_READY (negative would mean locked)", file=out)
    old_margin = dict(baseline.focus_margin)
    for prereq, margin in candidate.focus_margin:
        _line(f"    {prereq}", f"{old_margin.get(prereq, float('nan')):+.3f}",
              f"{margin:+.3f}", out)

    print("\nopen blockers (state) with occurrences (log)", file=out)
    for node_id in sorted(set(baseline.blockers) | set(candidate.blockers)):
        old_tags = baseline.blockers.get(node_id, ())
        new_tags = candidate.blockers.get(node_id, ())
        _line(f"  {node_id}",
              _blocker_cell(node_id, old_tags, counts),
              _blocker_cell(node_id, new_tags, counts), out)
    if not baseline.blockers and not candidate.blockers:
        print("  none", file=out)

    print("\naccuracy (a property of the log, not of the engine)", file=out)
    _line("  accuracy", accuracy, accuracy, out)
    _line("  due reviews", str(len(baseline.due)), str(len(candidate.due)), out)

    print("\ndaily set", file=out)
    for i in range(max(len(baseline.daily_set), len(candidate.daily_set))):
        _line(f"  {i + 1}.", _set_cell(baseline.daily_set, i), _set_cell(candidate.daily_set, i),
              out)

    _blame_report(baseline, candidate, out)
    horizon_problems = _horizon_report(baseline, candidate, graph, horizons, out)

    if show_nodes:
        _node_report(baseline, candidate, out)

    return _verdict(baseline, candidate, horizon_problems, out)


def _blocker_cell(node_id, tags, counts) -> str:
    if not tags:
        return "-"
    return ", ".join(f"{t} x{counts.get((node_id, t), 0)}" for t in tags)


def _set_cell(entries, index) -> str:
    if index >= len(entries):
        return "-"
    slot, item_id, _node, p_success = entries[index]
    return f"[{slot}] {item_id} {p_success:.2f}"


def _blame_report(baseline: Snapshot, candidate: Snapshot, out) -> None:
    """How close each blame came to a cap, in both behaviours.

    `headroom` is the factor by which LAMBDA_BLAME would have to grow before the tightest cap
    engaged on this log. Above 1.0 means no cap did anything, which is the finding that started
    this whole exercise.
    """
    print("\nblame caps", file=out)
    _line("  blames in log", str(len(baseline.blames)), str(len(candidate.blames)), out)
    _line("  max b delta applied",
          _fmt_max(d.applied for _a, _n, d in baseline.blames),
          _fmt_max(d.applied for _a, _n, d in candidate.blames), out)
    _line("  times a cap engaged",
          str(sum(1 for _a, _n, d in baseline.blames if d.bound_by != "none")),
          str(sum(1 for _a, _n, d in candidate.blames if d.bound_by != "none")), out)
    _line("  tightest cap seen",
          _fmt_min(min(d.ceiling, d.boundary) for _a, _n, d in baseline.blames),
          _fmt_min(min(d.ceiling, d.boundary) for _a, _n, d in candidate.blames), out)
    _line(f"  engages at LAMBDA_BLAME ({LAMBDA_BLAME})",
          _fmt_lambda(baseline), _fmt_lambda(candidate), out)


def _fmt_max(values) -> str:
    values = list(values)
    return f"{max(values):.3f}" if values else "-"


def _fmt_min(values) -> str:
    values = list(values)
    if not values:
        return "-"
    smallest = min(values)
    return "inf" if math.isinf(smallest) else f"{smallest:.3f}"


def _fmt_lambda(snapshot: Snapshot) -> str:
    """Smallest LAMBDA_BLAME at which some blame in this log would hit a cap."""
    needed = [min(d.ceiling, d.boundary) / (d.requested / LAMBDA_BLAME)
              for _a, _n, d in snapshot.blames if d.requested > 0]
    if not needed:
        return "-"
    smallest = min(needed)
    return "never" if math.isinf(smallest) else f"> {smallest:.2f}"


def _node_report(baseline: Snapshot, candidate: Snapshot, out) -> None:
    print("\nper-node state (only nodes that moved)", file=out)
    print(f"{'node':<26} {'p':>16}  {'p_eff':>16}  {'S':>14}  {'succ':>8}", file=out)
    for node_id in sorted(baseline.states):
        old, new = baseline.states[node_id], candidate.states[node_id]
        if old == new:
            continue
        print(f"{node_id:<26} "
              f"{old.p:7.3f}->{new.p:7.3f}  "
              f"{p_eff(old, baseline.now):7.3f}->{p_eff(new, candidate.now):7.3f}  "
              f"{old.stability:6.2f}->{new.stability:6.2f}  "
              f"{old.successes:3d}->{new.successes:3d}", file=out)


def _verdict(
    baseline: Snapshot,
    candidate: Snapshot,
    horizon_problems: Sequence[str],
    out,
) -> bool:
    """Name the demo-critical facts that moved, or say plainly that none did."""
    changes = list(horizon_problems)
    for name in STATUSES:
        old_n, new_n = len(baseline.by_status.get(name, [])), len(candidate.by_status.get(name, []))
        if old_n != new_n:
            changes.append(f"{name} count {old_n} -> {new_n}")
    if baseline.focus_status != candidate.focus_status:
        changes.append(f"{FOCUS_NODE} {baseline.focus_status} -> {candidate.focus_status}")
    if set(baseline.blockers) != set(candidate.blockers):
        changes.append(f"open blockers {sorted(baseline.blockers)} -> {sorted(candidate.blockers)}")
    if [e[1] for e in baseline.daily_set] != [e[1] for e in candidate.daily_set]:
        changes.append("the daily set changed")

    # A margin that shrank toward zero counts as a regression even when no status moved, because
    # the status it decides is an hour of decay away. Failed, not merely noted: treating it as
    # cosmetic is exactly how a change ships that breaks nothing at the anchor and locks the
    # demo's climax node in the app.
    old_margin = dict(baseline.focus_margin)
    for prereq, margin in candidate.focus_margin:
        before = old_margin.get(prereq)
        if before is not None and margin < before - 1e-9:
            changes.append(
                f"{prereq} prereq margin {before:+.3f} -> {margin:+.3f}, "
                f"{FOCUS_NODE} locks when it goes negative")

    print("\nverdict", file=out)
    if not changes:
        print("  no demo-critical fact moved.", file=out)
    for text in changes:
        print(f"  CHANGED: {text}", file=out)
    return not changes


def as_json(
    baseline: Snapshot,
    candidate: Snapshot,
    rows: Sequence[dict],
    graph: Graph,
    horizons: Sequence[float],
) -> dict:
    """Machine-readable form, so a comparison can be diffed between branches or checked in CI."""
    def one(snapshot: Snapshot) -> dict:
        return {
            "horizon_statuses": {str(d): s
                                 for d, s in horizon_statuses(snapshot, graph, horizons).items()},
            "label": snapshot.label,
            "config": dataclasses.asdict(snapshot.config),
            "status_counts": {n: len(snapshot.by_status.get(n, [])) for n in STATUSES},
            "statuses": snapshot.statuses,
            "focus_node": FOCUS_NODE,
            "focus_status": snapshot.focus_status,
            "focus_prereq_margin": dict(snapshot.focus_margin),
            "blockers": {k: list(v) for k, v in snapshot.blockers.items()},
            "due": snapshot.due,
            "daily_set": [{"slot": s, "item_id": i, "node_id": n, "p_success": p}
                          for s, i, n, p in snapshot.daily_set],
            "blames": [{"attempt_id": a, "node": n, "requested": d.requested,
                        "applied": d.applied, "bound_by": d.bound_by,
                        "pre_status": d.pre_status} for a, n, d in snapshot.blames],
        }

    correct = sum(1 for r in rows if r["correct"])
    return {
        "attempts": len(rows),
        "accuracy": correct / len(rows),
        "baseline": one(baseline),
        "candidate": one(candidate),
    }


# --------------------------------------------------------------------------- cli

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH))
    parser.add_argument("--items", default=str(DEFAULT_ITEMS))
    parser.add_argument("--baseline", default="off", type=parse_config,
                        help="behaviour to compare AGAINST (default: off, the legacy engine)")
    parser.add_argument("--candidate", default="on", type=parse_config,
                        help="behaviour to compare (default: on, every flag enabled)")
    parser.add_argument("--nodes", action="store_true",
                        help="also print every node whose state differs")
    parser.add_argument("--horizons", default="0,1,6,24",
                        help="HOURS past the log's anchor to re-derive status at "
                             "(default 0,1,6,24; the app routinely sits an hour or two past it)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--fail-on-change", action="store_true",
                        help="exit non-zero if any demo-critical fact moved")
    args = parser.parse_args(argv)

    history_path = Path(args.history)
    attempts, rows = load_attempts(history_path)
    if not rows:
        parser.error(f"{history_path} contains no attempts")
    graph = load_graph(args.graph)
    items = load_items(args.items)
    now = anchor_now(history_path, rows)

    try:
        horizons = tuple(float(d) for d in args.horizons.split(",") if d.strip())
    except ValueError:
        parser.error(f"--horizons must be a comma-separated list of days, got {args.horizons!r}")

    baseline = run("OLD", args.baseline, attempts, graph, items, now)
    candidate = run("NEW", args.candidate, attempts, graph, items, now)

    if args.json:
        print(json.dumps(as_json(baseline, candidate, rows, graph, horizons),
                         indent=2, default=str))
        unchanged = all(
            horizon_statuses(baseline, graph, horizons)[d]
            == horizon_statuses(candidate, graph, horizons)[d] for d in horizons)
    else:
        unchanged = report(baseline, candidate, rows, graph, horizons, show_nodes=args.nodes)

    return 1 if args.fail_on_change and not unchanged else 0


if __name__ == "__main__":
    raise SystemExit(main())
