#!/usr/bin/env python3
"""Isomorphic twins: the same drill, the same shape, different numbers.

Hint rung 4 reveals the full method. On its own that is a giveaway: the student reads the answer,
the attempt is worth almost nothing, and we have bought a moment of relief with a hole in the
evidence. A twin closes the hole. Immediately after the reveal the student gets the same problem
with different numbers, and *that* attempt is real evidence that the reveal was understood.

Three rules make this safe, and they are the whole design:

  1. **Operate on the `check` spec, never on the rendered text.** The spec is a task name plus
     sympy-syntax parameters. Rewriting the rendered LaTeX would be string surgery on a question
     whose answer we could then only guess at.
  2. **Perturb integer literals only, and never operators or identifiers.** Every non-digit
     character of every parameter survives byte for byte, which is what preserves the property the
     drill exists to test. A twin of an `alg.sign-distribution` drill still has a minus in front of
     a bracket because the minus and the bracket were never touched. `w1` and `w2` keep their
     digits because a digit welded to a letter is part of a name, not a literal.
  3. **Rebuild through `pipeline.drill_tasks.build`.** The answer is recomputed by the CAS from
     the new spec. It is never copied, never adjusted, never inferred. If the rebuilt drill fails
     validation, `twin_of` returns None; that is the guard doing its job and the UI simply does not
     offer a twin.

Some literals are not free to move, because moving them would change what is being tested:

  - **exponents** are frozen whenever the task is `gd_step` or the spec carries a `context`. Those
    are the squared-error and gradient-descent drills, where `**2` is the story, not a number.
  - **angles** written against `pi` are drawn from denominators {2, 3, 4, 6} and numerators
    {1..5}, so a unit-circle drill twins into another standard angle rather than into `cos(pi/5)`.
  - **derivative order** in `nth_derivative` stays in {2, 3}.

Items whose `source` is `openstax` have no `check` spec: they are scraped stems with scraped answer
keys and there is nothing to recompute from. `twin_of` returns None for them, and rung 4 does not
offer a twin on nodes whose bank is OpenStax-only (see `pipeline/build_hints.py`).

    twin_of(item: dict, seed: int) -> dict | None

Deterministic: the same (item, seed) always yields the same twin, including the same failure.

    python3 pipeline/twin.py            # twin rate across the generated bank
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import sympy as sp

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import drill_tasks as dt              # noqa: E402

ITEMS_PATH = ROOT / "data" / "items" / "items.json"

#: Parameter fields that hold mathematics and may be perturbed. `var` is an identifier and
#: `context` is prose, and both are handled separately.
NUMERIC_FIELDS = ("expr", "expr2", "at", "lr")

#: How many distinct perturbations to try before giving up on this item. Every candidate still
#: goes through the full CAS rebuild and every guard; this only means we do not abandon an item
#: because the first roll of the dice happened to produce, say, a quadratic that does not factor.
MAX_TRIES = 24

#: A digit run that is a literal rather than part of a name or a decimal. The lookarounds keep us
#: out of `w1` and `x2` (a digit welded to a letter is part of a name) and out of both halves of
#: `0.05` (a decimal is one number, and moving half of it is meaningless). A digit that merely
#: ends a sentence, as in "the target is 3.", is still a literal and is still moved, which matters
#: because that sentence has to stay true after the expression it describes has changed.
INT_RE = re.compile(r"(?<![A-Za-z_0-9.])\d+(?![A-Za-z_0-9])(?!\.\d)")

_EXP_RE = re.compile(r"\*\*\s*\(?\s*[-+]?\s*(\d+)")
_PI_DEN_RE = re.compile(r"pi\s*/\s*(\d+)")
_PI_NUM_RE = re.compile(r"(\d+)\s*\*?\s*pi\b")

#: A learning rate written as a unit fraction. Its denominator is the only part that may move, and
#: only onto another rate a person would actually choose: 1/9 is not a learning rate.
_UNIT_FRACTION_RE = re.compile(r"\s*1\s*/\s*(\d+)\s*\Z")
_LR_DENOMINATORS = (2, 4, 5, 10, 20)


# --------------------------------------------------------------------------- literal scanning

def _roles(src: str) -> dict[tuple[int, int], str]:
    """Span -> role for the literals in one parameter string."""
    marked: dict[tuple[int, int], str] = {}
    for rx, role in ((_EXP_RE, "exp"), (_PI_DEN_RE, "pi_den"), (_PI_NUM_RE, "pi_num")):
        for m in rx.finditer(src):
            marked[m.span(1)] = role
    return marked


def _literals(src: str) -> list[tuple[int, int, str, int]]:
    """(start, end, role, value) for every perturbable literal in a parameter string."""
    marked = _roles(src)
    out = []
    for m in INT_RE.finditer(src):
        span = m.span()
        out.append((span[0], span[1], marked.get(span, "plain"), int(m.group())))
    return out


def _field_literals(task: str, field: str, src: str) -> list[tuple[int, int, str, int]]:
    """`_literals`, with the roles that depend on which parameter we are looking at.

    Two literals mean something specific because of the field they sit in rather than because of
    the operators around them: the order of an `nth_derivative`, and the denominator of a learning
    rate written as a unit fraction.
    """
    src = str(src)
    lits = _literals(src)
    if task == "nth_derivative" and field == "at":
        return [(s, e, "order", v) for s, e, _, v in lits]
    if field == "lr":
        m = _UNIT_FRACTION_RE.fullmatch(src)
        if m:
            return [(s, e, "lr_den" if (s, e) == m.span(1) else "frozen", v)
                    for s, e, _, v in lits]
    return lits


def skeleton(src: str) -> str:
    """The parameter string with every literal blanked out.

    Two specs with the same skeleton differ only in their numbers, which is the formal statement
    of "isomorphic". `twin_of` asserts this rather than trusting itself.
    """
    return INT_RE.sub("#", str(src))


# --------------------------------------------------------------------------- perturbation

def _candidates(role: str, old: int) -> list[int]:
    """The values this literal is allowed to take, given what it is doing in the expression.

    A plain literal never becomes 1. `(w2*(w1*1))**2` is a coefficient that has stopped being a
    coefficient, and it renders as `w2 (w1 1)`, which no student should be shown.
    """
    if role == "exp":
        return [v for v in range(max(2, old - 2), old + 3) if v != old and 2 <= v <= 9]
    if role == "pi_den":
        return [v for v in (2, 3, 4, 6) if v != old]
    if role == "pi_num":
        return [v for v in (1, 2, 3, 4, 5) if v != old]
    if role == "order":
        return [v for v in (2, 3) if v != old]
    if role == "lr_den":
        return [v for v in _LR_DENOMINATORS if v != old]
    if role == "frozen":
        return []
    if old == 0:
        return [1, 2, 3]
    return [v for v in range(max(2, old - 3), min(old + 4, 13)) if v != old]


def _frozen_roles(task: str, params: dict) -> set[str]:
    """Roles that must not move for this task.

    An exponent is frozen wherever it carries meaning that the prose also carries: `gd_step` needs
    a loss that stays the loss it is described as, and any spec with a `context` has a sentence in
    front of it ("Two-layer network", "squared error") that a changed exponent would contradict.
    """
    if task == "gd_step" or str(params.get("context") or "").strip():
        return {"exp"}
    return set()


def _plan(task: str, params: dict, rng: random.Random) -> dict[tuple[str, int], int]:
    """Choose new values for a subset of the literals, keyed by (role, old value).

    The map is keyed by value, not by position, so every occurrence of the same literal in the
    same role moves together. That is what lets a `context` sentence ("the model predicts 2w + 1
    and the target is 3") stay true after the expression it describes has changed.
    """
    frozen = _frozen_roles(task, params)
    options: dict[tuple[str, int], list[int]] = {}
    for field in NUMERIC_FIELDS:
        src = params.get(field)
        if not src:
            continue
        for _, _, role, value in _field_literals(task, field, str(src)):
            if role in frozen:
                continue
            cands = _candidates(role, value)
            if cands:
                options[(role, value)] = cands
    if not options:
        return {}

    keys = sorted(options)
    rng.shuffle(keys)
    keys = keys[:rng.randint(1, len(keys))]
    return {k: rng.choice(options[k]) for k in sorted(keys)}


def _rewrite(src: str, task: str, field: str, plan: dict[tuple[str, int], int]) -> str:
    """Apply the plan to one parameter string, right to left so earlier spans stay valid."""
    out = str(src)
    for start, end, role, value in reversed(_field_literals(task, field, out)):
        new = plan.get((role, value))
        if new is not None:
            out = out[:start] + str(new) + out[end:]
    return out


def _rewrite_context(context: str, old_expr: str, new_expr: str,
                     plan: dict[tuple[str, int], int]) -> str | None:
    """Keep the prose framing true after the numbers move, or return None if we cannot.

    Three cases, in order:
      1. the context quotes the expression, either in sympy syntax ("output = (w2*(w1*3))**2.")
         or with `^` for the power ("Loss L = (a*x - 7)^2"), so substitute the whole quotation
      2. the context has no digits at all ("Shifted quadratic."), so leave it alone
      3. the context restates the numbers in words ("the target is 3"), so apply the same value
         map, which is only safe when every number in the prose is one the plan knows about

    Anything else returns None and the candidate is dropped. A stem whose sentence contradicts its
    own mathematics is worse than no twin.
    """
    for old, new in ((old_expr, new_expr),
                     (old_expr.replace("**", "^"), new_expr.replace("**", "^"))):
        if old and old in context:
            return context.replace(old, new)
    values = [int(m.group()) for m in INT_RE.finditer(context)]
    if not values:
        return context
    known = {v for (role, v) in plan if role == "plain"}
    mapping = {v: plan[("plain", v)] for (role, v) in plan if role == "plain"}
    if not all(v in known for v in values):
        return None
    return INT_RE.sub(lambda m: str(mapping[int(m.group())]), context)


# --------------------------------------------------------------------------- answer shape

def _parts(answer):
    """Flatten an answer into scalar expressions, whatever container it arrived in."""
    if isinstance(answer, sp.MatrixBase):
        return list(answer)
    if isinstance(answer, sp.FiniteSet):
        return list(answer.args)
    return [answer]


def _has_radical(answer) -> bool:
    for part in _parts(answer):
        try:
            for p in part.atoms(sp.Pow):
                if p.exp.is_Rational and not p.exp.is_Integer:
                    return True
        except Exception:                                     # noqa: BLE001
            continue
    return False


def _free_symbols(answer) -> frozenset[str]:
    out: set[str] = set()
    for part in _parts(answer):
        try:
            out |= {str(s) for s in part.free_symbols}
        except Exception:                                     # noqa: BLE001
            pass
    return frozenset(out)


def _shape(answer) -> tuple:
    """A coarse description of what kind of answer this is.

    The twin's answer has to be the same kind of object as the original's, not merely a valid
    answer to a valid question. Without this, a `solve` drill written for rational roots twins into
    one that needs the quadratic formula, and a unit-circle drill whose answer was
    $\\tfrac{\\sqrt{3}}{2}$ twins into one whose answer is $1$. Both are harder or easier than the
    drill they replace, which makes the twin useless as evidence about the same skill.
    """
    parts = _parts(answer)
    return (
        _free_symbols(answer),
        _has_radical(answer),
        all(bool(getattr(p, "is_number", False)) for p in parts),
        all(bool(getattr(p, "is_Integer", False)) for p in parts),
    )


def _is_zero(answer) -> bool:
    parts = _parts(answer)
    if not parts:
        return True
    return all(getattr(p, "is_zero", False) for p in parts)


def _descends(params: dict, new_w) -> bool:
    """A gradient descent twin must still go downhill.

    The update itself is CAS-computed, so its sign is right by construction, but a twin that moved
    the learning rate or the starting point could still overshoot into a higher loss. That would
    be a drill teaching the opposite of the node it is tagged to, so it is rejected.
    """
    try:
        loss = dt.P(params["expr"])
        w = dt.S(params.get("var", "w"))
        w0 = dt.P(params["at"])
        if sp.simplify(sp.diff(loss, w).subs(w, w0)) == 0:
            return False                                      # no step at all
        before = complex(sp.N(loss.subs(w, w0)))
        after = complex(sp.N(loss.subs(w, new_w)))
        if abs(before.imag) > 1e-9 or abs(after.imag) > 1e-9:
            return False
        return after.real < before.real
    except Exception:                                         # noqa: BLE001
        return False


# --------------------------------------------------------------------------- the twin

def _rng(item: dict, seed: int, attempt: int) -> random.Random:
    """Deterministic per (item, seed, attempt), and independent of dict ordering."""
    payload = json.dumps({"id": item.get("item_id"), "check": item.get("check")},
                         sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(f"{payload}\x00{seed}\x00{attempt}".encode("utf-8")).hexdigest()
    return random.Random(int(digest, 16))


def twin_of(item: dict, seed: int) -> dict | None:
    """An isomorphic twin of `item`, or None if one cannot be built and verified.

    None is a normal, expected outcome and always means one of:
      - the item has no `check` spec (every `source: openstax` item, which is most of the bank)
      - its spec has no literal that may be moved (`sin(x)**2 + cos(x)**2` is all exponents)
      - every candidate perturbation was rejected by the CAS or by a property guard

    The returned dict has the same shape as the input, with `item_id` suffixed `-twin<seed>` and
    a `twin_of` key naming the original. `answer_latex`, `answer_sympy` and `answer_kind` are
    recomputed from the new spec by sympy; nothing is carried over from the original answer.
    """
    check = (item or {}).get("check")
    if not check or not check.get("task") or not (check.get("params") or {}).get("expr"):
        return None                                            # openstax, or an unusable spec

    task = check["task"]
    params = dict(check["params"])
    if task not in dt.TASKS:
        return None

    try:
        original_stem, _, original_answer = dt.build(task, params)
    except Exception:                                          # noqa: BLE001
        return None                                            # the source drill no longer builds

    want_shape = _shape(original_answer)

    for attempt in range(MAX_TRIES):
        rng = _rng(item, seed, attempt)
        plan = _plan(task, params, rng)
        if not plan:
            return None                                        # nothing in this spec may move

        new_params = dict(params)
        for field in NUMERIC_FIELDS:
            if params.get(field):
                new_params[field] = _rewrite(str(params[field]), task, field, plan)

        if params.get("context"):
            ctx = _rewrite_context(str(params["context"]), str(params.get("expr") or ""),
                                   str(new_params.get("expr") or ""), plan)
            if ctx is None:
                continue
            new_params["context"] = ctx

        # Isomorphism, checked rather than assumed: only digits may have moved.
        if any(skeleton(params.get(f) or "") != skeleton(new_params.get(f) or "")
               for f in NUMERIC_FIELDS):
            continue
        if all(str(params.get(f) or "") == str(new_params.get(f) or "") for f in NUMERIC_FIELDS):
            continue                                           # identical spec, not a twin

        try:
            stem, answer_latex, answer = dt.build(task, new_params)
        except Exception:                                      # noqa: BLE001
            continue                                           # the guard rejected it: try again

        if stem == original_stem or stem == item.get("stem_latex"):
            continue
        if _is_zero(answer):
            continue                                           # degenerate, not a drill
        if _shape(answer) != want_shape:
            continue                                           # a different kind of answer
        if task == "gd_step" and not _descends(new_params, answer):
            continue

        kind, answer_sympy = dt.serialize_answer(answer)
        twin = dict(item)
        twin.update({
            "item_id": f"{item.get('item_id')}-twin{seed}",
            "stem_latex": stem,
            "answer_latex": answer_latex,
            "answer_kind": kind,
            "answer_sympy": answer_sympy,
            "check": {"task": task, "params": new_params},
            "twin_of": item.get("item_id"),
        })
        return twin

    return None


# --------------------------------------------------------------------------- report

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--items", default=str(ITEMS_PATH))
    args = ap.parse_args()

    items = json.loads(Path(args.items).read_text())["items"]
    with_check = [i for i in items if i.get("check")]
    ok, no_literal, per_node_ok, per_node_all = 0, 0, Counter(), Counter()
    for it in with_check:
        per_node_all[it["node_id"]] += 1
        if twin_of(it, args.seed) is not None:
            ok += 1
            per_node_ok[it["node_id"]] += 1
        elif not _plan(it["check"]["task"], it["check"].get("params") or {},
                       _rng(it, args.seed, 0)):
            no_literal += 1

    print(f"bank: {len(items)} items, {len(with_check)} with a check spec "
          f"({len(items) - len(with_check)} openstax, no twin possible)")
    print(f"twins at seed {args.seed}: {ok}/{len(with_check)} "
          f"({100.0 * ok / max(1, len(with_check)):.0f}%)")
    print(f"  no movable literal in the spec : {no_literal}   (sin(x), exp(x), cos(pi))")
    print(f"  rejected by the CAS or a guard : {len(with_check) - ok - no_literal}\n")
    for node in sorted(per_node_all):
        got, total = per_node_ok[node], per_node_all[node]
        flag = "" if got == total else "   <--"
        print(f"  {node:<32} {got:2d}/{total:2d}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
