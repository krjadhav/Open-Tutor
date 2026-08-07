"""Checkable drill templates.

The generator does NOT ask the model for an answer. It asks for a *spec* (a task name plus
sympy-syntax parameters), then sympy computes the answer and the stem is rendered from the same
spec. Two consequences worth the extra code:

  - a wrong answer is impossible by construction, not merely validated after the fact
  - the question and the answer cannot drift apart, which is the usual failure mode when an LLM
    writes both

Anything sympy cannot evaluate, or that turns out to be a trivial no-op drill, is rejected.
"""

import sympy as sp
from sympy.parsing.sympy_parser import (parse_expr, standard_transformations,
                                        implicit_multiplication)

# NOT implicit_multiplication_application: that bundle includes split_symbols, which shreds
# multi-character names. `w1` becomes w*1 and `w2` becomes 2*w, so a backprop drill silently
# differentiates a variable that no longer exists and every answer comes out 0. Plain
# implicit_multiplication still turns 2x into 2*x, which is all we actually wanted.
TRANSFORMS = standard_transformations + (implicit_multiplication,)


def P(s):
    """Parse a sympy-syntax string. Raises on anything unparseable."""
    if s is None or str(s).strip() == "":
        raise ValueError("empty expression")
    return parse_expr(str(s), transformations=TRANSFORMS, evaluate=True)


def PU(s):
    """Parse WITHOUT evaluating, for display only.

    sympy simplifies at parse time, so `-(3x-2)+5x` becomes `8x+2` before we ever render it and
    the drill silently turns into "expand 8x+2". Questions must be rendered from the unevaluated
    form and answers computed from the evaluated one.
    """
    try:
        return parse_expr(str(s), transformations=TRANSFORMS, evaluate=False)
    except Exception:  # noqa: BLE001
        return P(s)


def S(name):
    return sp.Symbol(str(name))


def L(e):
    return sp.latex(e)


def _same(a, b):
    """Symbolic equality, tolerant of unsimplified forms."""
    try:
        return sp.simplify(sp.together(a - b)) == 0
    except Exception:  # noqa: BLE001
        return False


# Each task: (render(params) -> stem, solve(params) -> answer expr, guard(params, answer) -> bool)
# The guard rejects drills that are technically valid but pedagogically useless.

def t_differentiate(p):
    e, v = P(p["expr"]), S(p.get("var", "x"))
    return f"Differentiate $f({v}) = {L(PU(p['expr']))}$.", sp.diff(e, v)


def t_derivative_at(p):
    e, v, a = P(p["expr"]), S(p.get("var", "x")), P(p["at"])
    return (f"Find the slope of the tangent to $y = {L(e)}$ at ${v} = {L(a)}$.",
            sp.simplify(sp.diff(e, v).subs(v, a)))


def t_partial(p):
    e, v = P(p["expr"]), S(p.get("var", "x"))
    ctx = p.get("context")
    stem = f"Find $\\frac{{\\partial f}}{{\\partial {v}}}$ for $f = {L(e)}$."
    return ((f"{ctx} {stem}" if ctx else stem), sp.diff(e, v))


def t_gradient(p):
    e = P(p["expr"])
    x, y = S("x"), S("y")
    return (f"Find the gradient $\\nabla f$ of $f(x,y) = {L(e)}$.",
            sp.Matrix([sp.diff(e, x), sp.diff(e, y)]))


def t_expand(p):
    e = P(p["expr"])
    return f"Expand and simplify ${L(PU(p['expr']))}$.", sp.expand(e)


def t_factor(p):
    e = P(p["expr"])
    return f"Factor ${L(PU(p['expr']))}$.", sp.factor(e)


def t_simplify(p):
    e = P(p["expr"])
    return f"Simplify ${L(PU(p['expr']))}$.", sp.simplify(sp.together(e))


def t_solve(p):
    lhs, rhs, v = P(p["expr"]), P(p.get("expr2", "0")), S(p.get("var", "x"))
    sols = sp.solve(sp.Eq(lhs, rhs), v)
    return f"Solve for ${v}$: ${L(lhs)} = {L(rhs)}$.", sp.FiniteSet(*sols)


def t_evaluate(p):
    e = P(p["expr"])
    return f"Evaluate ${L(PU(p['expr']))}$, giving an exact value.", sp.simplify(e)


def t_compose(p):
    f, g, v = P(p["expr"]), P(p["expr2"]), S(p.get("var", "x"))
    return (f"If $f({v}) = {L(f)}$ and $g({v}) = {L(g)}$, find $f(g({v}))$.",
            sp.simplify(f.subs(v, g)))


def t_limit(p):
    e, v, a = P(p["expr"]), S(p.get("var", "x")), P(p["at"])
    return f"Evaluate $\\lim_{{{v} \\to {L(a)}}} {L(PU(p['expr']))}$.", sp.limit(e, v, a)


def t_gd_step(p):
    """One gradient descent update. Answer computed, so the descent sign is right by construction."""
    loss, w = P(p["expr"]), S(p.get("var", "w"))
    w0, lr = P(p["at"]), P(p["lr"])
    grad = sp.diff(loss, w).subs(w, w0)
    ctx = p.get("context", "")
    stem = (f"The loss is $L({w}) = {L(loss)}$. Using a learning rate of $\\alpha = {L(lr)}$ and "
            f"starting from ${w} = {L(w0)}$, perform one gradient descent update. "
            f"What is the new value of ${w}$?")
    return ((f"{ctx} {stem}" if ctx else stem), sp.simplify(w0 - lr * grad))


def t_local_min_x(p):
    e, v = P(p["expr"]), S(p.get("var", "x"))
    d1, d2 = sp.diff(e, v), sp.diff(e, v, 2)
    crit = [c for c in sp.solve(sp.Eq(d1, 0), v) if c.is_real]
    mins = [c for c in crit if d2.subs(v, c) > 0]
    if len(mins) != 1:
        raise ValueError("need exactly one local minimum")
    return (f"Find the $x$-coordinate of the local minimum of $f({v}) = {L(e)}$.",
            sp.simplify(mins[0]))


TASKS = {
    "differentiate": t_differentiate,
    "derivative_at": t_derivative_at,
    "partial": t_partial,
    "gradient": t_gradient,
    "expand": t_expand,
    "factor": t_factor,
    "simplify": t_simplify,
    "solve": t_solve,
    "evaluate": t_evaluate,
    "compose": t_compose,
    "limit": t_limit,
    "gd_step": t_gd_step,
    "local_min_x": t_local_min_x,
}


def _nontrivial(task, params, answer):
    """Reject drills where there is nothing to do."""
    try:
        if task in ("expand", "factor", "simplify", "evaluate"):
            # compare the DISPLAYED question against the answer: if they render the same there
            # is nothing for the student to do
            if sp.latex(PU(params["expr"])) == sp.latex(answer):
                return False, "answer identical to the question as displayed, no work to do"
        if task == "differentiate" and answer.is_number:
            return False, "derivative is a constant, too trivial"
        if answer is None:
            return False, "no answer"
        if answer.free_symbols and len(str(answer)) > 220:
            return False, "answer too unwieldy to type"
        if str(answer) in ("nan", "zoo", "oo*I"):
            return False, f"degenerate answer {answer}"
    except Exception as e:  # noqa: BLE001
        return False, f"guard failed: {e}"
    return True, None


def build(task, params):
    """Returns (stem_latex, answer_latex, answer_sympy) or raises."""
    if task not in TASKS:
        raise ValueError(f"unknown task {task}")
    stem, answer = TASKS[task](params)
    ok, why = _nontrivial(task, params, answer)
    if not ok:
        raise ValueError(why)
    return stem, sp.latex(answer), answer


def serialize_answer(ans):
    """(kind, string) that round-trips through load_answer.

    str() of a FiniteSet is "{2, 3}" and of a Matrix is "Matrix([[..]])"; neither parses back, so
    a stored answer would be unloadable at grading time.
    """
    if isinstance(ans, sp.FiniteSet):
        return "set", ", ".join(str(a) for a in sorted(ans, key=str))
    if isinstance(ans, sp.MatrixBase):
        return "vector", ", ".join(str(a) for a in ans)
    return "expr", str(ans)


def load_answer(kind, s):
    if kind == "set":
        return sp.FiniteSet(*[P(t) for t in str(s).split(",")])
    if kind == "vector":
        return sp.Matrix([P(t) for t in str(s).split(",")])
    return P(s)


def check_student_answer(answer_sympy, typed):
    """The production grader: is a typed answer equivalent to the stored one?

    Deliberately generous about form. A student who writes 2x - 3 unsimplified, or x - 5 + x + 2,
    is correct, and marking them wrong is the most damaging error the system can make.
    """
    try:
        got = P(typed)
    except Exception:  # noqa: BLE001
        return False
    if isinstance(answer_sympy, sp.FiniteSet):
        try:
            return sp.FiniteSet(*[P(t) for t in str(typed).split(",")]) == answer_sympy
        except Exception:  # noqa: BLE001
            return False
    if isinstance(answer_sympy, sp.Matrix):
        try:
            return sp.simplify(answer_sympy - sp.Matrix(got)) == sp.zeros(*answer_sympy.shape)
        except Exception:  # noqa: BLE001
            return False
    return _same(answer_sympy, got)
