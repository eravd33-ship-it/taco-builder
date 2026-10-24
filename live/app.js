// Page wiring: microphone -> speech recognition -> translation -> big Hebrew subtitles.
import { LANGUAGES, DEFAULT_LANGUAGE, findLanguage } from './languages.js';
import { createTranslator } from './translate.js';
import { createRecognizer, speechSupported } from './speech.js';
import { createCaptions, toTranscript } from './captions.js';

const $ = (id) => document.getElementById(id);
const STORE = 'live-subtitles:v1';
const DEFAULTS = { lang: DEFAULT_LANGUAGE, size: 36, orig: true, engine: 'auto', key: '', live: false };
const SIZE_MIN = 20;
const SIZE_MAX = 72;
const SIZE_STEP = 4;
const VISIBLE_LINES = 6;
const NOTICE_REPEAT_MS = 15000;

// Latin names inside Hebrew sentences need isolating, or the punctuation around them jumps to the wrong side.
const ltr = (name) => `\u2068${name}\u2069`;

const SPEECH_ERRORS = {
  unsupported: `הדפדפן הזה לא תומך בזיהוי דיבור. ב-${ltr('Android')} כדאי להשתמש ב-${ltr('Chrome')}, וב-${ltr('iPhone')} ב-${ltr('Safari')}.`,
  'not-allowed': 'אין הרשאה למיקרופון. אשרו גישה למיקרופון בהגדרות הדפדפן ונסו שוב.',
  'service-not-allowed': 'אין הרשאה למיקרופון. אשרו גישה למיקרופון בהגדרות הדפדפן ונסו שוב.',
  'audio-capture': 'לא נמצא מיקרופון במכשיר.',
  'language-not-supported': 'שפת הדיבור הזו לא נתמכת בדפדפן. נסו שפה אחרת.',
  network: 'זיהוי הדיבור צריך חיבור לאינטרנט. בדקו את החיבור ונסו שוב.',
  unstable: 'זיהוי הדיבור נעצר שוב ושוב. נסו להפעיל מחדש, או לסגור אפליקציות אחרות שמשתמשות במיקרופון.',
};
const TRANSLATE_ERRORS = {
  quota: `נגמרה המכסה של שירות התרגום להיום, ולכן מוצג הטקסט המקורי. אפשר להוסיף מפתח ${ltr('Google')} בהגדרות.`,
  auth: `מפתח ${ltr('Google')} לא תקין או לא מורשה לאתר הזה. בדקו אותו בהגדרות.`,
  unsupported: 'התרגום במכשיר לא זמין בדפדפן הזה. בחרו מנוע תרגום אחר בהגדרות.',
  loading: 'מודל התרגום במכשיר עדיין נטען, וזה יכול לקחת כמה דקות. בינתיים מוצג הטקסט המקורי.',
  network: 'אין חיבור לשירות התרגום, ולכן מוצג הטקסט המקורי.',
  failed: 'התרגום נכשל כרגע, ולכן מוצג הטקסט המקורי.',
};

// Shown by the "demo" button so the look can be seen without a microphone or a network.
const DEMO = [
  ['Hi everyone, welcome to the show.', 'שלום לכולם, ברוכים הבאים לתוכנית.'],
  ["Today we're going to talk about building an app in a single afternoon.", 'היום נדבר על בניית אפליקציה בתוך אחר צהריים אחד.'],
  ['The subtitles appear here as soon as someone speaks.', 'הכתוביות מופיעות כאן ברגע שמישהו מדבר.'],
  ['You can change the language at the top and make the text bigger.', 'אפשר להחליף שפה למעלה ולהגדיל את הטקסט.'],
];
const demoText = new Map(DEMO);

function loadSettings() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch { /* private mode: defaults */ }
  const s = { ...DEFAULTS, ...saved };
  s.lang = findLanguage(s.lang).code;
  s.size = Math.min(SIZE_MAX, Math.max(SIZE_MIN, Number(s.size) || DEFAULTS.size));
  return s;
}
function saveSettings() {
  try { localStorage.setItem(STORE, JSON.stringify(settings)); } catch { /* not saved */ }
}

const settings = loadSettings();
const els = Object.fromEntries(
  ['lang', 'status', 'status-text', 'empty', 'lines', 'live', 'notice', 'notice-text', 'notice-close', 'btn-mic', 'btn-smaller', 'btn-bigger', 'btn-orig',
    'btn-transcript', 'btn-settings', 'btn-demo', 'btn-clear', 'btn-copy', 'btn-save', 'dlg-settings', 'dlg-transcript', 'transcript', 'set-engine', 'set-key', 'set-orig', 'set-live']
    .map((id) => [id, $(id)]),
);

let running = false;
let demoToken = 0;
const demoOn = () => demoToken !== 0;

const translator = createTranslator({ engine: settings.engine, googleKey: settings.key.trim() });
const captions = createCaptions({
  translate: (text, lang) => (demoOn() ? Promise.resolve(demoText.get(text) ?? text) : translator.translate(text, lang)),
  getLang: () => settings.lang,
  isLive: () => !demoOn() && (settings.live || translator.active === 'device'),
  onChange: render,
  onError: (err) => notice(TRANSLATE_ERRORS[err.kind] || TRANSLATE_ERRORS.failed, true),
});

const codeOfTag = (tag) => (LANGUAGES.find((l) => l.speech === tag) || findLanguage(settings.lang)).code;

const recognizer = speechSupported()
  ? createRecognizer({
      lang: findLanguage(settings.lang).speech,
      onInterim: (text, tag) => captions.setInterim(text, codeOfTag(tag)),
      onFinal: (text, tag) => captions.commit(text, codeOfTag(tag)),
      onState: (state) => {
        running = state === 'listening';
        if (!running) unlockScreen();
        updateControls();
      },
      onError: (code) => notice(SPEECH_ERRORS[code] || SPEECH_ERRORS.unstable),
    })
  : null;

/* ---------- rendering ---------- */

function sourceSpan(text, lang, cls) {
  const span = document.createElement('span');
  span.className = cls;
  span.dir = 'auto';
  if (lang) span.lang = lang;
  span.textContent = text;
  return span;
}

function hebrewSpan(text) {
  const span = document.createElement('span');
  span.className = 'he';
  span.dir = 'rtl';
  span.textContent = text;
  return span;
}

function lineElement(line) {
  const p = document.createElement('p');
  p.className = `line ${line.status === 'pending' ? 'pending' : line.status === 'error' ? 'failed' : ''}`.trim();
  if (line.status === 'done') {
    p.append(hebrewSpan(line.he));
    if (settings.orig) p.append(sourceSpan(line.src, line.lang, 'orig'));
  } else {
    p.append(sourceSpan(line.src, line.lang, 'he src-only'));
  }
  return p;
}

function render({ lines, interim }) {
  els.lines.replaceChildren(...lines.slice(-VISIBLE_LINES).map(lineElement));

  const liveVisible = Boolean(interim.src) || (running && lines.length === 0) || (demoOn() && lines.length === 0);
  els.live.hidden = !liveVisible;
  els.live.classList.toggle('wait', Boolean(interim.he));
  if (interim.src && interim.he) {
    els.live.replaceChildren(hebrewSpan(interim.he), ...(settings.orig ? [sourceSpan(interim.src, settings.lang, 'orig')] : []));
  } else if (interim.src) {
    els.live.replaceChildren(sourceSpan(interim.src, settings.lang, 'he src-only'));
  } else if (liveVisible) {
    const hint = document.createElement('span');
    hint.className = 'listening-hint';
    hint.textContent = 'מקשיב… אפשר להתחיל לדבר';
    els.live.replaceChildren(hint);
  }

  const kids = [...els.lines.children];
  kids.forEach((kid, i) => kid.classList.toggle('dim', liveVisible || i < kids.length - 1));
  els.empty.hidden = lines.length > 0 || liveVisible || running || demoOn();
}

function updateControls() {
  const active = running || demoOn();
  els['btn-mic'].setAttribute('aria-pressed', String(active));
  els['btn-mic'].setAttribute('aria-label', active ? 'עצירה' : 'התחלת האזנה');
  const state = demoOn() ? 'demo' : running ? 'listening' : 'idle';
  els.status.dataset.state = state;
  els['status-text'].textContent = { demo: 'הדגמה', listening: 'מאזין', idle: 'מוכן' }[state];
  els['btn-smaller'].disabled = settings.size <= SIZE_MIN;
  els['btn-bigger'].disabled = settings.size >= SIZE_MAX;
  els['btn-orig'].setAttribute('aria-pressed', String(settings.orig));
  render({ lines: captions.lines, interim: captions.interim });
}

function applySize() {
  document.documentElement.style.setProperty('--fs', `${settings.size}px`);
  updateControls();
}

/* ---------- notices ---------- */

const shownAt = new Map();
function notice(text, throttle = false) {
  const now = Date.now();
  if (throttle && now - (shownAt.get(text) || 0) < NOTICE_REPEAT_MS) return;
  shownAt.set(text, now);
  els['notice-text'].textContent = text;
  els.notice.hidden = false;
}
const hideNotice = () => { els.notice.hidden = true; };

/* ---------- keep the screen on while listening ---------- */

let wakeLock = null;
async function lockScreen() {
  try {
    wakeLock = await navigator.wakeLock?.request('screen');
    wakeLock?.addEventListener('release', () => { wakeLock = null; });
  } catch { /* not allowed or not supported: the screen may dim */ }
}
function unlockScreen() {
  wakeLock?.release().catch(() => {});
  wakeLock = null;
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden || !running) return;
  lockScreen();
  recognizer?.resume();
});

/* ---------- start, stop, demo ---------- */

function syncTranslator() {
  translator.setOptions({ engine: settings.engine, googleKey: settings.key.trim() });
}

function start() {
  if (!recognizer) {
    notice(SPEECH_ERRORS.unsupported);
    return;
  }
  stopDemo();
  hideNotice();
  syncTranslator();
  translator.prepare(settings.lang); // inside the tap, so Chrome may download its translation model
  recognizer.setLang(findLanguage(settings.lang).speech);
  recognizer.start();
  running = true;
  lockScreen();
  updateControls();
}

function stop() {
  recognizer?.stop();
  captions.setInterim('');
  running = false;
  unlockScreen();
  updateControls();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runDemo(token) {
  while (demoToken === token) {
    for (const [text] of DEMO) {
      const words = text.split(' ');
      for (let i = 1; i <= words.length; i++) {
        if (demoToken !== token) return;
        captions.setInterim(words.slice(0, i).join(' '));
        await sleep(230);
      }
      await sleep(350);
      if (demoToken !== token) return;
      captions.commit(text);
      await sleep(1700);
    }
  }
}

function startDemo() {
  if (running) stop();
  hideNotice();
  captions.clear();
  demoToken = Date.now();
  updateControls();
  runDemo(demoToken);
}

function stopDemo() {
  if (!demoOn()) return;
  demoToken = 0;
  captions.clear();
  updateControls();
}

/* ---------- transcript ---------- */

function openTranscript() {
  const box = els.transcript;
  const lines = captions.lines;
  if (!lines.length) {
    const note = document.createElement('p');
    note.className = 'empty-note';
    note.textContent = 'עוד אין כתוביות. אחרי שמישהו ידבר, התמלול המלא יופיע כאן.';
    box.replaceChildren(note);
  } else {
    box.replaceChildren(...lines.map((line) => {
      const item = document.createElement('div');
      item.className = 'item';
      const done = line.status === 'done';
      item.append(done ? hebrewSpan(line.he) : sourceSpan(line.src, line.lang, 'he'));
      if (done) item.append(document.createElement('br'), sourceSpan(line.src, line.lang, 'orig'));
      return item;
    }));
    box.scrollTop = box.scrollHeight;
  }
  els['dlg-transcript'].showModal();
}

async function copyTranscript() {
  const text = toTranscript(captions.lines);
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  els['btn-copy'].textContent = 'הועתק ✓';
  setTimeout(() => { els['btn-copy'].textContent = 'העתקה'; }, 1800);
}

function saveTranscript() {
  const text = toTranscript(captions.lines);
  if (!text) return;
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([`﻿${text}\n`], { type: 'text/plain;charset=utf-8' }));
  link.download = `subtitles-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.txt`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

/* ---------- events ---------- */

for (const l of LANGUAGES) {
  const option = document.createElement('option');
  option.value = l.code;
  option.textContent = `${l.he} · ${l.native}`;
  els.lang.append(option);
}
els.lang.value = settings.lang;
els.lang.addEventListener('change', () => {
  settings.lang = els.lang.value;
  saveSettings();
  captions.setInterim('');
  if (running) {
    translator.prepare(settings.lang);
    recognizer.setLang(findLanguage(settings.lang).speech);
  }
});

els['btn-mic'].addEventListener('click', () => (demoOn() ? stopDemo() : running ? stop() : start()));
els['btn-demo'].addEventListener('click', startDemo);
els['notice-close'].addEventListener('click', hideNotice);
els['btn-smaller'].addEventListener('click', () => { settings.size = Math.max(SIZE_MIN, settings.size - SIZE_STEP); saveSettings(); applySize(); });
els['btn-bigger'].addEventListener('click', () => { settings.size = Math.min(SIZE_MAX, settings.size + SIZE_STEP); saveSettings(); applySize(); });
els['btn-orig'].addEventListener('click', () => { settings.orig = !settings.orig; els['set-orig'].checked = settings.orig; saveSettings(); updateControls(); });
els['btn-transcript'].addEventListener('click', openTranscript);
els['btn-copy'].addEventListener('click', copyTranscript);
els['btn-save'].addEventListener('click', saveTranscript);
els['btn-settings'].addEventListener('click', () => els['dlg-settings'].showModal());
els['btn-clear'].addEventListener('click', () => { captions.clear(); els['dlg-settings'].close(); });

els['set-engine'].value = settings.engine;
els['set-key'].value = settings.key;
els['set-orig'].checked = settings.orig;
els['set-live'].checked = settings.live;
els['set-engine'].addEventListener('change', () => { settings.engine = els['set-engine'].value; saveSettings(); syncTranslator(); });
els['set-key'].addEventListener('change', () => { settings.key = els['set-key'].value.trim(); saveSettings(); syncTranslator(); });
els['set-orig'].addEventListener('change', () => { settings.orig = els['set-orig'].checked; saveSettings(); updateControls(); });
els['set-live'].addEventListener('change', () => { settings.live = els['set-live'].checked; saveSettings(); });

for (const dialog of [els['dlg-settings'], els['dlg-transcript']]) {
  dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); }); // tap outside the sheet
}

applySize();

// Only exists on https and localhost, which is also where the microphone works.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}
