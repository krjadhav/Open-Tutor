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

#: The LaTeX commands a generated task may emit. This began as a hard constraint: the app had no
#: LaTeX library, so an unknown command lost its backslash and printed as a literal word.
#: `\mathbf{u}` printed "mathbfu" and a sympy Matrix printed "beginmatrix ... endmatrix", and both
#: of those reached a card. The app now draws mathematics with KaTeX, which renders all of it, so
#: the list is no longer what stands between the bank and a visible bug.
#:
#: It is kept, and kept narrow, because the generated bank is checked in and regenerating it is a
#: content change, not a rendering one. `unsupported_commands` is still asserted over the whole
#: bank in pipeline/tests/test_drill_tasks.py; widening this list is now a deliberate decision
#: about the maths, not a workaround for the renderer.
#:
#: Vectors are written as tuples by `_row` and a norm as ||u||, which need no command at all.
RENDERABLE_COMMANDS = frozenset({
    "cdot", "nabla", "partial", "frac", "sqrt", "text", "alpha", "pi", "left", "right",
    "to", "infty", "times", "div", "sin", "cos", "tan", "log", "exp", "lim",
})

_COMMAND_RE = re.compile(r"\\([a-zA-Z]+)")


def unsupported_commands(text):
    """The LaTeX commands in `text` that the app cannot draw, as a sorted list."""
    return sorted({c for c in _COMMAND_RE.findall(str(text or ""))
                   if c not in RENDERABLE_COMMANDS})


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


# The three limit nodes are separated by *what the substitution does*, not by how the stem is
# worded, so each one gets a task whose precondition the CAS checks:
#
#   lim.direct-substitution     f(a) exists and is the limit
#   lim.concept                 f(a) does not exist, yet the limit does
#   lim.indeterminate-factoring f(a) is 0/0 specifically, so there is a factor to cancel
#
# Without those checks the same expression could be filed under any of the three, and a student
# blamed on the wrong node is exactly the failure the graph exists to prevent.

def _limit_spec(p):
    e, v, a = P(p["expr"]), S(p.get("var", "x")), P(p["at"])
    if v not in e.free_symbols:
        raise ValueError("the expression does not involve the limit variable")
    if e.is_Symbol:
        raise ValueError("nothing to evaluate")
    value = e.subs(v, a)
    limit = sp.limit(e, v, a)
    return e, v, a, value, limit


def _finite(x):
    return bool(x.is_number and x.is_finite)


def t_limit_substitution(p):
    e, v, a, value, limit = _limit_spec(p)
    if not _finite(value):
        raise ValueError("substitution does not give a value, so this is not that skill")
    if sp.simplify(value - limit) != 0:
        raise ValueError("substituting does not give the limit")
    return (f"Evaluate $\\lim_{{{v} \\to {L(a)}}} {source_to_latex(p['expr'])}$ "
            f"by direct substitution.", sp.simplify(limit))


def t_limit_informal(p):
    e, v, a, value, limit = _limit_spec(p)
    if _finite(value):
        raise ValueError("the function has a value there, so nothing distinguishes the limit")
    if not _finite(limit):
        raise ValueError("the limit is not a finite value")
    return (f"$f({v}) = {source_to_latex(p['expr'])}$ has no value at ${v} = {L(a)}$. What value "
            f"does $f({v})$ approach as ${v} \\to {L(a)}$?", sp.simplify(limit))


def t_limit_indeterminate(p):
    e, v, a, _value, limit = _limit_spec(p)
    num, den = sp.fraction(sp.together(e))
    if sp.simplify(den.subs(v, a)) != 0 or sp.simplify(num.subs(v, a)) != 0:
        raise ValueError("substitution does not give 0/0, so there is no factor to cancel")
    if not _finite(limit):
        raise ValueError("the limit is not a finite value")
    return (f"Direct substitution gives $0/0$. Evaluate "
            f"$\\lim_{{{v} \\to {L(a)}}} {source_to_latex(p['expr'])}$.", sp.simplify(limit))


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
    """Vectors render as (a, b, c), not as a LaTeX matrix.

    sympy's latex() emits \\left[\\begin{matrix}...\\end{matrix}\\right], which the app's small
    LaTeX renderer cannot draw, so it leaked the command names as literal words onto the card.
    Tuple notation is standard for a coordinate vector, reads correctly at card size, and needs
    no renderer support at all. The answer is a vector too, so it goes through the same path.
    """
    return "(" + ", ".join(sp.latex(x) for x in v) + ")"


def t_dot_product(p):
    u, v = _vec(p["expr"]), _vec(p["expr2"])
    if len(u) != len(v):
        raise ValueError("dot product needs vectors of equal length")
    return (f"Given $u = {_row(u)}$ and $v = {_row(v)}$, "
            f"find $u \\cdot v$.", sp.simplify(u.dot(v)))


def t_scalar_multiple(p):
    k, u = P(p["expr"]), _vec(p["expr2"])
    return (f"Compute ${sp.latex(k)} {_row(u)}$.", sp.simplify(k * u))


def t_vector_magnitude(p):
    u = _vec(p["expr"])
    return (f"Find $||u||$ for $u = {_row(u)}$.",
            sp.simplify(sp.sqrt(sum(x ** 2 for x in u))))


def t_vector_update(p):
    """Exactly the gradient descent update, done on numbers before it is done on a loss."""
    w, grad, a = _vec(p["expr"]), _vec(p["expr2"]), P(p["lr"])
    return (f"A parameter vector is $w = {_row(w)}$ and the gradient is "
            f"$\\nabla L = {_row(grad)}$. With a learning rate of $\\alpha = {sp.latex(a)}$, "
            f"compute $w - \\alpha \\nabla L$.", sp.simplify(w - a * grad))



def t_derivative_from_definition(p):
    """der.definition, without asking a student to type a symbolic difference quotient.

    The node is "the derivative as a limit of the difference quotient", and the honest way to
    keep the drill about that and still have a typeable answer is to ask for f'(x) and let the
    CAS take the limit of the quotient itself. sp.diff is checked against that limit rather than
    trusted, so a spec where the two disagree (a function with a corner, say) is rejected instead
    of shipping a stem that says "use the definition" above an answer the definition does not
    give.
    """
    e, v = P(p["expr"]), S(p.get("var", "x"))
    h = sp.Symbol("h")
    quotient_limit = sp.simplify(sp.limit((e.subs(v, v + h) - e) / h, h, 0))
    if sp.simplify(quotient_limit - sp.diff(e, v)) != 0:
        raise ValueError("the difference quotient limit disagrees with the derivative")
    if quotient_limit == 0:
        raise ValueError("derivative is zero, nothing to find")
    return (f"Use the limit definition of the derivative to find $f'({v})$ for "
            f"$f({v}) = {source_to_latex(p['expr'])}$.", quotient_limit)


def _needs_chain_rule(e, v):
    """True if some subexpression is a genuine composition in v, not just a power of v.

    x**5 and sin(x) are power-rule and trig-rule drills wearing a chain rule label. What makes a
    chain rule drill is an inner function that is not the bare variable: (3x**2+1)**5, sin(2x),
    sqrt(x**2+1), exp(-x**2). Checked structurally so a spec cannot be mis-tagged by its author.
    """
    for sub in sp.preorder_traversal(e):
        if isinstance(sub, sp.Pow):
            base = sub.base
            if v in base.free_symbols and not base.is_Symbol and sub.exp != 1:
                return True
        elif isinstance(sub, sp.Function) and sub.args:
            inner = sub.args[0]
            if v in inner.free_symbols and not inner.is_Symbol:
                return True
    return False


def t_chain_rule(p):
    """der.chain-rule. Same computation as `differentiate`, but the spec has to earn the node."""
    e, v = P(p["expr"]), S(p.get("var", "x"))
    if not _needs_chain_rule(e, v):
        raise ValueError("no composition here, so the chain rule is not needed")
    return (f"Use the chain rule to differentiate $f({v}) = {source_to_latex(p['expr'])}$.",
            sp.diff(e, v))


def t_implicit_dydx(p):
    """der.implicit. F(x, y) = 0 gives dy/dx = -F_x / F_y.

    The variables are fixed as x and y rather than taken from the spec: implicit differentiation
    is written in those letters everywhere, and a free choice of names is one more thing an
    author can get wrong. `expr` is the left side of the equation and `expr2` the right, both
    rendered exactly as written.
    """
    x, y = S("x"), S("y")
    lhs_src, rhs_src = p["expr"], str(p.get("expr2", "0"))
    F = P(lhs_src) - P(rhs_src)
    if not {x, y} <= F.free_symbols:
        raise ValueError("an implicit equation must involve both x and y")
    Fx, Fy = sp.diff(F, x), sp.diff(F, y)
    if Fy == 0:
        raise ValueError("the equation does not determine y")
    if not Fy.free_symbols:
        # x**2 + y = 5 has F_y = 1: you would solve for y and differentiate. That is not the skill.
        raise ValueError("linear in y, so this is explicit differentiation in disguise")
    return (f"Find $\\frac{{dy}}{{dx}}$ for "
            f"${source_to_latex(lhs_src)} = {source_to_latex(rhs_src)}$.",
            sp.simplify(-Fx / Fy))


def t_chain_rule_multivar(p):
    """mv.chain-rule-multivar: z = f(x, y) with x and y both functions of t, so dz/dt sums two
    paths. `partial` on a composed expression would not do: a single partial derivative never
    forces the student to add the paths up, which is the whole content of the node and the exact
    thing its blame_hint fences off ("omitting one path").

    `expr2` carries the two inner functions as "x(t), y(t)", the same comma form `_vec` uses.
    """
    z = P(p["expr"])
    x, y, t = S("x"), S("y"), S(p.get("var", "t"))
    inner_src = [s.strip() for s in str(p["expr2"]).split(",")]
    if len(inner_src) != 2:
        raise ValueError("expr2 must be two comma-separated functions of the parameter")
    gx, gy = P(inner_src[0]), P(inner_src[1])
    if not {x, y} <= z.free_symbols:
        raise ValueError("z must depend on both x and y, or there is only one path")
    if t not in gx.free_symbols or t not in gy.free_symbols:
        raise ValueError("both inner functions must depend on the parameter")
    total = sp.diff(z, x) * sp.diff(gx, t) + sp.diff(z, y) * sp.diff(gy, t)
    answer = sp.simplify(total.subs({x: gx, y: gy}, simultaneous=True))
    return (f"For $z = {source_to_latex(p['expr'])}$ with $x = {source_to_latex(inner_src[0])}$ "
            f"and $y = {source_to_latex(inner_src[1])}$, find $\\frac{{dz}}{{d{t}}}$.", answer)


def t_directional_derivative(p):
    """mv.directional-derivative: grad f at a point, dotted with a UNIT direction.

    The direction in the spec is deliberately NOT assumed to be a unit vector, and a spec that
    supplies one is rejected. Forgetting to normalise is the named misconception for this node
    (see its blame_hint), so a drill whose direction is already unit length cannot detect it: the
    student who divides by ||u|| and the student who does not both write the same answer.

    `at` is the point and `expr2` the direction, both as comma-separated components, and the
    variables are x, y, z in that order, taken to match the number of components.
    """
    e = P(p["expr"])
    point, direction = _vec(p["at"]), _vec(p["expr2"])
    if len(point) != len(direction):
        raise ValueError("the point and the direction need the same number of components")
    names = [S(n) for n in ("x", "y", "z")][:len(point)]
    if not e.free_symbols <= set(names):
        raise ValueError("f uses a variable the point does not give a value for")
    norm = sp.sqrt(sum(c ** 2 for c in direction))
    if norm == 0:
        raise ValueError("the direction vector is zero")
    if sp.simplify(norm - 1) == 0:
        raise ValueError("the direction is already a unit vector, so normalising is not exercised")
    where = {n: point[i] for i, n in enumerate(names)}
    grad = sp.Matrix([sp.diff(e, n).subs(where, simultaneous=True) for n in names])
    if all(g == 0 for g in grad):
        raise ValueError("the gradient vanishes at the point, so every direction gives zero")
    return (f"Find the directional derivative of $f = {source_to_latex(p['expr'])}$ at "
            f"${_row(point)}$ in the direction $u = {_row(direction)}$.",
            sp.simplify(grad.dot(direction) / norm))


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
    "derivative_from_definition": t_derivative_from_definition,
    "chain_rule": t_chain_rule,
    "chain_rule_multivar": t_chain_rule_multivar,
    "implicit_dydx": t_implicit_dydx,
    "directional_derivative": t_directional_derivative,
    "limit_substitution": t_limit_substitution,
    "limit_informal": t_limit_informal,
    "limit_indeterminate": t_limit_indeterminate,
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
        if task in ("differentiate", "chain_rule") and answer.is_number:
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


def _fmt_answer(ans):
    """Same tuple form for a vector answer, so the question and the answer look alike."""
    if isinstance(ans, sp.MatrixBase):
        return "(" + ", ".join(sp.latex(x) for x in ans) + ")"
    return sp.latex(ans)


# Tasks whose answer should be presented in SIMPLEST form. Excluded deliberately: factor, expand,
# simplify, evaluate and solve, where the task itself defines the target form and simplifying would
# undo the exercise (simplify of a factored quadratic expands it straight back).
_SIMPLIFY_ANSWER = {
    "differentiate", "derivative_at", "partial", "gradient", "nth_derivative",
    "chain_rule", "implicit_dydx", "chain_rule_multivar", "directional_derivative",
    "derivative_from_definition", "limit", "compose", "local_min_x", "critical_points",
    "gd_step", "dot_product", "scalar_multiple", "vector_magnitude", "vector_update",
}


def _simplified(task, answer):
    """A derivative left as sympy emits it is not the answer a student writes.

    The quotient rule drill for (2x+1)/(x-3) stored 2/(x-3) - (2x+1)/(x-3)**2, while the diagnosis
    written against the same problem says "-5 instead of -7". The Expected line would have
    contradicted the explanation directly under it.
    """
    if task not in _SIMPLIFY_ANSWER:
        return answer
    try:
        import sympy as _sp
        if isinstance(answer, _sp.MatrixBase):
            return answer.applyfunc(_sp.simplify)
        simp = _sp.simplify(answer)
        return simp if _sp.count_ops(simp) <= _sp.count_ops(answer) else answer
    except Exception:                                    # noqa: BLE001
        return answer


def build(task, params):
    """Returns (stem_latex, answer_latex, answer_sympy) or raises."""
    if task not in TASKS:
        raise ValueError(f"unknown task {task}")
    stem, answer = TASKS[task](params)
    answer = _simplified(task, answer)
    ok, why = _nontrivial(task, params, answer)
    if not ok:
        raise ValueError(why)
    answer_latex = _fmt_answer(answer)
    # A drill the app cannot draw is not a drill. Refusing here, rather than checking the bank
    # afterwards, is what stopped `\begin{matrix}` reaching a card a second time.
    bad = sorted(set(unsupported_commands(stem)) | set(unsupported_commands(answer_latex)))
    if bad:
        raise ValueError("latex the app cannot render: " + ", ".join(bad))
    return stem, answer_latex, answer


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
