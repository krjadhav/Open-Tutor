#!/usr/bin/env python3
"""Bundle the app into ONE self-contained HTML file for a Claude Artifact.

An Artifact runs under a strict CSP: every request to any host is blocked, including the same
origin, so `/api/...` cannot work. The frontend already ships a mock path for exactly that case
(the offline fallback), so the bundle forces it on and the result is a complete, clickable
walkthrough of all twelve screens with sample data and no engine behind it.

Everything is inlined, fonts as data URIs, because the CSP blocks font CDNs too and a silent
fallback would be worse than no font at all.
"""

import base64, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
S = ROOT / "app" / "static"
OUT = ROOT / "docs" / "artifact" / "open-tutor-ui.html"


def data_uri(path: pathlib.Path) -> str:
    return "data:font/woff2;base64," + base64.b64encode(path.read_bytes()).decode()


def inline_fonts(css: str, base: pathlib.Path) -> str:
    def sub(m):
        rel = m.group(1)
        f = (base / rel).resolve()
        if not f.exists():
            print(f"  MISSING FONT {rel}", file=sys.stderr)
            return m.group(0)
        return f"url({data_uri(f)})"
    return re.sub(r"url\(([^)]+\.woff2)\)", sub, css)


def main() -> None:
    html = (S / "index.html").read_text()
    katex_css = inline_fonts((S / "vendor/katex/katex.min.css").read_text(), S / "vendor/katex")
    disp_css = inline_fonts((S / "vendor/display-fonts.css").read_text(), S / "vendor")
    app_css = (S / "styles.css").read_text()
    katex_js = (S / "vendor/katex/katex.min.js").read_text()
    app_js = (S / "app.js").read_text()

    # Force the mock. The app reads ?mock=1; setting it before app.js parses is the least invasive
    # way in, and it keeps the bundle honest: it runs the SAME code path the real app runs offline,
    # not a special build that could drift from what ships.
    force = ("<script>(function(){var u=new URL(location.href);"
             "if(!u.searchParams.has('mock')){u.searchParams.set('mock','1');"
             "history.replaceState(null,'',u.toString());}})();</script>\n")

    head_block = (f"<style>\n{disp_css}\n</style>\n"
                  f"<style>\n{katex_css}\n</style>\n"
                  f"<style>\n{app_css}\n</style>\n")
    html = re.sub(r'\s*<link rel="stylesheet" href="[^"]+">', "", html)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S, count=1)
    html = html.replace("</head>", head_block + "</head>")
    html = re.sub(r'\s*<script\b[^>]*\bsrc="[^"]+"[^>]*>\s*</script>', "", html)
    html = html.replace("</body>", f"{force}<script>\n{katex_js}\n</script>\n"
                                   f"<script>\n{app_js}\n</script>\n</body>")

    # The Artifact host wraps the file in its own doctype/html/head/body skeleton, so emit CONTENT
    # only. Keeping our own document tags would nest a second document inside theirs.
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S).group(1)
    head_styles = "".join(re.findall(r"<style>.*?</style>", html, re.S))
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    content = (f"<title>{title}</title>\n{head_styles}\n{body}\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content)
    html = content
    left = re.findall(r'(?:src|href)="(?!data:|#)([^"]+)"', html)
    print(f"wrote {OUT}  ({len(html)/1024/1024:.2f} MB)")
    print(f"remaining external references: {left or 'none'}")
    print(f"contains katex: {'katex' in html}  forced mock: {'mock' in force}")


if __name__ == "__main__":
    main()
