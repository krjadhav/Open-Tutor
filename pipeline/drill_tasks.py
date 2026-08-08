"""Checkable drill templates.

The generator does NOT ask the model for an answer. It asks for a *spec* (a task name plus
sympy-syntax parameters), then sympy computes the answer and the stem is rendered from the same
spec. Two consequences worth the extra code:

  - a wrong answer is impossible by construction, not merely validated after the fact
  - the question and the answer cannot drift apart, which is the usual failure mode when an LLM
    writes both

Anything sympy cannot evaluate, or that turns out to be a trivial no-op drill, is rejected.
"""

import re

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



# ---------------------------------------------------------------- display
#
# The question shown to a student is rendered from the SPEC'S SOURCE TEXT, never from a parsed
# sympy expression. sympy normalises: it flattens nested Add, distributes a unary minus and
# reorders terms, all before anything reaches the page. That silently rewrote the question.
# Measured examples that shipped:
#     -( (2*x + 3) - (x - 5) )  displayed as  "-x - 5 - 3"     the bracket, and the skill, gone
#     -( (1/2)*x - 3/4 )        displayed as  "-x/2 + 3/4"     which is the ANSWER, shown as the question
# Both were on alg.sign-distribution, the node the demo's headline diagnosis lands on.
#
# sympy is still the sole authority on the ANSWER. It is simply not allowed near the QUESTION.
# This transformation is purely syntactic: it never reassociates, reorders or cancels anything.

_FUNCS_TEX = ("arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
              "sin", "cos", "tan", "sec", "csc", "cot", "log", "exp", "sqrt")


def source_to_latex(src):
    """Prettify a sympy-syntax source string for display, preserving structure exactly."""
    s = str(src).strip()
    s = re.sub(r"\s+", " ", s)

    # exponents: ** -> ^, braced so multi-character exponents group correctly
    def _pow(m):
        return "^{" + m.group(1).strip() + "}"
    s = re.sub(r"\*\*\s*\(([^()]*)\)", _pow, s)          # x**(-3)
    s = re.sub(r"\*\*\s*(-?\w+(?:/\w+)?)", _pow, s)        # x**7, x**2/3

    # sqrt(...) with balanced inner parens
    while True:
        m = re.search(r"\bsqrt\(", s)
        if not m:
            break
        i, depth = m.end(), 1
        while i < len(s) and depth:
            depth += (s[i] == "(") - (s[i] == ")")
            i += 1
        s = s[:m.start()] + "\\sqrt{" + s[m.end():i - 1] + "}" + s[i:]

    for f in _FUNCS_TEX:
        if f != "sqrt":
            s = re.sub(rf"\b{f}\b", "\\\\" + f, s)
    s = re.sub(r"\bpi\b", r"\\pi", s)
    s = re.sub(r"\bE\b", "e", s)

    # implicit multiplication: drop * where juxtaposition reads correctly
    s = re.sub(r"(?<=[\w)\}])\s*\*\s*(?=[\w(\\])", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Each task: (render(params) -> stem, solve(params) -> answer expr, guard(params, answer) -> bool)
# The guard rejects drills that are technically valid but pedagogically useless.

def t_differentiate(p):
    e, v = P(p["expr"]), S(p.get("var", "x"))
    return f"Differentiate $f({v}) = {source_to_latex(p['expr'])}$.", sp.diff(e, v)


def t_derivative_at(p):
    e, v, a = P(p["expr"]), S(p.get("var", "x")), P(p["at"])
    return (f"Find the slope of the tangent to $y = {source_to_latex(p['expr'])}$ at ${v} = {L(a)}$.",
            sp.simplify(sp.diff(e, v).subs(v, a)))


def t_partial(p):
    e, v = P(p["expr"]), S(p.get("var", "x"))
    ctx = p.get("context")
    stem = f"Find $\\frac{{\\partial f}}{{\\partial {v}}}$ for $f = {source_to_latex(p['expr'])}$."
    return ((f"{ctx} {stem}" if ctx else stem), sp.diff(e, v))


def t_gradient(p):
    e = P(p["expr"])
    x, y = S("x"), S("y")
    return (f"Find the gradient $\\nabla f$ of $f(x,y) = {source_to_latex(p['expr'])}$.",
            sp.Matrix([sp.diff(e, x), sp.diff(e, y)]))


def t_expand(p):
    e = P(p["expr"])
    return f"Expand and simplify ${source_to_latex(p['expr'])}$.", sp.expand(e)


def t_factor(p):
    e = P(p["expr"])
    return f"Factor ${source_to_latex(p['expr'])}$.", sp.factor(e)


def t_simplify(p):
    e = P(p["expr"])
    return f"Simplify ${source_to_latex(p['expr'])}$.", sp.simplify(sp.together(e))


def t_solve(p):
    lhs, rhs, v = P(p["expr"]), P(p.get("expr2", "0")), S(p.get("var", "x"))
    sols = sp.solve(sp.Eq(lhs, rhs), v)
    return (f"Solve for ${v}$: ${source_to_latex(p['expr'])} = "
            f"{source_to_latex(p.get('expr2','0'))}$.", sp.FiniteSet(*sols))


def t_evaluate(p):
    e = P(p["expr"])
    return f"Evaluate ${source_to_latex(p['expr'])}$, giving an exact value.", sp.simplify(e)


def t_compose(p):
    f, g, v = P(p["expr"]), P(p["expr2"]), S(p.get("var", "x"))
    return (f"If $f({v}) = {source_to_latex(p['expr'])}$ and $g({v}) = {source_to_latex(p['expr2'])}$, "
            f"find $f(g({v}))$.",
            sp.simplify(f.subs(v, g)))


def t_limit(p):
    e, v, a = P(p["expr"]), S(p.get("var", "x")), P(p["at"])
    return f"Evaluate $\\lim_{{{v} \\to {L(a)}}} {source_to_latex(p['expr'])}$.", sp.limit(e, v, a)


def t_gd_step(p):
    """One gradient descent update. Answer computed, so the descent sign is right by construction."""
    loss, w = P(p["expr"]), S(p.get("var", "w"))
    w0, lr = P(p["at"]), P(p["lr"])
    grad = sp.diff(loss, w).subs(w, w0)
    ctx = p.get("context", "")
    stem = (f"The loss is $L({w}) = {source_to_latex(p['expr'])}$. Using a learning rate of $\\alpha = {L(lr)}$ and "
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



# ---------------------------------------------------------------- vectors
#
# alg.vectors was added by the graph audit (D3): the course target is w := w - alpha * grad(L),
# a scalar-times-vector subtraction, and nothing in the graph covered vector mechanics. Because
# alg.vectors directly gates mv.gradient, mv.directional-derivative and the target itself, a node
# with no items leaves the goal unreachable by practice: it can never be attempted, so it never
# leaves the frontier, so its dependents stay locked forever.
#
# These tasks are deliberately shaped like the operations the target actually performs.

def _vec(s):
    return sp.Matrix([P(t) for t in str(s).split(",")])


def _row(v):
    return sp.latex(v.T)


def t_dot_product(p):
    u, v = _vec(p["expr"]), _vec(p["expr2"])
    if len(u) != len(v):
        raise ValueError("dot product needs vectors of equal length")
    return (f"Given $\\mathbf{{u}} = {_row(u)}$ and $\\mathbf{{v}} = {_row(v)}$, "
            f"find $\\mathbf{{u}} \\cdot \\mathbf{{v}}$.", sp.simplify(u.dot(v)))


def t_scalar_multiple(p):
    k, u = P(p["expr"]), _vec(p["expr2"])
    return (f"Compute ${sp.latex(k)}\\,{_row(u)}$.", sp.simplify(k * u))


def t_vector_magnitude(p):
    u = _vec(p["expr"])
    return (f"Find $\\lVert\\mathbf{{u}}\\rVert$ for $\\mathbf{{u}} = {_row(u)}$.",
            sp.simplify(sp.sqrt(sum(x ** 2 for x in u))))


def t_vector_update(p):
    """Exactly the gradient descent update, done on numbers before it is done on a loss."""
    w, grad, a = _vec(p["expr"]), _vec(p["expr2"]), P(p["lr"])
    return (f"A parameter vector is $\\mathbf{{w}} = {_row(w)}$ and the gradient is "
            f"$\\nabla L = {_row(grad)}$. With a learning rate of $\\alpha = {sp.latex(a)}$, "
            f"compute $\\mathbf{{w}} - \\alpha\\,\\nabla L$.", sp.simplify(w - a * grad))



def t_nth_derivative(p):
    """Second and higher derivatives. der.higher-order had no gradeable items, and because it
    gates opt.local-extrema which gates the target, that single gap made the course goal
    unreachable by practice."""
    e, v = P(p["expr"]), S(p.get("var", "x"))
    n = int(P(p.get("at", "2")))
    ordinal = {2: "second", 3: "third", 4: "fourth"}.get(n, f"{n}th")
    return (f"Find the {ordinal} derivative of $f({v}) = {source_to_latex(p['expr'])}$.", sp.diff(e, v, n))



def t_critical_points(p):
    e, v = P(p["expr"]), S(p.get("var", "x"))
    sols = [c for c in sp.solve(sp.Eq(sp.diff(e, v), 0), v) if c.is_real]
    if not sols:
        raise ValueError("no real critical points")
    return (f"Find the {v}-values of the critical points of $f({v}) = {source_to_latex(p['expr'])}$.",
            sp.FiniteSet(*sols))


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
    "dot_product": t_dot_product,
    "scalar_multiple": t_scalar_multiple,
    "vector_magnitude": t_vector_magnitude,
    "vector_update": t_vector_update,
    "nth_derivative": t_nth_derivative,
    "critical_points": t_critical_points,
}


def _nontrivial(task, params, answer):
    """Reject drills where there is nothing to do."""
    try:
        src = str(params.get("expr", ""))
        if task == "expand" and "(" not in src:
            # nothing bracketed to distribute, so there is no expanding to do whatever the
            # rendered strings happen to look like
            return False, "expand drill with no bracket to expand"
        if task == "factor" and "(" not in sp.latex(answer):
            return False, "factor drill whose answer does not factor"
        if task in ("simplify", "evaluate") and source_to_latex(src) == sp.latex(answer):
            return False, "answer identical to the question as displayed, no work to do"
        if isinstance(answer, sp.MatrixBase):
            return (True, None) if any(x != 0 for x in answer) else (False, "zero vector")
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
