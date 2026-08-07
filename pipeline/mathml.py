"""Presentation MathML to LaTeX.

OpenStax ships Presentation MathML with a Content-MathML annotation and no TeX annotation, so
there is nothing to just read off. Stripping tags naively destroys exactly the structure that
matters: `x^3 - 7x^2` collapses to `x 3 - 7 x 2`, and fractions lose their numerator/denominator
split entirely. Since superscripts and fractions are most of what a calculus exercise *is*, and
since a mangled stem would corrupt both the CAS answer check and the diagnosis, this is worth
doing properly rather than approximating.

Covers the subset OpenStax actually uses in calculus exercises.
"""

import re
import xml.etree.ElementTree as ET

# a handful of operators that need escaping or renaming in LaTeX
OPS = {
    "−": "-", "–": "-", "′": "'", "″": "''",
    "×": r"\times ", "÷": r"\div ", "⋅": r"\cdot ",
    "≤": r"\leq ", "≥": r"\geq ", "≠": r"\neq ",
    "→": r"\to ", "∞": r"\infty ", "∫": r"\int ",
    "∑": r"\sum ", "∏": r"\prod ", "∂": r"\partial ",
    "√": r"\sqrt ", "π": r"\pi ", "θ": r"\theta ",
    "α": r"\alpha ", "β": r"\beta ", "Δ": r"\Delta ",
    "⁡": "", " ": " ",
}

FUNCS = {"sin", "cos", "tan", "sec", "csc", "cot", "ln", "log", "exp",
         "lim", "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan"}


def _txt(el):
    return (el.text or "").strip()


def _clean(s):
    for k, v in OPS.items():
        s = s.replace(k, v)
    return s


def _kids(el):
    return [c for c in el if _tag(c) not in ("annotation", "annotation-xml")]


def _tag(el):
    return el.tag.split("}")[-1]


def conv(el):
    t = _tag(el)
    ch = _kids(el)

    if t in ("math", "semantics", "mstyle", "mpadded", "mphantom"):
        return "".join(conv(c) for c in ch)
    if t == "mrow":
        return "".join(conv(c) for c in ch)
    if t == "mi":
        s = _clean(_txt(el))
        return f"\\{s} " if s in FUNCS else s
    if t == "mn":
        return _clean(_txt(el))
    if t == "mo":
        s = _clean(_txt(el))
        return s
    if t == "mtext":
        s = _clean(_txt(el))
        return f"\\text{{{s}}}" if s.strip() else " "
    if t == "mspace":
        return " "
    if t == "msup":
        return f"{_wrap(ch[0])}^{{{conv(ch[1])}}}" if len(ch) == 2 else "".join(conv(c) for c in ch)
    if t == "msub":
        return f"{_wrap(ch[0])}_{{{conv(ch[1])}}}" if len(ch) == 2 else "".join(conv(c) for c in ch)
    if t == "msubsup":
        return (f"{_wrap(ch[0])}_{{{conv(ch[1])}}}^{{{conv(ch[2])}}}"
                if len(ch) == 3 else "".join(conv(c) for c in ch))
    if t == "mfrac":
        return f"\\frac{{{conv(ch[0])}}}{{{conv(ch[1])}}}" if len(ch) == 2 else ""
    if t == "msqrt":
        return f"\\sqrt{{{''.join(conv(c) for c in ch)}}}"
    if t == "mroot":
        return f"\\sqrt[{conv(ch[1])}]{{{conv(ch[0])}}}" if len(ch) == 2 else ""
    if t in ("munder", "mover", "munderover"):
        # lim_{x \to 0} and friends
        base = conv(ch[0])
        if len(ch) >= 2:
            sub = conv(ch[1])
            if base.strip() in (r"\lim", r"\lim "):
                return f"\\lim_{{{sub}}}"
            return f"{base}_{{{sub}}}" if t == "munder" else f"{base}^{{{sub}}}"
        return base
    if t == "mfenced":
        inner = ",".join(conv(c) for c in ch)
        return f"({inner})"
    if t in ("mtable", "mtr", "mtd"):
        return " ".join(conv(c) for c in ch)
    return "".join(conv(c) for c in ch)


def _wrap(el):
    """Base of a super/subscript needs braces when it is more than one token."""
    s = conv(el)
    return s if len(s) <= 1 or (s.startswith("{") and s.endswith("}")) else (
        s if re.fullmatch(r"[A-Za-z0-9]", s) else f"{{{s}}}")


def mathml_to_latex(fragment):
    """Convert one <math>...</math> string. Returns None if it will not parse."""
    frag = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", fragment)
    frag = re.sub(r"<annotation-xml.*?</annotation-xml>", "", frag, flags=re.S)
    frag = re.sub(r"<annotation.*?</annotation>", "", frag, flags=re.S)
    try:
        root = ET.fromstring(frag)
    except ET.ParseError:
        return None
    out = conv(root)
    out = re.sub(r"\s+", " ", out).strip()
    return out or None


def html_to_text(fragment):
    """Strip an OpenStax problem block to plain text, with maths rendered as inline LaTeX."""
    import html as _html

    def repl(m):
        tex = mathml_to_latex(m.group(0))
        return f" ${tex}$ " if tex else " "

    s = re.sub(r"<math.*?</math>", repl, fragment, flags=re.S)
    s = re.sub(r"<(script|style).*?</\1>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\$\s*\$", "", s)
    return s.strip()
