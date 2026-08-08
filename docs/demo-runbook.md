# Demo runbook

Everything below runs with **zero Sarvam API calls**. The diagnosis and the photo transcription are
both served from `data/demo/diagnosis_cache.json`, because section 16 measured that `seed` does not
pin model output and one live run in 23 drifted to a different node. Nothing on stage rides a coin
flip.

---

## Start it

```bash
cd /Users/avinash/Projects/Hackathons/SarvamAug2026/Open-Tutor
./run.sh
```

Then open **http://localhost:8000**.

Do NOT export `SARVAM_API_KEY` for the demo. Without it the app is fully cached and deterministic.
With it, a cache miss silently becomes a live call, which is the one thing we do not want on stage.

**View it as a phone.** The layout is a fixed 390x844 frame, centred on desktop. In Chrome, open
devtools, toggle device toolbar (cmd+shift+M) and pick iPhone 14 Pro. That is what a judge should
see over your shoulder.

**On a real phone**, for the camera. `run.sh` already binds `0.0.0.0`, so put the laptop and the
phone on the same wifi and open `http://<laptop-ip>:8000`:

```bash
ipconfig getifaddr en0        # wifi address to type into the phone
```

Plain HTTP is deliberate and sufficient: the camera is the phone's own, reached through
`capture="environment"` on a file input, which needs no permission prompt and no secure context.
`getUserMedia` would need HTTPS and buy nothing here.

**If the phone shows an older build**, it is holding a cached `app.js`. The server now sends
`Cache-Control: no-store` on the shell, so this should not recur, but a copy cached before that
shipped will survive until the tab is closed. Close the tab and reopen it; a pull-to-refresh is not
always enough.

---

## The 90 second path

1. **Onboarding.** "What do you want to understand?", prefilled `How neural networks learn`. Tap the button.
2. **Today.** Six problems, each with its reason chip: `Blocker · fractions`, `Blocker · signs`, `On your path`, `Review`, `New · Vector notation`, `Review`. Point out that the app is telling the student *why* each problem is there.
3. **Path tab.** Gradient descent pinned at the top, `18 skills away`, `about 16 days at your pace`. Expand a stage. Note the four node shapes and the `needs:` chips, all generated from the graph.
4. **Blockers tab.** Two open misconceptions, each showing the student's own wrong line struck through above the corrected one. This is the tab to linger on.
5. **Back to Today, tap the quotient rule problem**, the fourth one, `Review`. Camera button.
6. **Crop.** "Keep just this working." The box starts at 80% of the photo; drag it onto the one problem. The readout under it shows what the upload actually costs, `Original: 227 KB -> Will upload: 19 KB`. Worth one sentence: one problem per image is why the transcription is reliable, and the saving is a side effect.
7. **Check.** "Here's what we read." Confirm.
8. **Result.** Verdict lands instantly from the CAS. Just under a second later the diagnosis arrives:
   > **Your quotient rule is correct.** You applied the quotient rule correctly, but the second term is subtracted, so -(3x)(2) is -6x, not +6x. That is why 6x - 15 - 6x collapses to -15.

   Line 2 of the working highlights. This is the moment the product exists for.

   The engine serves `gen-der.quotient-rule-5` here, not `-1`. They are the same misconception on
   different numbers, and the wording above is `-5`'s, read out of the cache. If you rehearse
   against `-1`'s wording you will quote a sentence the judge is not looking at.
9. **Language toggle in the You tab.** Same screens in Hindi, mathematics unchanged:
   > आपका भागफल नियम सही है।
10. **Complete.** Spine rises, nodes light, tomorrow's focus named.

---

## Verify it end to end without clicking

```bash
python3 - <<'EOF'
import json, urllib.request
B = "http://127.0.0.1:8000"
def post(p, b):
    r = urllib.request.Request(B+p, data=json.dumps(b).encode(),
                               headers={"content-type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())
WORK = ["f'(x) = [(2)(x-3) - (2x+1)(1)] / (x-3)^2",
        "= [2x - 6 - 2x + 1] / (x-3)^2", "= -5 / (x-3)^2"]
g = post("/api/solve/grade", {"item_id": "gen-der.quotient-rule-1",
        "typed_answer": "-5/(x-3)**2", "hint_level": 0,
        "channel": "photo", "work_lines": WORK})
d = post("/api/solve/diagnose", {"attempt_id": g["attempt_id"],
        "item_id": "gen-der.quotient-rule-1", "work_text": "\n".join(WORK)})
print(g["verdict"], "|", d["blamed_node"], "| cache:", d["from_cache"], "| line:", d["failed_line_index"])
EOF
```

Expected: `Not quite | alg.sign-distribution | cache: True | line: 1`.
If `cache` is False the demo is about to make a live call. Stop and check the cache file.

## Reset between runs

```bash
curl -s -X POST http://127.0.0.1:8000/api/reset > /dev/null
```
Restores the seeded 5-day history exactly. Do this before every rehearsal, and once immediately
before you present.

## Full test suite

```bash
python3 -m pytest -q          # 817 passed, 1 skipped
```
The 1 skip is a live API test that only runs with a key set. That is correct: it should skip.

---

## What is real and what is cached, stated plainly

**Real, computed live:** the knowledge graph and all 37 nodes, the mastery engine and its four
update rules, the daily set composition, difficulty targeting, XP, the CAS grader, blame
propagation and every state change you see on the Path.

**Served from cache:** the diagnosis sentence for the demo problem, and the OCR transcription.
Both are real outputs, measured and recorded, not invented. A live call is the fallback, not the
default.

**Seeded:** the 5 day history, which is 244 real attempts replayed through the engine. The state
you see is earned by replay, not written into a file.

If a judge asks whether the AI is grading: it is not. A computer algebra system decides
correctness; the model only explains why an answer was wrong, and is never asked about a correct
one.

---

## Known rough edges

- **Committing the blame locks the quotient rule.** Blaming sign distribution cascades down, so the
  student is locked out of the problem they were just working on. The engine is behaving correctly.
  Decide whether you want that beat on stage.
- **XP shows 0 on an incorrect attempt**, which is correct by design (incorrect earns nothing) but
  makes the Complete screen read thin if the only attempt was wrong. Answer one problem correctly
  first if you want a non-zero number.
- **Hint prose is English on 34 of 37 nodes.** The three the demo touches are fully Hindi.
- **Streak is derived from the seeded history**, so it reads 5, not 12 as in the mock.
