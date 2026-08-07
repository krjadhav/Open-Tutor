#!/usr/bin/env python3
"""Generate a printable worksheet of the 12 diagnosis cases to be copied out by hand.

The point of reusing the exact same cases as experiment 2 is that the clean-text baseline
(83% on error cases) is already known. Anything the end-to-end run loses is attributable to
OCR, not to the diagnosis model. That is the only way to get a number that means something.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = json.loads((HERE.parent / "exp2_diagnosis" / "cases.json").read_text())["cases"]

CSS = """
body { font-family: Georgia, serif; max-width: 800px; margin: 24px auto; padding: 0 16px; color: #111; }
h1 { font-size: 22px; margin-bottom: 4px; }
.sub { color: #555; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
.case { border: 1px solid #bbb; border-radius: 6px; padding: 12px 16px; margin-bottom: 18px; page-break-inside: avoid; }
.id { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #a00; font-weight: bold; }
.problem { font-size: 15px; margin: 6px 0 10px; }
.label { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #666; margin-bottom: 4px; }
pre { font-family: ui-monospace, Menlo, monospace; font-size: 14px; background: #f6f6f4;
      border-left: 3px solid #999; padding: 10px 12px; margin: 0; white-space: pre-wrap; line-height: 1.6; }
.note { font-size: 12px; color: #666; margin-top: 8px; font-style: italic; }
@media print { body { margin: 0; } .case { border-color: #999; } }
"""

INSTRUCTIONS = """
<b>How to run this.</b> Copy each block below onto paper <b>in your own handwriting</b>, exactly as
written, mistakes included. The errors are deliberate and are the whole point. Write naturally:
do not print neatly, do not use a ruler. Messy is the realistic case and messy is what we need to
measure.
<br><br>
Photograph each solution as its own image and name the file with the case id: <code>c01.jpg</code>,
<code>c02.jpg</code> and so on, all in one folder. Phone camera is correct, that is the product.
Ordinary lighting and a slight angle are fine and are more representative than a flat scan.
<br><br>
Short on time? Do a subset. The runner handles whatever files exist. Prioritise
<code>c01</code>, <code>c05</code>, <code>c10</code> (the prerequisite-blame cases, which are the
product thesis) and <code>c02</code>, <code>c07</code> (topic-blame). Five images is enough for a
usable signal.
<br><br>
Write only the working. Do not write the case id or the problem statement on the page.
"""


def main():
    parts = [f"<style>{CSS}</style>", "<h1>Open Tutor: handwriting test sheet</h1>",
             f'<div class="sub">{INSTRUCTIONS}</div>']
    for c in CASES:
        parts.append(f"""
<div class="case">
  <div class="id">{c['id']}  &rarr;  save your photo as {c['id'].split('-')[0]}.jpg</div>
  <div class="problem"><b>Problem:</b> {c['problem']}</div>
  <div class="label">Copy this out by hand, exactly as shown</div>
  <pre>{c['student_work']}</pre>
</div>""")
    out = HERE / "worksheet.html"
    out.write_text("\n".join(parts))
    print(f"Wrote {out}")
    print(f"{len(CASES)} cases. Open it in a browser and print, or just copy from the screen.")


if __name__ == "__main__":
    main()
