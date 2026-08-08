/* ==========================================================================
   Open Tutor: frontend
   Plain HTML, CSS and vanilla JS. No build step, no framework.
   Served as static files by the FastAPI app. Every field this file reads is
   defined in docs/api-contract.md; nothing here invents a field.
   ========================================================================== */

'use strict';

/* --------------------------------------------------------------------------
   1. Configuration
   DATA_SOURCE is the single switch. LIVE hits the API and falls back to the
   mock only when a call fails, so the demo renders even if the server is down.
   Force the mock with ?mock=1 in the URL. ?fast=1 marks the first four
   problems done so the Complete screen is two taps away.
   -------------------------------------------------------------------------- */

var QS = new URLSearchParams(location.search);

var CONFIG = {
  DATA_SOURCE: QS.get('mock') === '1' ? 'MOCK' : 'LIVE',   // 'LIVE' | 'MOCK'
  BASE: '/api',
  FAST_DEMO: QS.get('fast') === '1',
  DIAGNOSIS_MIN_WAIT_MS: 700   // the blinking indicator must be seen, not flash
};

/* --------------------------------------------------------------------------
   2. Chrome strings
   Content strings (chips, skill names, verdicts, the diagnosis, `needs:`,
   pace lines) always come from the server, already translated. Only the fixed
   furniture below lives here, because the contract exposes no endpoint for it.
   If GET /api/state ever returns a `ui` object it wins: see applyServerUI.
   -------------------------------------------------------------------------- */

var CHROME = {
  en: {
    signupQ: 'What should we call you?',
    namePlaceholder: 'Your name',
    signupNote: 'No account is created; this is a placeholder.',
    continueWord: 'Continue',
    coursesEyebrow: 'Courses', coursesHead: 'Choose where to begin',
    coursesNote: 'One course is ready; the rest are coming.',
    endsAt: 'Ends at', skillsWord: 'skills', comingSoon: 'Coming soon',
    today: 'Today', start: 'Start session',
    blockers: 'Blockers', fix: 'Fix this →',
    you: 'You', mastered: 'skills mastered', accuracy: 'accuracy this week',
    language: 'Language', replay: 'Replay onboarding', signOut: 'Sign out',
    answerPlaceholder: 'Type your answer',
    photo: 'Photograph your working',
    check: 'Check answer',
    hints: 'Hints', hintNext: 'Show next hint', hintTwin: 'Try the twin problem',
    checkHead: 'Here’s what we read.', checkSub: 'Tap any line to correct it.',
    edit: 'edit', confirm: 'Looks right',
    expected: 'Expected', reading: 'Reading your working…',
    ask: 'Was this your mistake?', yes: 'Yes', no: 'No',
    sessionDone: 'Session complete', done: 'Done',
    next: 'Next problem', finish: 'Finish session',
    stepSolve: 'Solve', stepCheck: 'Check', stepResult: 'Result',
    tabToday: 'Today', tabPath: 'Path', tabBlockers: 'Blockers', tabYou: 'You',
    loading: 'Loading',
    offline: 'Server not responding. Showing demo data.',
    ocrEmpty: 'We could not read that photo. Type your answer instead.',
    ocrFailed: 'The photo did not go through. Type your answer instead.',
    diagFailed: 'We could not read the working this time.',
    unsimplified: 'Correct, but it can be simplified further.',
    xp: 'XP', unlockedWord: 'unlocked',
    setDone: 'Today’s set is finished.',
    blockersHead: function (n) {
      if (n === 0) { return 'Nothing is blocking you right now'; }
      if (n === 1) { return 'One thing keeps tripping you up'; }
      if (n === 2) { return 'Two things keep tripping you up'; }
      return n + ' things keep tripping you up';
    }
  },
  hi: {
    signupQ: 'हम आपको क्या कहें?',
    namePlaceholder: 'आपका नाम',
    signupNote: 'कोई खाता नहीं बनता; यह सिर्फ़ एक प्लेसहोल्डर है।',
    continueWord: 'आगे बढ़ें',
    // "पाठ्यक्रम" rather than "कोर्स", to match the word the server's own
    // Hindi uses in the headline just below it.
    coursesEyebrow: 'पाठ्यक्रम', coursesHead: 'कहाँ से शुरू करें',
    coursesNote: 'एक कोर्स तैयार है; बाकी जल्द आ रहे हैं।',
    endsAt: 'अंत में', skillsWord: 'कौशल', comingSoon: 'जल्द आ रहा है',
    today: 'आज', start: 'सत्र शुरू करें',
    blockers: 'रुकावटें', fix: 'इसे ठीक करें →',
    you: 'आप', mastered: 'कौशल में महारत', accuracy: 'इस हफ़्ते सटीकता',
    language: 'भाषा', replay: 'शुरुआत फिर देखें', signOut: 'साइन आउट',
    answerPlaceholder: 'अपना उत्तर लिखें',
    photo: 'अपना काम फ़ोटो करें',
    check: 'उत्तर जाँचें',
    hints: 'संकेत', hintNext: 'अगला संकेत दिखाएँ', hintTwin: 'जुड़वाँ प्रश्न आज़माएँ',
    checkHead: 'हमने यह पढ़ा।', checkSub: 'सुधारने के लिए किसी पंक्ति पर टैप करें।',
    edit: 'बदलें', confirm: 'सही है',
    expected: 'अपेक्षित', reading: 'आपका काम पढ़ा जा रहा है…',
    ask: 'क्या यही आपकी ग़लती थी?', yes: 'हाँ', no: 'नहीं',
    sessionDone: 'सत्र पूरा', done: 'हो गया',
    next: 'अगला प्रश्न', finish: 'सत्र समाप्त करें',
    stepSolve: 'हल', stepCheck: 'जाँच', stepResult: 'नतीजा',
    tabToday: 'आज', tabPath: 'रास्ता', tabBlockers: 'रुकावटें', tabYou: 'आप',
    loading: 'लोड हो रहा है',
    offline: 'सर्वर नहीं मिला। डेमो डेटा दिख रहा है।',
    ocrEmpty: 'यह फ़ोटो पढ़ा नहीं जा सका। उत्तर लिखकर भेजें।',
    ocrFailed: 'फ़ोटो नहीं भेजा जा सका। उत्तर लिखकर भेजें।',
    diagFailed: 'इस बार आपका काम पढ़ा नहीं जा सका।',
    unsimplified: 'सही है, पर इसे और सरल किया जा सकता है।',
    xp: 'XP', unlockedWord: 'खुले',
    setDone: 'आज का सेट पूरा हो गया।',
    blockersHead: function (n) {
      if (n === 0) { return 'अभी कोई रुकावट नहीं है'; }
      if (n === 1) { return 'एक चीज़ बार-बार अटका रही है'; }
      if (n === 2) { return 'दो चीज़ें बार-बार अटका रही हैं'; }
      return n + ' चीज़ें बार-बार अटका रही हैं';
    }
  }
};

function T() { return CHROME[state.lang] || CHROME.en; }

/* The server's own i18n keys are `<group>.<name>`; this table is flat camelCase.
   Every pair below is a genuine counterpart, so the server's string wins and the
   local one is only ever the fallback for an unreachable API. The exceptions,
   and the reason for each, are listed under `LOCAL_ONLY_CHROME` below. */
var SERVER_UI_ALIASES = {
  // The two screens before the tabs
  'signup.title': 'signupQ',
  'signup.subtitle': 'signupNote',
  // `signup.name_label` and not `signup.name_placeholder`: the field has no
  // visible label, so the placeholder is the only thing naming it, and
  // "Optional" would leave the student guessing what to type.
  'signup.name_label': 'namePlaceholder',
  'action.continue': 'continueWord',
  'action.sign_out': 'signOut',
  'screen.courses': 'coursesHead',
  'courses.subtitle': 'coursesNote',
  'course.ends_at': 'endsAt',
  'course.coming_soon': 'comingSoon',

  // Tabs, and the eyebrow that names each screen. A server key may feed more
  // than one local key: the Blockers and You eyebrows repeat their tab's word,
  // and the server has only the one word for each.
  'tab.today': 'tabToday',
  'tab.path': 'tabPath',
  'tab.blockers': ['tabBlockers', 'blockers'],
  'tab.you': ['tabYou', 'you'],
  'screen.todays_set': 'today',        // the eyebrow above Today's headline

  // Today
  'action.start_set': 'start',
  'session.finished': 'setDone',

  // Solve
  'action.take_photo': 'photo',
  'action.check_answer': 'check',
  'action.show_hint': 'hintNext',

  // Check
  'confirm.ocr': 'checkHead',

  // Result
  'confirm.diagnosis': 'ask',
  'common.yes': 'yes',
  'common.no': 'no',
  'action.next_problem': 'next',

  // Complete
  'screen.session_complete': 'sessionDone',
  'progress.xp_earned': 'xp',
  'progress.unlocked': 'unlockedWord',

  // The step indicator, which names the three screens of the solve flow
  'screen.solve': 'stepSolve',
  'screen.check': 'stepCheck',
  'screen.result': 'stepResult',

  'common.loading': 'loading'
};

/* Every CHROME key the server does NOT own, and why. This list is the true set
   of strings the frontend still has to translate itself. Kept as data so the
   dev-time check below can tell "no server key exists" apart from "a server key
   exists and nobody wired it up", which is the failure worth catching. */
var LOCAL_ONLY_CHROME = {
  // No server key exists for these.
  coursesEyebrow: 'no server key', skillsWord: 'no server key: server sends skills_line whole',
  fix: 'no server key', mastered: 'no server key', accuracy: 'no server key',
  language: 'no server key', replay: 'no server key',
  hints: 'no server key', hintTwin: 'no server key, and the twin is not wired',
  checkSub: 'no server key', edit: 'no server key', confirm: 'no server key',
  expected: 'no server key', reading: 'no server key', done: 'no server key',
  finish: 'no server key', unsimplified: 'no server key',
  blockersHead: 'a function of the blocker count, not a string',

  // Deliberately local even though a loosely related server key exists.
  answerPlaceholder: 'action.type_answer is the photo-failure button, not this placeholder',
  ocrEmpty: 'no server key; error.generic is too generic to explain a failed photo',
  ocrFailed: 'no server key; action.retake_photo is a button, not this message',
  diagFailed: 'no server key; error.generic is too generic to explain a failed diagnosis',
  offline: 'must not depend on the server: it is the message shown when the server is gone'
};

/* The server owns the chrome whenever it sends any: `ui` on /api/state wins over
   the table above, which is what keeps `data/i18n/hi.json` the single place a
   Hindi string is fixed. CHROME is the fallback for an unreachable API. */
function applyServerUI(payload) {
  if (!payload || !payload.ui) { return; }
  var src = payload.ui, target = CHROME[state.lang];
  var unresolved = [];

  Object.keys(src).forEach(function (k) {
    if (typeof src[k] !== 'string') { return; }
    var local = SERVER_UI_ALIASES[k] || k;
    var names = (typeof local === 'string') ? [local] : local;
    var landed = false;
    names.forEach(function (name) {
      // Only a string already in the table can be replaced: that keeps the
      // server's dotted keys out of it and keeps `blockersHead`, which is a
      // function, from being overwritten with a string.
      if (typeof target[name] === 'string') { target[name] = src[k]; landed = true; }
    });
    if (!landed) { unresolved.push(k); }
  });

  reportUnresolvedUI(unresolved, Object.keys(src).length);
}

/* Any server string the frontend renders nowhere. The list is expected to be
   non-empty: the server uses one i18n file for chrome AND for the words it
   composes payloads from, so `stage.*`, `slot.*`, `node.*`, `verdict.*` and
   friends arrive already baked into `/api/state` and have no business in this
   table. What this catches is the other kind: a string somebody translated on
   the server, expected to see on screen, and which silently landed nowhere. */
function reportUnresolvedUI(unresolved, total) {
  if (state.uiReported || !unresolved.length) { return; }
  state.uiReported = true;
  console.info('[open-tutor] server ui: ' + (total - unresolved.length) + ' of ' + total +
    ' keys render as chrome. Not rendered: ' + unresolved.sort().join(', '));
  var localOnly = Object.keys(LOCAL_ONLY_CHROME);
  console.info('[open-tutor] chrome the server does not own (' + localOnly.length + '): ' +
    localOnly.map(function (k) { return k + ' (' + LOCAL_ONLY_CHROME[k] + ')'; }).join('; '));
}

/* --------------------------------------------------------------------------
   3. Mathematics rendering, by KaTeX
   KaTeX is vendored under /vendor/katex and loaded by index.html ahead of this
   file, so window.katex is already defined by the time anything renders. It
   replaces a hand-rolled LaTeX pass that knew about twenty commands and printed
   the rest as literal words: `\mathbf{u}` drew "mathbfu" and a sympy matrix drew
   "beginmatrix ... endmatrix", and both of those shipped as visible bugs.

   `throwOnError: false` means a stem KaTeX cannot parse is drawn in its error
   colour rather than taking the screen down with it. If KaTeX itself is missing
   we degrade to readable plain text AND say so in the console, because a silent
   fallback is how a broken build reaches a demo.
   -------------------------------------------------------------------------- */

function escapeHTML(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

var mathFallbackReported = false;

function reportMathFallback(why) {
  if (mathFallbackReported) { return; }
  mathFallbackReported = true;
  console.error('[open-tutor] KaTeX is not rendering (' + why + '). ' +
    'Mathematics is falling back to plain text; check /vendor/katex/katex.min.js.');
}

/* Readable text for one TeX string when there is no KaTeX to draw it. Not a
   renderer and not trying to be one: it strips the syntax so the symbols and
   numbers survive, which is enough to keep a screen usable. */
function plainMath(raw) {
  var s = String(raw === null || raw === undefined ? '' : raw);
  s = s.replace(/\\(?:left|right|!|,|;|:|quad|qquad)/g, '');
  s = s.replace(/\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '($1)/($2)');
  s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, '√($1)');
  s = s.replace(/\\(?:text|mathbf|mathrm|operatorname)\s*\{([^{}]*)\}/g, '$1');
  s = s.replace(/\\([a-zA-Z]+)/g, '$1');
  s = s.replace(/[{}]/g, '');
  return s;
}

/* One TeX string, as an HTML fragment. */
function texHTML(raw) {
  var src = String(raw === null || raw === undefined ? '' : raw);
  if (!src) { return ''; }
  if (!window.katex) {
    reportMathFallback('window.katex is undefined');
    return '<span class="math-fallback">' + escapeHTML(plainMath(src)) + '</span>';
  }
  try {
    return window.katex.renderToString(src, { throwOnError: false, displayMode: false });
  } catch (err) {
    // throwOnError:false already covers parse errors, so reaching here means
    // something worse. Say which string did it rather than blanking the node.
    console.error('[open-tutor] KaTeX failed on: ' + src, err);
    return '<span class="math-fallback">' + escapeHTML(plainMath(src)) + '</span>';
  }
}

/* $...$ spans become mathematics, everything between them stays words. */
function splitDollars(s) {
  var parts = s.split('$');
  var out = '';
  for (var i = 0; i < parts.length; i++) {
    if (!parts[i]) { continue; }
    out += (i % 2 === 1) ? texHTML(parts[i]) : escapeHTML(parts[i]);
  }
  return out;
}

/* A `math` field. A string with no $ is treated as all maths, which is how the
   contract's "Differentiate f(x) = ..." and every bare answer arrive. With $
   the words outside stay words: KaTeX would set "Expand and simplify" as a run
   of italic variables, which is not what that sentence is. */
function mixedHTML(raw) {
  var s = String(raw === null || raw === undefined ? '' : raw);
  if (s.indexOf('$') === -1) { return texHTML(s); }
  return splitDollars(s);
}

/* A prose field such as the diagnosis body. Plain text stays plain: only the
   $...$ spans are rendered as mathematics. */
function proseHTML(raw) {
  var s = String(raw === null || raw === undefined ? '' : raw);
  if (s.indexOf('$') === -1) { return escapeHTML(s); }
  return splitDollars(s);
}

/* --------------------------------------------------------------------------
   4. Mock data
   Shaped to docs/api-contract.md exactly, drawn from data/graph/nodes.json and
   data/demo/diagnosis_cache.json. Used only when a live call fails.
   -------------------------------------------------------------------------- */

function pick(en, hi) { return state.lang === 'hi' ? hi : en; }

/* The mock keeps its own session so the flow is walkable with the backend down:
   signing up moves it to `courses`, selecting a course to `ready`. */
var mockSession = { flow: 'signup', name: '', course: null };

function MOCK_STATE() {
  return {
    student_id: 'demo',
    lang: state.lang,
    flow: mockSession.flow,
    goal: {
      node_id: 'ai.gradient-descent-step',
      title: pick('Gradient descent', 'ग्रेडिएंट डिसेंट'),
      skills_away: 17,
      pace_line: pick('about 21 days at your pace', 'आपकी गति से लगभग 21 दिन')
    },
    today: {
      headline: pick('6 problems · 15 min', '6 प्रश्न · 15 मिनट'),
      problems: [
        { item_id: 'gen-alg.sign-distribution-1',
          math: 'Expand and simplify $5x - (3x - 2)$',
          chip: pick('Blocker · signs', 'रुकावट · चिह्न'),
          slot: 'blocker', is_blocker: true, done: false },
        { item_id: 'gen-alg.fraction-arithmetic-1',
          math: 'Simplify $2/(x+1) - 3/(x-2)$',
          chip: pick('Blocker · fractions', 'रुकावट · भिन्न'),
          slot: 'blocker', is_blocker: true, done: false },
        { item_id: 'gen-lim.indeterminate-factoring-1',
          math: 'Evaluate $\\lim_{x \\to 2} (x^2 - 4)/(x - 2)$',
          chip: pick('Review', 'दोहराव'),
          slot: 'review', is_blocker: false, done: false },
        { item_id: 'gen-der.power-rule-1',
          math: 'Differentiate $f(x) = x^{5} - 4x^{2}$',
          chip: pick('Review', 'दोहराव'),
          slot: 'review', is_blocker: false, done: false },
        { item_id: 'demo-quotient-sign',
          math: 'Differentiate $f(x) = (2x + 1)/(x - 3)$',
          chip: pick('New · Quotient rule', 'नया · भागफल नियम'),
          slot: 'new', is_blocker: false, done: false },
        { item_id: 'gen-opt.critical-points-1',
          math: 'Find the critical points of $f(x) = x^2 - 6x + 5$',
          chip: pick('On your path', 'आपके रास्ते पर'),
          slot: 'goal_link', is_blocker: false, done: false }
      ]
    },
    path: {
      stages: [
        { id: 'ai', name: pick('Gradient descent', 'ग्रेडिएंट डिसेंट'), count: '0/3', state: 'locked', skills: [
          { node_id: 'ai.gradient-descent-step', name: pick('The gradient descent update step', 'ग्रेडिएंट डिसेंट का अद्यतन चरण'), state: 'locked',
            needs: pick('needs: Gradient of a loss with respect to parameters', 'चाहिए: परामितियों के सापेक्ष हानि का ग्रेडिएंट') },
          { node_id: 'ai.gradient-of-loss', name: pick('Gradient of a loss with respect to parameters', 'परामितियों के सापेक्ष हानि का ग्रेडिएंट'), state: 'locked',
            needs: pick('needs: The gradient vector', 'चाहिए: ग्रेडिएंट सदिश') },
          { node_id: 'ai.loss-function', name: pick('Loss as a function of parameters', 'परामितियों के फलन के रूप में हानि'), state: 'locked',
            needs: pick('needs: Functions of several variables', 'चाहिए: कई चरों के फलन') }
        ]},
        { id: 'opt', name: pick('Optimisation', 'अनुकूलन'), count: '0/2', state: 'locked', skills: [
          { node_id: 'opt.local-extrema', name: pick('Classifying local minima and maxima', 'स्थानीय न्यूनतम और अधिकतम पहचानना'), state: 'locked',
            needs: pick('needs: Finding critical points', 'चाहिए: क्रांतिक बिंदु खोजना') },
          { node_id: 'opt.critical-points', name: pick('Finding critical points', 'क्रांतिक बिंदु खोजना'), state: 'locked',
            needs: pick('needs: Derivative as slope of the tangent line', 'चाहिए: स्पर्श रेखा की ढाल के रूप में अवकलज') }
        ]},
        { id: 'mv', name: pick('Multivariable', 'बहुचर'), count: '0/4', state: 'locked', skills: [
          { node_id: 'mv.directional-derivative', name: pick('Directional derivatives and steepest descent', 'दिशात्मक अवकलज और तीव्रतम ढलान'), state: 'locked',
            needs: pick('needs: The gradient vector', 'चाहिए: ग्रेडिएंट सदिश') },
          { node_id: 'mv.gradient', name: pick('The gradient vector', 'ग्रेडिएंट सदिश'), state: 'locked',
            needs: pick('needs: Partial derivatives', 'चाहिए: आंशिक अवकलज') },
          { node_id: 'mv.partial-derivative', name: pick('Partial derivatives', 'आंशिक अवकलज'), state: 'locked',
            needs: pick('needs: Chain rule', 'चाहिए: शृंखला नियम') },
          { node_id: 'mv.functions-several-vars', name: pick('Functions of several variables', 'कई चरों के फलन'), state: 'locked',
            needs: pick('needs: Recognizing and decomposing f(g(x))', 'चाहिए: f(g(x)) को पहचानना और खोलना') }
        ]},
        { id: 'der', name: pick('Derivative rules', 'अवकलन नियम'), count: '3/8', state: 'now', skills: [
          { node_id: 'der.quotient-rule', name: pick('Quotient rule', 'भागफल नियम'), state: 'now', needs: '' },
          { node_id: 'der.chain-rule', name: pick('Chain rule', 'शृंखला नियम'), state: 'locked',
            needs: pick('needs: Recognizing and decomposing f(g(x))', 'चाहिए: f(g(x)) को पहचानना और खोलना') },
          { node_id: 'der.exp-log-derivatives', name: pick('Derivatives of e^x and ln x', 'e^x और ln x के अवकलज'), state: 'locked',
            needs: pick('needs: Properties of exponentials and logarithms', 'चाहिए: घातांक और लघुगणक के गुण') },
          { node_id: 'der.higher-order', name: pick('Second and higher derivatives', 'द्वितीय और उच्च अवकलज'), state: 'learning', needs: '' },
          { node_id: 'der.slope-interpretation', name: pick('Derivative as slope of the tangent line', 'स्पर्श रेखा की ढाल के रूप में अवकलज'), state: 'learning', needs: '' },
          { node_id: 'der.definition', name: pick('The derivative as a limit of the difference quotient', 'अंतर भागफल की सीमा के रूप में अवकलज'), state: 'mastered', needs: '' },
          { node_id: 'der.constant-multiple-sum', name: pick('Constant multiple and sum rules', 'अचर गुणज और योग नियम'), state: 'mastered', needs: '' },
          { node_id: 'der.power-rule', name: pick('Power rule', 'घात नियम'), state: 'mastered', needs: '' }
        ]},
        { id: 'lim', name: pick('Limits', 'सीमाएँ'), count: '3/3', state: 'mastered', skills: [
          { node_id: 'lim.indeterminate-factoring', name: pick('Resolving 0/0 limits by factoring and cancelling', 'गुणनखंड से 0/0 सीमाएँ हल करना'), state: 'mastered', needs: '' },
          { node_id: 'lim.direct-substitution', name: pick('Evaluating limits by direct substitution', 'सीधे प्रतिस्थापन से सीमा निकालना'), state: 'mastered', needs: '' },
          { node_id: 'lim.concept', name: pick('Informal idea of a limit', 'सीमा की सामान्य समझ'), state: 'mastered', needs: '' }
        ]},
        { id: 'trig', name: pick('Trig values', 'त्रिकोणमितीय मान'), count: '1/1', state: 'mastered', skills: [
          { node_id: 'trig.unit-circle', name: pick('Exact values of sine and cosine at standard angles', 'मानक कोणों पर साइन और कोसाइन के सटीक मान'), state: 'mastered', needs: '' }
        ]},
        { id: 'alg', name: pick('Algebra moves', 'बीजगणित चालें'), count: '3/7', state: 'learning', skills: [
          { node_id: 'alg.sign-distribution', name: pick('Distributing a negative across a sum or difference', 'योग या अंतर पर ऋण चिह्न खोलना'), state: 'learning', needs: '' },
          { node_id: 'alg.fraction-arithmetic', name: pick('Adding and simplifying rational expressions', 'परिमेय व्यंजक जोड़ना और सरल करना'), state: 'learning', needs: '' },
          { node_id: 'alg.function-composition', name: pick('Recognizing and decomposing f(g(x))', 'f(g(x)) को पहचानना और खोलना'), state: 'learning', needs: '' },
          { node_id: 'alg.vectors', name: pick('Vector notation, scalar multiples and the dot product', 'सदिश संकेत, अदिश गुणज और डॉट गुणनफल'), state: 'learning', needs: '' },
          { node_id: 'alg.exponent-rules', name: pick('Laws of exponents, including negative and fractional', 'घातांक के नियम, ऋण और भिन्न समेत'), state: 'mastered', needs: '' },
          { node_id: 'alg.factoring', name: pick('Factoring polynomials', 'बहुपदों का गुणनखंडन'), state: 'mastered', needs: '' },
          { node_id: 'alg.solving-equations', name: pick('Solving linear and quadratic equations', 'रैखिक और वर्ग समीकरण हल करना'), state: 'mastered', needs: '' }
        ]}
      ],
      legend: [
        { state: 'mastered', label: pick('mastered', 'महारत') },
        { state: 'learning', label: pick('learning', 'सीख रहे') },
        { state: 'now', label: pick('now', 'अभी') },
        { state: 'locked', label: pick('locked', 'बंद') }
      ]
    },
    blockers: [
      { node_id: 'alg.sign-distribution',
        rank: pick('Blocker 1', 'रुकावट 1'),
        name: pick('Dropping negatives across brackets', 'कोष्ठक पर ऋण चिह्न गिरना'),
        freq: pick('4 times this week', 'इस हफ़्ते 4 बार'),
        wrong: '= [2x - 6 - 2x + 1] / (x-3)^2',
        right: '= [2x - 6 - 2x - 1] / (x-3)^2',
        item_count: 7 },
      { node_id: 'alg.fraction-arithmetic',
        rank: pick('Blocker 2', 'रुकावट 2'),
        name: pick('Subtracting fractions by subtracting denominators', 'हर घटाकर भिन्न घटाना'),
        freq: pick('twice', 'दो बार'),
        wrong: '3/4 - 1/6 = 2/2',
        right: '3/4 - 1/6 = 7/12',
        item_count: 4 }
    ],
    you: {
      streak_line: pick('12 day streak', '12 दिन लगातार'),
      mastered: 9,
      accuracy: '78%'
    }
  };
}

/* Shaped to the addendum, with the ids, titles and skill counts taken from
   data/courses.json. `selectable` is carried as its own field here for the same
   reason the server sends it: the frontend must never infer it from `state`. */
function MOCK_COURSES() {
  return {
    courses: [
      { id: 'calculus-for-ai',
        title: pick('Calculus for AI', 'AI के लिए कैलकुलस'),
        subtitle: pick('From algebra to gradient descent', 'बीजगणित से ग्रेडिएंट डिसेंट तक'),
        ends_at: pick('The gradient descent update step', 'ग्रेडिएंट डिसेंट का अद्यतन चरण'),
        state: 'active', skills: 37,
        detail: pick('about 18 days at 15 min a day', 'रोज़ 15 मिनट, लगभग 18 दिन'),
        selectable: true },
      { id: 'linear-algebra-for-ai',
        title: pick('Linear Algebra for AI', 'AI के लिए रैखिक बीजगणित'),
        subtitle: pick('Vectors to eigenvalues', 'सदिश से आइगेनमान तक'),
        ends_at: pick('Principal component analysis', 'मुख्य घटक विश्लेषण'),
        state: 'coming_soon', skills: 41,
        detail: pick('Coming soon', 'जल्द आ रहा है'), selectable: false },
      { id: 'jee-calculus',
        title: pick('JEE Mains Calculus', 'JEE मेन्स कैलकुलस'),
        subtitle: pick('The full calculus syllabus', 'पूरा कैलकुलस पाठ्यक्रम'),
        ends_at: pick('Definite integrals and areas', 'निश्चित समाकल और क्षेत्रफल'),
        state: 'coming_soon', skills: 63,
        detail: pick('Coming soon', 'जल्द आ रहा है'), selectable: false },
      { id: 'class-12-calculus',
        title: pick('Class 12 Calculus', 'कक्षा 12 कैलकुलस'),
        subtitle: pick('CBSE board, in your language', 'CBSE बोर्ड, आपकी भाषा में'),
        ends_at: pick('Applications of integrals', 'समाकलन के अनुप्रयोग'),
        state: 'coming_soon', skills: 58,
        detail: pick('Coming soon', 'जल्द आ रहा है'), selectable: false },
      { id: 'probability-for-ml',
        title: pick('Probability for ML', 'ML के लिए प्रायिकता'),
        subtitle: pick('Counting to Bayes', 'गिनती से बेज़ तक'),
        ends_at: pick('Maximum likelihood estimation', 'अधिकतम संभाव्यता आकलन'),
        state: 'coming_soon', skills: 34,
        detail: pick('Coming soon', 'जल्द आ रहा है'), selectable: false }
    ],
    selected: mockSession.course
  };
}

function mockReveal(itemId) {
  var it = (MOCK_STATE().today.problems || []).filter(function (p) { return p.item_id === itemId; })[0];
  return { correct: (it && it.answer) || '42', wrong: 'wrong', error: null };
}

function mockSignup(name) {
  mockSession.name = name || '';
  mockSession.flow = 'courses';
  return { student_id: 'demo', name: mockSession.name, next: 'courses' };
}

/* The endpoint refusing a coming_soon id is the backstop, not the gate, so the
   mock refuses too rather than quietly letting an unplayable course through. */
function mockSelectCourse(courseId) {
  var all = MOCK_COURSES().courses;
  var wanted = all.filter(function (c) { return c.id === courseId; })[0];
  if (!wanted || !wanted.selectable) {
    return { error: pick('not available yet', 'अभी उपलब्ध नहीं'), selected: mockSession.course };
  }
  mockSession.course = wanted.id;
  mockSession.flow = 'ready';
  return MOCK_STATE();
}

var MOCK_HINTS = {
  en: [
    { n: 1, text: 'A fraction of two functions. Which rule is that?', is_math: false },
    { n: 2, text: "(u/v)' = (u'v - uv') / v^2", is_math: true },
    { n: 3, text: "u = 2x + 1, v = x - 3. Write u'v - uv' before you simplify.", is_math: false },
    { n: 4, text: "Full solution: f'(x) = -7/(x-3)^2.", is_math: false }
  ],
  hi: [
    { n: 1, text: 'दो फलनों का भागफल। कौन-सा नियम लगेगा?', is_math: false },
    { n: 2, text: "(u/v)' = (u'v - uv') / v^2", is_math: true },
    { n: 3, text: "u = 2x + 1, v = x - 3. सरल करने से पहले u'v - uv' लिखें।", is_math: false },
    { n: 4, text: "पूरा हल: f'(x) = -7/(x-3)^2.", is_math: false }
  ]
};

function mockStart(itemId) {
  var problems = (state.data && state.data.today && state.data.today.problems) || MOCK_STATE().today.problems;
  var idx = 0;
  for (var i = 0; i < problems.length; i++) { if (problems[i].item_id === itemId) { idx = i; } }
  var p = problems[idx] || problems[0];
  return {
    item_id: p.item_id,
    index: idx + 1,
    total: problems.length,
    problem_label: pick('Problem ' + (idx + 1) + ' of ' + problems.length,
                        'प्रश्न ' + (idx + 1) + ' / ' + problems.length),
    math: p.math,
    hints: MOCK_HINTS[state.lang] || MOCK_HINTS.en
  };
}

var MOCK_WORK_LINES = [
  "f'(x) = [(2)(x-3) - (2x+1)(1)] / (x-3)^2",
  '= [2x - 6 - 2x + 1] / (x-3)^2',
  '= -5 / (x-3)^2'
];

/* The mock cannot run a CAS, so it grades by item id. The three item ids below
   are chosen to exercise all three Result states without a server. */
function mockGrade(payload) {
  var id = payload.item_id;
  if (id === 'demo-quotient-sign') {
    return { attempt_id: 'mock-a1', correct: false,
             verdict: pick('Incorrect', 'ग़लत'),
             expected_latex: '-7/(x-3)^{2}', unsimplified: false, needs_diagnosis: true };
  }
  if (id === 'gen-alg.fraction-arithmetic-1') {
    return { attempt_id: 'mock-a2', correct: true,
             verdict: pick('Correct', 'सही'),
             expected_latex: '(-x - 7)/((x+1)(x-2))', unsimplified: true, needs_diagnosis: false };
  }
  return { attempt_id: 'mock-a3', correct: true,
           verdict: pick('Correct', 'सही'),
           expected_latex: '', unsimplified: false, needs_diagnosis: false };
}

function mockDiagnose() {
  return {
    blamed_node: 'alg.sign-distribution',
    headline: pick('Your quotient rule is correct.',
                   'आपका भागफल नियम सही है।'),
    body: pick('You dropped the negative when distributing across −(2x+1), so −2x−1 became −2x+1.',
               '−(2x+1) को खोलते समय आपने ऋण चिह्न छोड़ दिया, इसलिए −2x−1 की जगह −2x+1 बन गया।'),
    recurrence: pick('Same mistake as Tuesday.', 'मंगलवार वाली वही ग़लती।'),
    failed_line_index: 1,
    consequence: pick('Distributing a negative drops from mastered to needs-work.',
                      'ऋण चिह्न खोलना: महारत से अभ्यास पर आ गया।'),
    from_cache: true,
    latency_s: 0.0
  };
}

function mockCommit() {
  return {
    xp: 40,
    node_changes: [{ node_id: 'alg.sign-distribution', from: 'mastered', to: 'learning' }],
    unlocked: [],
    // commitAndAdvance marks the current item done before calling this, so the
    // set is finished only when nothing at all is left.
    session_done: remainingProblems().length === 0
  };
}

function mockComplete() {
  return {
    xp_total: 120,
    unlocked_count: 3,
    nodes: [
      { node_id: 'der.chain-rule', name: pick('Chain rule', 'शृंखला नियम'), state: 'now', just_lit: true },
      { node_id: 'der.quotient-rule', name: pick('Quotient rule', 'भागफल नियम'), state: 'mastered', just_lit: true },
      { node_id: 'der.product-rule', name: pick('Product rule', 'गुणनफल नियम'), state: 'mastered', just_lit: false },
      { node_id: 'der.power-rule', name: pick('Power rule', 'घात नियम'), state: 'mastered', just_lit: false }
    ],
    tomorrow: pick('Tomorrow starts with sign distribution.',
                   'कल की शुरुआत ऋण चिह्न से होगी।')
  };
}

var MOCK_TRANSCRIBE = { lines: MOCK_WORK_LINES.slice(), ocr_seconds: 6.8, source: 'cache' };

/* --------------------------------------------------------------------------
   5. API layer
   Every call tries the server first, then falls back to the mock. A failure
   raises the offline banner; the next success lowers it.
   -------------------------------------------------------------------------- */

function url(path) {
  return CONFIG.BASE + path + (path.indexOf('?') === -1 ? '?' : '&') + 'lang=' + state.lang;
}

function setOffline(on) {
  if (state.offline === on) { return; }
  state.offline = on;
  var banner = byId('banner');
  byId('bannerText').textContent = T().offline;
  banner.hidden = !on;
}

function callJSON(method, path, body) {
  var opts = { method: method, headers: { 'Accept': 'application/json' } };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  return fetch(url(path), opts).then(function (r) {
    if (!r.ok) { throw new Error('HTTP ' + r.status); }
    return r.json();
  });
}

/* live() runs the request, fallback() supplies mock data if anything fails. */
function request(method, path, body, fallback) {
  if (CONFIG.DATA_SOURCE === 'MOCK') {
    setOffline(false);
    return Promise.resolve(fallback());
  }
  return callJSON(method, path, body).then(function (data) {
    setOffline(false);
    return data;
  }).catch(function (err) {
    console.warn('[open-tutor] falling back to mock for ' + path + ':', err.message);
    setOffline(true);
    return fallback();
  });
}

var API = {
  state: function () { return request('GET', '/state', undefined, MOCK_STATE); },
  /* full=1 clears the session and lands back on Sign up, which is what
     "Replay onboarding" means now that Sign up is the entry point. */
  reveal: function (itemId) {
    return request('POST', '/solve/reveal', { item_id: itemId }, function () {
      return mockReveal(itemId);
    });
  },
  signOut: function () {
    // `request` prefixes /api and supplies the offline fallback; a bare fetch here would
    // bypass both, which is how this shipped calling an undefined `post`.
    return request('POST', '/session/signout', {}, function () {
      mockSession = { flow: 'signup', name: '', course: null };
      return { ok: true, next: 'signup', flow: 'signup' };
    });
  },
  reset: function (full) {
    return request('POST', full ? '/reset?full=1' : '/reset', {}, function () {
      if (full) { mockSession = { flow: 'signup', name: '', course: null }; }
      return MOCK_STATE();
    });
  },
  courses: function () { return request('GET', '/courses', undefined, MOCK_COURSES); },
  signup: function (name) {
    return request('POST', '/session/signup', { name: name }, function () { return mockSignup(name); });
  },
  selectCourse: function (courseId) {
    return request('POST', '/courses/' + encodeURIComponent(courseId) + '/select', {},
      function () { return mockSelectCourse(courseId); });
  },
  start: function (itemId) {
    return request('POST', '/solve/start', { item_id: itemId }, function () { return mockStart(itemId); });
  },
  grade: function (payload) {
    return request('POST', '/solve/grade', payload, function () { return mockGrade(payload); });
  },
  diagnose: function (payload) {
    return request('POST', '/solve/diagnose', payload, mockDiagnose);
  },
  commit: function (payload) {
    return request('POST', '/solve/commit', payload, mockCommit);
  },
  complete: function () { return request('GET', '/session/complete', undefined, mockComplete); },
  transcribe: function (file, itemId) {
    if (CONFIG.DATA_SOURCE === 'MOCK') {
      return new Promise(function (res) { setTimeout(function () { res(MOCK_TRANSCRIBE); }, 900); });
    }
    var fd = new FormData();
    fd.append('image', file, file.name || 'working.jpg');
    fd.append('item_id', itemId);
    return fetch(url('/solve/transcribe'), { method: 'POST', body: fd }).then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.json();
    }).then(function (d) { setOffline(false); return d; })
      .catch(function (err) {
        console.warn('[open-tutor] transcribe failed:', err.message);
        setOffline(true);
        return MOCK_TRANSCRIBE;
      });
  }
};

/* --------------------------------------------------------------------------
   6. State
   -------------------------------------------------------------------------- */

var state = {
  lang: 'en',
  screen: 'signup',
  tab: 'today',
  data: null,          // GET /api/state payload
  openStage: null,
  offline: false,
  uiReported: false,   // the chrome coverage line is logged once per load
  doneIds: {},         // item ids graded in this session, applied over any reload
  // The solve attempt in progress. Not to be confused with the server's `flow`
  // field, which names a screen and is deliberately never stored here.
  flow: null
};

function newFlow(itemId) {
  return {
    itemId: itemId,
    channel: 'typed',
    start: null,
    hintLevel: 0,
    workLines: [],
    grade: null,
    diagnosis: null,
    blameAnswered: false
  };
}

/* --------------------------------------------------------------------------
   7. Small DOM helpers
   -------------------------------------------------------------------------- */

function byId(id) { return document.getElementById(id); }
function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

function h(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined && text !== null) { n.textContent = text; }
  return n;
}

/* "37 skills" split into its numeral and its word, so the badge can set the
   number large and the word small under it. Language independent: "37 कौशल"
   leads with digits too. Returns nulls if the string does not start with a
   number, and the caller falls back to printing it whole. */
function splitCount(text) {
  var m = /^(\d[\d.,]*)\s*([\s\S]*)$/.exec(String(text === null || text === undefined ? '' : text));
  return m ? { n: m[1], word: m[2] } : null;
}

/* A bevelled panel: the outer node carries the hard drop shadow, the inner face
   carries the skew, the black border and the face colour. Two nodes because a
   filter and a clip-path on one element clip the shadow away. */
function bevel(wrapTag, wrapCls, faceCls) {
  var wrap = h(wrapTag, 'bev' + (wrapCls ? ' ' + wrapCls : ''));
  var face = h(wrapTag === 'div' ? 'div' : 'span', 'bev-face' + (faceCls ? ' ' + faceCls : ''));
  wrap.appendChild(face);
  wrap.face = face;
  return wrap;
}

/* Buttons carry their label in a stroked span inside the bevelled face, so
   setting `textContent` on the button itself would throw the face away. */
function setBtnLabel(id, text) {
  var label = byId(id).querySelector('.btn-label');
  if (label) { label.textContent = text; }
}

function knownState(nodeState) {
  var known = { mastered: 1, learning: 1, now: 1, locked: 1 };
  return known[nodeState] ? nodeState : 'locked';
}

function dotClass(nodeState) { return 'dot dot--' + knownState(nodeState); }

/* Locked stages recede, mastered ones take the gold. Everything else is the
   default white on card. */
function stageToneClass(nodeState) {
  if (nodeState === 'locked') { return ' is-locked'; }
  if (nodeState === 'mastered') { return ' is-mastered'; }
  return '';
}

function stateTextClass(nodeState) {
  if (nodeState === 'locked') { return 'is-locked-text'; }
  if (nodeState === 'now') { return 'is-now-text'; }
  return '';
}

/* --------------------------------------------------------------------------
   8. Chrome
   There is no theme here any more. The arcade style is dark only: there is no
   light mock, and a light Brawl Stars is not a thing. The language toggle is
   the only setting left on You.
   -------------------------------------------------------------------------- */

function applyChrome() {
  var t = T();
  var nodes = document.querySelectorAll('[data-t]');
  for (var i = 0; i < nodes.length; i++) {
    var key = nodes[i].getAttribute('data-t');
    if (typeof t[key] === 'string') { nodes[i].textContent = t[key]; }
  }
  // The field has no visible label, so the placeholder is also its accessible
  // name. Setting it explicitly means a screenreader still names the field
  // once the student has typed into it and the placeholder is gone.
  byId('nameInput').placeholder = t.namePlaceholder;
  byId('nameInput').setAttribute('aria-label', t.namePlaceholder);
  byId('answerInput').placeholder = t.answerPlaceholder;
  byId('bannerText').textContent = t.offline;
  byId('btnLangEn').classList.toggle('is-on', state.lang === 'en');
  byId('btnLangHi').classList.toggle('is-on', state.lang === 'hi');
}

function setLang(lang) {
  if (state.lang === lang) { return; }
  state.lang = lang;
  try { localStorage.setItem('ot.lang', lang); } catch (e) { /* private mode */ }
  document.documentElement.lang = lang;
  applyChrome();
  // The server owns the translated content, so re-fetch rather than re-label.
  loadState().then(function () {
    renderAll();
    if (state.screen === 'solve' && state.flow) { restartCurrentProblem(); }
  });
}

/* --------------------------------------------------------------------------
   9. Screen routing
   -------------------------------------------------------------------------- */

var TAB_SCREENS = { today: 1, path: 1, blockers: 1, you: 1 };

/* GET /api/state carries `flow`, a fact about the student rather than a screen
   name, and this table is the one place it becomes a screen. The rule lives on
   the server: nothing here recomputes it and nothing about it is kept in
   localStorage. */
var SCREEN_FOR_FLOW = { signup: 'signup', courses: 'courses', ready: 'today' };

/* Where an unknown or missing `flow` lands. Sign up, because a `flow` we cannot
   read is a student we know nothing about, and the thing to do with a student we
   know nothing about is ask who they are. It is also the screen that costs least
   to be wrong about: the student types a name and the server answers again. */
var FALLBACK_SCREEN = 'signup';

function routeFromFlow(flowName) {
  var screen = SCREEN_FOR_FLOW[flowName] || FALLBACK_SCREEN;
  if (screen === 'today') { goTab('today'); return Promise.resolve(); }
  if (screen === 'courses') { return openCourses(); }
  openSignup();
  return Promise.resolve();
}

function showScreen(name) {
  state.screen = name;
  // Purely presentational: it lets the stylesheet dress one screen differently
  // without a class on every node. Nothing reads it back.
  document.body.setAttribute('data-screen', name);
  var screens = document.querySelectorAll('.screen');
  for (var i = 0; i < screens.length; i++) {
    screens[i].classList.toggle('is-active', screens[i].id === 'screen-' + name);
  }
  var onTab = !!TAB_SCREENS[name];
  byId('tabbar').hidden = !onTab;
  if (onTab) {
    state.tab = name;
    var tabs = document.querySelectorAll('.tab');
    for (var j = 0; j < tabs.length; j++) {
      tabs[j].classList.toggle('is-on', tabs[j].getAttribute('data-tab') === name);
    }
  }
  var scroller = document.querySelector('#screen-' + name + ' .scroll');
  if (scroller) { scroller.scrollTop = 0; }
}

function goTab(name) { showScreen(name); renderAll(); }

/* --------------------------------------------------------------------------
   9b. Sign up and course selection
   Sign up is the entry point, a placeholder, and says so on the screen: a
   display name, one button, no password and no email field. Course selection
   renders whatever GET /api/courses returns and gates interaction on
   `selectable` alone.
   -------------------------------------------------------------------------- */

function openSignup() {
  showScreen('signup');
  byId('btnSignup').disabled = false;
  byId('nameInput').focus();
}

function submitSignup() {
  var btn = byId('btnSignup');
  if (btn.disabled) { return; }
  btn.disabled = true;
  // The name is optional in the contract, so an empty field is allowed through.
  API.signup(byId('nameInput').value.trim()).then(function (res) {
    btn.disabled = false;
    routeFromFlow(res && res.next ? res.next : 'courses');
  });
}

function openCourses() {
  showScreen('courses');
  clear(byId('coursesList'));
  return API.courses().then(renderCourses);
}

function renderCourses(payload) {
  var list = byId('coursesList');
  var t = T();
  clear(list);

  var courses = (payload && Array.isArray(payload.courses)) ? payload.courses : [];
  var selected = payload ? payload.selected : null;

  var ruled = false;

  courses.forEach(function (c) {
    // The server decides what is playable. Never infer this from `c.state`.
    var playable = c.selectable === true;

    // One plain rule between the playable card and the rest. No caption on it:
    // "the roadmap" is chrome the server does not send, and inventing an
    // untranslated string for a divider is not worth it.
    if (!playable && !ruled && list.firstChild) {
      list.appendChild(h('div', 'rule'));
      ruled = true;
    }

    var card = bevel(playable ? 'button' : 'div',
                     'coursecard ' + (playable ? 'bev--drop-lg' : 'coursecard--soon bev--drop-sm'),
                     playable ? 'bev-face--card' : 'bev-face--dim');

    if (playable) {
      card.type = 'button';
      if (selected && c.id === selected) {
        card.classList.add('is-selected');
        card.setAttribute('aria-current', 'true');
      }
      card.addEventListener('click', function () { chooseCourse(c.id); });
    } else {
      // A div, so there is no button to focus and nothing to press: it is out
      // of the tab order by construction rather than by tabindex="-1".
      // aria-disabled states in the accessibility tree what the dimmed card and
      // the "Coming soon" marker say on the screen.
      card.setAttribute('aria-disabled', 'true');
    }

    // The server sends a ready-made `skills_line` ("37 skills"). Prefer it, and
    // fall back to composing the count with a local word, because the addendum
    // only promises `skills`.
    var skillsLine = c.skills_line ||
      ((c.skills === undefined || c.skills === null) ? '' : String(c.skills) + ' ' + t.skillsWord);

    if (playable) {
      card.face.appendChild(h('span', 'strip strip--cyan'));
      var body = h('span', 'coursecard-body');
      body.appendChild(h('span', 'coursecard-title', c.title || ''));
      if (c.subtitle) { body.appendChild(h('span', 'coursecard-sub', c.subtitle)); }

      if (c.ends_at) {
        var ends = bevel('span', 'coursecard-panel', 'bev-face--sunk');
        var endsBody = h('span', 'coursecard-panelbody');
        endsBody.appendChild(h('span', 'coursecard-endslabel', t.endsAt));
        endsBody.appendChild(h('span', 'coursecard-ends', c.ends_at));
        ends.face.appendChild(endsBody);
        body.appendChild(ends);
      }

      if (c.detail) {
        var meta = bevel('span', 'coursecard-panel', 'bev-face--sunk');
        var metaBody = h('span', 'coursecard-panelbody');
        metaBody.appendChild(h('span', 'coursecard-detail', c.detail));
        meta.face.appendChild(metaBody);
        body.appendChild(meta);
      }

      // A plate, not a button: the whole card is already the button, and a
      // button inside a button is neither valid nor clickable.
      var play = bevel('span', 'coursecard-play', 'bev-face--gold');
      play.face.appendChild(h('span', 'coursecard-playlabel', t.start));
      body.appendChild(play);

      card.face.appendChild(body);

      if (skillsLine) {
        var counts = splitCount(skillsLine);
        var badge = bevel('span', 'starbadge', '');
        var badgeText = h('span', 'starbadge-text', counts ? counts.n : skillsLine);
        if (counts && counts.word) { badgeText.appendChild(h('span', 'starbadge-word', counts.word)); }
        badge.face.appendChild(badgeText);
        card.appendChild(badge);
      }
    } else {
      var soon = h('div', 'coursecard-body');
      soon.appendChild(h('span', 'coursecard-title', c.title || ''));
      if (c.subtitle) { soon.appendChild(h('span', 'coursecard-sub', c.subtitle)); }
      if (c.ends_at) { soon.appendChild(h('span', 'coursecard-endsline', t.endsAt + ' ' + c.ends_at)); }
      card.face.appendChild(soon);

      // The server's own wording when it sends one, so the marker stays
      // localised and is read straight after the title.
      var pill = bevel('div', 'coursecard-pill', 'bev-face--card');
      pill.face.appendChild(h('span', 'coursecard-pilltext', c.detail || t.comingSoon));
      card.appendChild(pill);
    }

    list.appendChild(card);
  });
}

function chooseCourse(courseId) {
  API.selectCourse(courseId).then(function (payload) {
    // A refusal comes back as HTTP 200 with `error` and no state body. That
    // string is a machine string and is not localised, so it is never put on
    // screen: the non-interactive card is the gate and this is only the
    // backstop behind it. Staying put is the whole behaviour.
    if (!payload || (payload.error && !payload.today)) {
      console.warn('[open-tutor] course refused:', courseId, payload && payload.error);
      return;
    }
    // Select returns the full GET /api/state body, so go straight to Today
    // with this payload rather than asking again.
    adoptState(payload);
    goTab('today');
  });
}

/* --------------------------------------------------------------------------
   10. Tab renderers
   -------------------------------------------------------------------------- */

function renderAll() {
  if (!state.data) { return; }
  applyChrome();          // before the renderers, which set their own labels
  renderToday();
  renderPath();
  renderBlockers();
  renderYou();
}

function problems() {
  var d = state.data;
  return (d && d.today && Array.isArray(d.today.problems)) ? d.today.problems : [];
}

function isDone(p) { return !!(p.done || state.doneIds[p.item_id]); }

function remainingProblems() {
  return problems().filter(function (p) { return !isDone(p); });
}

/* The chip colour names the slot the engine put the problem in, so it comes
   from `slot` and never from the chip text, which is prose and translated. */
function chipClass(slot) {
  var known = { blocker: 'blocker', review: 'review', new: 'new', goal_link: 'goal-link' };
  return 'chip chip--' + (known[slot] || 'review');
}

function renderToday() {
  var d = state.data, list = byId('todayList');
  byId('todayHead').textContent = (d.today && d.today.headline) || '';
  // The same streak line the You tab shows, in the header where the mock has it.
  byId('todayStreak').textContent = (d.you && d.you.streak_line) || '';
  clear(list);

  var ps = problems();
  if (!ps.length) {
    list.appendChild(h('div', 'inline-note', T().setDone));
  }
  ps.forEach(function (p, i) {
    var card = bevel('button', 'problemcard' + (isDone(p) ? ' is-done' : ''), 'bev-face--card');
    var body = h('span', 'problemcard-body');

    var row = h('span', 'problemcard-row');
    row.appendChild(h('span', 'problemcard-n', String(i + 1)));
    var math = h('span', 'problemcard-math');
    math.innerHTML = mixedHTML(p.math);
    row.appendChild(math);
    body.appendChild(row);

    var meta = h('span', 'problemcard-meta');
    if (p.is_blocker) { meta.appendChild(h('span', 'problemcard-dot')); }
    if (p.chip) { meta.appendChild(h('span', chipClass(p.slot), p.chip)); }
    if (isDone(p)) { meta.appendChild(h('span', 'problemcard-tick', '✓')); }
    body.appendChild(meta);

    card.face.appendChild(body);
    card.addEventListener('click', function () { startProblem(p.item_id); });
    list.appendChild(card);
  });

  var left = remainingProblems();
  byId('btnStartSession').disabled = left.length === 0;
  setBtnLabel('btnStartSession', left.length === 0 ? T().finish : T().start);
}

function renderPath() {
  var d = state.data;
  var goal = d.goal || {};
  byId('goalTitle').textContent = goal.title || '';
  byId('goalAway').textContent = (goal.skills_away === undefined || goal.skills_away === null)
    ? '' : String(goal.skills_away) + ' ' + (state.lang === 'hi' ? 'कौशल दूर' : 'skills away');
  byId('goalPace').textContent = goal.pace_line || '';

  var wrap = byId('pathStages');
  clear(wrap);
  var stages = (d.path && Array.isArray(d.path.stages)) ? d.path.stages : [];

  if (state.openStage === null) {
    var nowStage = stages.filter(function (s) { return s.state === 'now'; })[0];
    state.openStage = nowStage ? nowStage.id : (stages[0] ? stages[0].id : null);
  }

  stages.forEach(function (s) {
    var item = h('div', 'stage-item');
    item.appendChild(h('span', 'stage-node stage-node--' + knownState(s.state)));

    var open = state.openStage === s.id;
    var tone = stageToneClass(s.state);
    var btn = bevel('button', 'stage-btn' + (open ? ' is-open' : '') + (s.state === 'now' ? ' is-now' : ''),
                    'bev-face--card');
    var body = h('span', 'stage-body');
    var row = h('span', 'stage-row');
    row.appendChild(h('span', 'stage-name' + tone, s.name || ''));
    row.appendChild(h('span', 'stage-count' + tone, s.count || ''));
    body.appendChild(row);
    body.appendChild(h('span', 'stage-state' + tone, legendLabelFor(s.state)));
    btn.face.appendChild(body);
    btn.addEventListener('click', function () {
      state.openStage = open ? null : s.id;
      renderPath();
    });
    item.appendChild(btn);

    if (open) {
      var skills = bevel('div', 'skills', 'bev-face--sunk');
      var skillsBody = h('div', 'skills-body');
      (s.skills || []).forEach(function (k) {
        var srow = h('div', 'skill');
        srow.appendChild(h('span', dotClass(k.state)));
        var sbody = h('span', 'skill-body');
        sbody.appendChild(h('span', 'skill-name ' + stateTextClass(k.state), k.name || ''));
        var tags = h('span', 'skill-tags');
        tags.appendChild(h('span', 'skill-state ' + stateTextClass(k.state), legendLabelFor(k.state)));
        if (k.needs) { tags.appendChild(h('span', 'skill-needs', k.needs)); }
        sbody.appendChild(tags);
        srow.appendChild(sbody);
        skillsBody.appendChild(srow);
      });
      skills.face.appendChild(skillsBody);
      item.appendChild(skills);
    }
    wrap.appendChild(item);
  });

  var legend = byId('pathLegend');
  clear(legend);
  ((d.path && d.path.legend) || []).forEach(function (l) {
    var span = h('span', 'legend-item');
    span.appendChild(h('span', dotClass(l.state)));
    span.appendChild(document.createTextNode(l.label || ''));
    legend.appendChild(span);
  });
}

/* The legend is the server's own vocabulary for the four states, so state
   labels elsewhere are read back from it rather than translated here. */
function legendLabelFor(nodeState) {
  var legend = (state.data && state.data.path && state.data.path.legend) || [];
  for (var i = 0; i < legend.length; i++) {
    if (legend[i].state === nodeState) { return legend[i].label || nodeState; }
  }
  return nodeState || '';
}

/* One line of the duel: the student's own failing step, then the corrected one.
   The marker is a shape as well as a colour, the same rule the four node states
   follow, so the pair is still readable with the strike-through alone. */
function blockerLine(kind, tex) {
  var row = h('div', 'blockercard-line blockercard-' + kind);
  row.appendChild(h('span', 'blockercard-mark blockercard-mark--' + kind));
  var text = h('span', 'blockercard-tex');
  // The rule the strike is drawn on. `text-decoration: line-through` does not
  // cross an atomic inline such as KaTeX's box, so on a rendered expression it
  // simply vanishes; this inner span is sized to the maths and carries the line
  // as a ::after instead. Without it the "wrong line" is only a grey line.
  var inner = h('span', 'blockercard-strike');
  inner.innerHTML = mixedHTML(tex || '');
  text.appendChild(inner);
  row.appendChild(text);
  return row;
}

function renderBlockers() {
  var list = byId('blockersList');
  var bs = Array.isArray(state.data.blockers) ? state.data.blockers : [];
  byId('blockersHead').textContent = T().blockersHead(bs.length);
  clear(list);

  bs.forEach(function (b, i) {
    var card = bevel('div', 'blockercard bev--drop-lg', 'bev-face--card');
    var body = h('div', 'blockercard-body');

    // The rank is a purple plate rather than a caption, because a blocker is a
    // named opponent you can beat and not an item on a list.
    var top = h('div', 'blockercard-top');
    var rank = bevel('div', 'blockercard-rankplate', '');
    rank.face.appendChild(h('span', 'blockercard-rank', b.rank || ''));
    top.appendChild(rank);
    top.appendChild(h('span', 'blockercard-freq', b.freq || ''));
    body.appendChild(top);

    body.appendChild(h('div', 'blockercard-name', b.name || ''));

    var lines = h('div', 'blockercard-lines');
    lines.appendChild(blockerLine('wrong', b.wrong));
    lines.appendChild(blockerLine('right', b.right));
    body.appendChild(lines);

    // The one action on the card, so it takes the gold.
    var fix = h('button', 'btn btn--ghost blockercard-fix');
    var face = h('span', 'bev-face bev-face--gold btn-face');
    face.appendChild(h('span', 'btn-label', T().fix));
    fix.appendChild(face);
    fix.addEventListener('click', function () { fixBlocker(i); });
    body.appendChild(fix);

    card.face.appendChild(body);
    list.appendChild(card);
  });
}

function renderYou() {
  var y = state.data.you || {};
  byId('youStreak').textContent = y.streak_line || '';
  byId('statMastered').textContent = (y.mastered === undefined || y.mastered === null) ? '-' : String(y.mastered);
  byId('statAccuracy').textContent = y.accuracy || '-';
}

/* --------------------------------------------------------------------------
   11. Step indicator: two dots typed, three photo
   -------------------------------------------------------------------------- */

function stepLabels() {
  var t = T();
  return state.flow && state.flow.channel === 'photo'
    ? [t.stepSolve, t.stepCheck, t.stepResult]
    : [t.stepSolve, t.stepResult];
}

function renderSteps(barsId, labelId, screenName) {
  var labels = stepLabels();
  var idx;
  if (screenName === 'solve') { idx = 0; }
  else if (screenName === 'check') { idx = 1; }
  else { idx = labels.length - 1; }

  var bars = byId(barsId);
  clear(bars);
  for (var i = 0; i < labels.length; i++) {
    bars.appendChild(h('span', 'stepbar' + (i <= idx ? ' is-on' : '')));
  }
  byId(labelId).textContent = labels[idx];
}

/* --------------------------------------------------------------------------
   12. Solve
   -------------------------------------------------------------------------- */

function startProblem(itemId) {
  state.flow = newFlow(itemId);
  showScreen('solve');
  byId('problemLabel').textContent = '';
  byId('problemMath').innerHTML = '';
  byId('answerInput').value = '';
  byId('solveNote').hidden = true;
  renderSteps('solveSteps', 'solveStepLabel', 'solve');
  renderHints();

  API.start(itemId).then(function (s) {
    if (!state.flow || state.flow.itemId !== itemId) { return; }
    state.flow.start = s;
    byId('problemLabel').textContent = s.problem_label || '';
    byId('problemMath').innerHTML = mixedHTML(s.math);
    renderHints();
    byId('answerInput').focus();
  });
}

function restartCurrentProblem() {
  if (state.flow) { startProblem(state.flow.itemId); }
}

function renderHints() {
  var f = state.flow;
  var all = (f && f.start && Array.isArray(f.start.hints)) ? f.start.hints : [];
  var shown = f ? f.hintLevel : 0;
  // The rungs are drawn as pips; the count stays for a screenreader, which
  // cannot see them.
  byId('hintCount').textContent = shown + ' / ' + (all.length || 0);

  var pips = byId('hintPips');
  clear(pips);
  for (var i = 0; i < all.length; i++) {
    pips.appendChild(h('span', 'pip' + (i < shown ? ' is-on' : '')));
  }

  var list = byId('hintList');
  clear(list);
  all.slice(0, shown).forEach(function (hint, idx) {
    var card = bevel('div', 'hint', 'bev-face--dim');
    var body = h('div', 'hint-body');
    var row = h('div', 'hint-row');
    row.appendChild(h('span', 'hint-n', String(hint.n !== undefined ? hint.n : idx + 1)));
    var text = h('span', 'hint-text' + (hint.is_math ? ' hint-text--math' : ''));
    // A maths rung arrives either bare or already wrapped in $...$, and the
    // second form is what the item bank actually sends. mixedHTML handles both;
    // handing the dollars straight to KaTeX is a parse error on screen.
    if (hint.is_math) { text.innerHTML = mixedHTML(hint.text); }
    else { text.innerHTML = proseHTML(hint.text); }
    row.appendChild(text);
    body.appendChild(row);
    card.face.appendChild(body);
    list.appendChild(card);
  });

  var btn = byId('btnNextHint');
  // The twin problem in hint rung 4 has no endpoint in the contract, so the
  // ladder simply ends rather than offering something that cannot be served.
  btn.hidden = !all.length || shown >= all.length;
  setBtnLabel('btnNextHint', T().hintNext);
}

function nextHint() {
  var f = state.flow;
  if (!f || !f.start) { return; }
  var total = (f.start.hints || []).length;
  if (f.hintLevel < total) { f.hintLevel += 1; renderHints(); }
}

function submitTyped() {
  var f = state.flow;
  if (!f) { return; }
  var answer = byId('answerInput').value.trim();
  if (!answer) { byId('answerInput').focus(); return; }
  f.channel = 'typed';
  f.workLines = [];
  gradeAndShow({
    item_id: f.itemId,
    typed_answer: answer,
    hint_level: f.hintLevel,
    channel: 'typed',
    work_lines: []
  });
}

/* --------------------------------------------------------------------------
   13. Photo path
   -------------------------------------------------------------------------- */

function onPhotoChosen(ev) {
  var f = state.flow;
  var file = ev.target.files && ev.target.files[0];
  ev.target.value = '';
  if (!file || !f) { return; }

  var note = byId('solveNote');
  note.hidden = false;
  note.textContent = T().reading;

  API.transcribe(file, f.itemId).then(function (res) {
    if (!state.flow || state.flow !== f) { return; }
    var lines = (res && Array.isArray(res.lines)) ? res.lines.filter(function (l) { return String(l).trim(); }) : [];
    if (!lines.length) {
      // Fall back to the typed path with a plain message rather than dead-ending.
      note.hidden = false;
      note.textContent = res && res.error ? T().ocrEmpty : T().ocrEmpty;
      byId('answerInput').focus();
      return;
    }
    note.hidden = true;
    f.channel = 'photo';
    f.workLines = lines;
    showScreen('check');
    renderCheck();
  }).catch(function () {
    note.hidden = false;
    note.textContent = T().ocrFailed;
  });
}

function renderCheck() {
  var f = state.flow;
  renderSteps('checkSteps', 'checkStepLabel', 'check');
  var wrap = byId('transcript');
  clear(wrap);
  f.workLines.forEach(function (line, i) {
    var row = bevel('div', 'tline', '');
    var body = h('div', 'tline-body');
    // Rendered as mathematics at rest so OCR errors are easy to spot, but
    // editing happens on the raw transcript text the server sent.
    var text = h('span', 'tline-text');
    text.innerHTML = mixedHTML(f.workLines[i]);
    text.contentEditable = 'true';
    text.spellcheck = false;
    text.addEventListener('focus', function () { text.textContent = f.workLines[i]; });
    text.addEventListener('input', function () { f.workLines[i] = text.textContent; });
    text.addEventListener('blur', function () {
      f.workLines[i] = text.textContent;
      text.innerHTML = mixedHTML(f.workLines[i]);
    });
    body.appendChild(text);
    body.appendChild(h('span', 'tline-edit', T().edit));
    body.addEventListener('click', function () { text.focus(); });
    row.face.appendChild(body);
    wrap.appendChild(row);
  });
}

function confirmLines() {
  var f = state.flow;
  if (!f) { return; }
  var lines = f.workLines.filter(function (l) { return String(l).trim(); });
  gradeAndShow({
    item_id: f.itemId,
    typed_answer: lines.length ? lines[lines.length - 1] : '',
    hint_level: f.hintLevel,
    channel: 'photo',
    work_lines: lines
  });
}

/* --------------------------------------------------------------------------
   14. Result: three states
   -------------------------------------------------------------------------- */

function gradeAndShow(payload) {
  var f = state.flow;
  showScreen('result');
  resetResultScreen();
  renderSteps('resultSteps', 'resultStepLabel', 'result');

  API.grade(payload).then(function (g) {
    if (!state.flow || state.flow !== f) { return; }
    f.grade = g;
    renderVerdict(g);

    if (g.needs_diagnosis && !g.correct) {
      requestDiagnosis(f, payload);
    } else {
      showNextButton();
    }
  });
}

function resetResultScreen() {
  byId('resVerdict').textContent = '';
  byId('resVerdict').className = 'verdict hd hd--l';
  byId('resStrip').className = 'strip';
  byId('resMark').className = 'verdict-mark';
  byId('resExpected').innerHTML = '';
  byId('resNote').hidden = true;
  byId('resNote').textContent = '';
  byId('resWork').hidden = true;
  byId('resWaiting').hidden = true;
  byId('resDiag').hidden = true;
  byId('screen-result').classList.remove('is-lean');
  // The footer bar, not the button, is what disappears: an empty anchored bar
  // at the foot of the screen is worse than no bar at all.
  byId('resultFoot').hidden = true;
}

function renderVerdict(g) {
  var f = state.flow;
  var verdict = byId('resVerdict');
  verdict.textContent = g.verdict || '';
  verdict.classList.toggle('is-correct', !!g.correct);

  // Colour carries the state, and none of it is red. Gold for correct, amber for
  // correct-but-unsimplified, purple for incorrect: purple is the blocker colour
  // everywhere else in the app, which is exactly what a wrong answer just found.
  // The star is drawn for BOTH correct states, so the unsimplified one cannot
  // read as a failure however fast the screen is glanced at.
  byId('resStrip').className = 'strip ' +
    (g.correct ? (g.unsimplified ? 'strip--amber' : 'strip--gold') : 'strip--purple');
  byId('resMark').className = 'verdict-mark' + (g.correct ? ' is-on' : '');
  // A correct answer puts nothing under the card, so the card centres itself
  // rather than leaving three quarters of the screen visibly empty.
  byId('screen-result').classList.toggle('is-lean', !!g.correct);

  // State 1, correct: the verdict and nothing else. No diagnosis is requested.
  // State 2, correct but unsimplified: the verdict plus one light note.
  // State 3, incorrect: expected answer, the working, then the diagnosis.
  if (g.correct && g.unsimplified) {
    var note = byId('resNote');
    note.hidden = false;
    note.innerHTML = escapeHTML(T().unsimplified) +
      (g.expected_latex ? ' ' + mixedHTML(g.expected_latex) : '');
  }

  if (!g.correct) {
    if (g.expected_latex) {
      byId('resExpected').innerHTML = escapeHTML(T().expected) + ' ' + mixedHTML(g.expected_latex);
    }
    if (f.workLines.length) { renderWork(-1); }
  }
}

function renderWork(highlightIndex) {
  var f = state.flow;
  var block = byId('resWork');
  block.hidden = false;
  clear(block);
  f.workLines.forEach(function (line, i) {
    var span = h('span', 'workline' + (i === highlightIndex ? ' is-hl' : ''));
    span.innerHTML = mixedHTML(line);
    block.appendChild(span);
  });
}

function requestDiagnosis(f, gradePayload) {
  byId('resWaiting').hidden = false;
  var started = Date.now();
  var workText = f.workLines.length ? f.workLines.join('\n') : (gradePayload.typed_answer || '');

  API.diagnose({
    attempt_id: f.grade.attempt_id,
    item_id: f.itemId,
    work_text: workText
  }).then(function (dg) {
    var wait = Math.max(0, CONFIG.DIAGNOSIS_MIN_WAIT_MS - (Date.now() - started));
    setTimeout(function () {
      if (!state.flow || state.flow !== f) { return; }
      byId('resWaiting').hidden = true;
      f.diagnosis = dg;
      if (!dg || (!dg.headline && !dg.body)) {
        byId('resNote').hidden = false;
        byId('resNote').textContent = T().diagFailed;
        showNextButton();
        return;
      }
      renderDiagnosis(dg);
    }, wait);
  }).catch(function () {
    byId('resWaiting').hidden = true;
    byId('resNote').hidden = false;
    byId('resNote').textContent = T().diagFailed;
    showNextButton();
  });
}

function renderDiagnosis(dg) {
  var f = state.flow;
  if (f.workLines.length && typeof dg.failed_line_index === 'number' && dg.failed_line_index >= 0) {
    renderWork(dg.failed_line_index);
  }
  byId('diagHeadline').textContent = dg.headline || '';
  byId('diagBody').innerHTML = proseHTML(dg.body || '');

  var rec = byId('diagRecurrence');
  rec.textContent = dg.recurrence || '';
  rec.hidden = !dg.recurrence;

  var consRow = byId('diagConsequenceRow');
  byId('diagConsequence').textContent = dg.consequence || '';
  consRow.hidden = !dg.consequence;

  var panel = byId('resDiag');
  panel.hidden = false;
  // restart the arrive animation
  panel.style.animation = 'none';
  void panel.offsetWidth;
  panel.style.animation = '';
}

function showNextButton() {
  byId('resultFoot').hidden = false;
  setBtnLabel('btnResultNext', remainingProblems().length > 1 ? T().next : T().finish);
}

function answerBlame(confirmed) {
  var f = state.flow;
  if (!f || f.blameAnswered) { return; }
  f.blameAnswered = true;
  byId('btnBlameYes').disabled = true;
  byId('btnBlameNo').disabled = true;
  commitAndAdvance(confirmed);
}

function commitAndAdvance(blameConfirmed) {
  var f = state.flow;
  if (!f) { return; }
  state.doneIds[f.itemId] = true;

  var payload = { attempt_id: f.grade ? f.grade.attempt_id : null, blame_confirmed: null };
  if (blameConfirmed !== undefined && blameConfirmed !== null) { payload.blame_confirmed = blameConfirmed; }

  byId('btnResultNext').disabled = true;

  API.commit(payload).then(function (c) {
    byId('btnResultNext').disabled = false;
    byId('btnBlameYes').disabled = false;
    byId('btnBlameNo').disabled = false;
    // The contract calls GET /api/state after every commit.
    return loadState().then(function () {
      var left = remainingProblems();
      if ((c && c.session_done) || left.length === 0) {
        goComplete();
      } else {
        startProblem(left[0].item_id);
      }
    });
  });
}

/* --------------------------------------------------------------------------
   15. Complete
   -------------------------------------------------------------------------- */

function goComplete() {
  showScreen('complete');
  var wrap = byId('finishNodes');
  clear(wrap);
  byId('tomorrowLine').textContent = '';
  byId('xpNum').textContent = '';
  byId('xpLabel').textContent = '';
  byId('unlockedNum').textContent = '';
  byId('unlockedLabel').textContent = '';
  byId('spineWrap').classList.remove('is-rising');
  byId('spineWrap').style.height = '0px';

  API.complete().then(function (c) {
    var t = T();
    var nodes = Array.isArray(c.nodes) ? c.nodes : [];
    clear(wrap);
    nodes.forEach(function (n) {
      var row = h('div', 'finish-node');
      // The full node shape rather than a small dot, because this is the screen
      // where a node turning gold is the whole point.
      var justLit = (n.state === 'mastered' && !!n.just_lit);
      var node = h('span', 'stage-node stage-node--inline stage-node--' + knownState(n.state) +
                           (justLit ? ' is-pending' : ''));
      if (justLit) { node.setAttribute('data-lit', '1'); }
      row.appendChild(node);
      row.appendChild(h('span', 'finish-name ' + stateTextClass(n.state), n.name || ''));
      wrap.appendChild(row);
    });

    byId('tomorrowLine').textContent = c.tomorrow || '';
    // Two plates rather than one line: the numbers are the reward, so they are
    // set as numbers. Both words are chrome the server already owns.
    byId('xpNum').textContent = '+' + (c.xp_total || 0);
    byId('xpLabel').textContent = t.xp;
    byId('unlockedNum').textContent = String(c.unlocked_count || 0);
    byId('unlockedLabel').textContent = t.unlockedWord;

    // The spine grows from the bottom to the highest lit node, THEN the nodes
    // earned today take the gold. Measured in pixels against the rail rather
    // than as a percentage of the panel, so the fill cannot run past its end.
    var lit = nodes.filter(function (n) { return n.state === 'mastered'; }).length;
    var frac = nodes.length ? Math.max(0.18, lit / nodes.length) : 0;
    var finish = document.querySelector('#screen-complete .finish');
    var railLength = Math.max(0, (finish ? finish.offsetHeight : 0) - 40);
    var wrapEl = byId('spineWrap');
    wrapEl.style.height = Math.round(frac * railLength) + 'px';
    setTimeout(function () { wrapEl.classList.add('is-rising'); }, 60);

    setTimeout(function () {
      var lits = wrap.querySelectorAll('[data-lit="1"]');
      for (var i = 0; i < lits.length; i++) {
        lits[i].classList.remove('is-pending');
        lits[i].classList.add('is-lit');
      }
    }, 900);
  });
}

/* --------------------------------------------------------------------------
   16. Blockers, "Fix this"
   The contract has no endpoint for composing a set from a node id, so this
   opens the matching blocker problem already in today's set.
   -------------------------------------------------------------------------- */

function fixBlocker(rankIndex) {
  var blockerProblems = problems().filter(function (p) { return p.is_blocker && !isDone(p); });
  var target = blockerProblems[rankIndex] || blockerProblems[0] || remainingProblems()[0] || problems()[0];
  if (target) { startProblem(target.item_id); }
}

/* --------------------------------------------------------------------------
   17. Loading
   -------------------------------------------------------------------------- */

/* Adopting a state body. POST /api/courses/{id}/select returns the same shape,
   so it takes this path too and Today never needs a second call. */
function adoptState(payload) {
  if (payload && payload.lang && payload.lang !== state.lang) {
    // Trust the server about which language it actually served.
    state.lang = payload.lang;
    applyChrome();
  }
  applyServerUI(payload);
  state.data = payload;
  if (CONFIG.FAST_DEMO) {
    problems().slice(0, 4).forEach(function (p) { state.doneIds[p.item_id] = true; });
    CONFIG.FAST_DEMO = false;
  }
  return payload;
}

function loadState() {
  return API.state().then(adoptState);
}

/* --------------------------------------------------------------------------
   18. Wiring
   -------------------------------------------------------------------------- */

function wire() {
  byId('btnSignup').addEventListener('click', submitSignup);
  byId('nameInput').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { submitSignup(); }
  });

  byId('btnStartSession').addEventListener('click', function () {
    var left = remainingProblems();
    if (left.length) { startProblem(left[0].item_id); } else { goComplete(); }
  });

  var tabs = document.querySelectorAll('.tab');
  for (var i = 0; i < tabs.length; i++) {
    (function (btn) {
      btn.addEventListener('click', function () { goTab(btn.getAttribute('data-tab')); });
    })(tabs[i]);
  }

  var exits = document.querySelectorAll('[data-exit]');
  for (var j = 0; j < exits.length; j++) {
    exits[j].addEventListener('click', function () { state.flow = null; goTab('today'); });
  }

  byId('btnNextHint').addEventListener('click', nextHint);
  byId('btnCheckAnswer').addEventListener('click', submitTyped);
  byId('answerInput').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { submitTyped(); }
  });

  byId('btnPhoto').addEventListener('click', function () { byId('photoInput').click(); });
  byId('photoInput').addEventListener('change', onPhotoChosen);

  byId('btnConfirmLines').addEventListener('click', confirmLines);

  byId('btnBlameYes').addEventListener('click', function () { answerBlame(true); });
  byId('btnBlameNo').addEventListener('click', function () { answerBlame(false); });
  byId('btnResultNext').addEventListener('click', function () { commitAndAdvance(null); });

  byId('btnDone').addEventListener('click', function () { state.flow = null; goTab('today'); });

  byId('btnLangEn').addEventListener('click', function () { setLang('en'); });
  byId('btnLangHi').addEventListener('click', function () { setLang('hi'); });

  byId('btnSignOut').addEventListener('click', function () {
    // Sign out returns to Sign up. It is a student action, so it clears the session; the demo
    // reset deliberately keeps the course selected and is a different button.
    state.doneIds = {};
    state.flow = null;
    state.openStage = null;
    API.signOut().then(function () {
      return loadState();
    }).then(function (payload) {
      renderAll();
      routeFromFlow(payload && payload.flow);
    }).catch(function (err) {
      console.error('[open-tutor] sign out failed', err);
      routeFromFlow('signup');
    });
  });

  byId('btnReplay').addEventListener('click', function () {
    state.doneIds = {};
    state.flow = null;
    state.openStage = null;
    // full=1 clears the session as well, which is what replaying the
    // onboarding means: back to Sign up, then the course.
    API.reset(true).then(function (payload) {
      adoptState(payload);
      renderAll();
      routeFromFlow(payload && payload.flow);
    });
  });

  // DEMO CHEAT. Triple tap the problem to fill the answer box: first tap a correct answer, tap
  // again for a deliberately wrong one, so a presenter can reach either branch of Result without
  // typing algebra on stage. Three taps is hard to hit by accident and invisible to anyone who has
  // not been told. Both values come from the server, which verifies them through the real grader
  // first, so the cheat can never fill something that then fails to behave as promised.
  (function () {
    var taps = 0, timer = null, cache = null, useWrong = false;
    var el = byId('problemMath');
    if (!el) { return; }
    el.addEventListener('click', function () {
      taps += 1;
      clearTimeout(timer);
      timer = setTimeout(function () { taps = 0; }, 600);
      if (taps < 3) { return; }
      taps = 0;
      var itemId = state.flow && state.flow.itemId;
      if (!itemId) { return; }
      var apply = function (r) {
        if (!r || r.error) { console.warn('[open-tutor] reveal unavailable', r && r.error); return; }
        var value = useWrong ? (r.wrong || r.correct) : r.correct;
        useWrong = !useWrong;
        var input = byId('answerInput');
        input.value = value || '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
        el.classList.add('is-revealed');
        setTimeout(function () { el.classList.remove('is-revealed'); }, 400);
      };
      if (cache && cache.itemId === itemId) { apply(cache.data); return; }
      API.reveal(itemId).then(function (r) { cache = { itemId: itemId, data: r }; apply(r); });
    });
  })();

}

function boot() {
  try {
    var savedLang = localStorage.getItem('ot.lang');
    if (savedLang === 'en' || savedLang === 'hi') { state.lang = savedLang; }
  } catch (e) { /* private mode */ }

  // Says in the console, once, whether the maths on this page is KaTeX or the
  // plain-text fallback. A demo that silently drew fractions as "(a)/(b)" is
  // exactly the failure this line is here to make loud.
  if (!window.katex) { reportMathFallback('script did not load before app.js'); }

  document.documentElement.lang = state.lang;
  applyChrome();
  wire();
  // Sign up is the first thing painted under the veil, and it is also where an
  // unreadable `flow` lands, so the two agree by construction.
  showScreen(FALLBACK_SCREEN);

  loadState().then(function (payload) {
    renderAll();
    // The server names the screen to land on; the frontend does not compute it.
    return routeFromFlow(payload && payload.flow);
  }).then(function () {
    byId('veil').hidden = true;
  }).catch(function (err) {
    console.error('[open-tutor] boot failed', err);
    byId('veil').hidden = true;
    setOffline(true);
    state.data = MOCK_STATE();
    renderAll();
    routeFromFlow(state.data.flow);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
