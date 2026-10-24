// Translation into Hebrew. Three engines behind one function, with automatic fallback:
//   device    Chrome's built-in on-device translator (free, private, works offline once its model is downloaded)
//   google    Google Cloud Translation, with the user's own API key
//   mymemory  MyMemory's free public API (small daily quota)

export const TARGET = 'he';

export class TranslateError extends Error {
  // kind: 'quota' | 'auth' | 'unsupported' | 'loading' | 'network' | 'failed'
  constructor(kind, message) {
    super(message || kind);
    this.name = 'TranslateError';
    this.kind = kind;
  }
}

const PERMANENT = new Set(['quota', 'auth', 'unsupported']);
const MYMEMORY_CODES = { zh: 'zh-CN' };
const REQUEST_TIMEOUT_MS = 8000;
const CACHE_LIMIT = 300;
const PAUSE_MS = 10 * 60 * 1000; // quota, bad key or unsupported language: don't ask again for a while
const LOADING_PAUSE_MS = 15 * 1000; // on-device model still downloading
const DEVICE_WAIT_MS = 1200; // how long a sentence waits for the on-device translator before an online engine takes over
const DEVICE_TRANSLATE_MS = 5000;

// MyMemory refuses queries over 500 bytes, and non-Latin letters take up to 3 bytes each.
export function splitForLimit(text, max) {
  let rest = text.trim();
  const limit = max || (/[^\x00-\x7f]/.test(rest) ? 150 : 450);
  const out = [];
  while (rest.length > limit) {
    const head = rest.slice(0, limit);
    let cut = Math.max(head.lastIndexOf('. '), head.lastIndexOf('? '), head.lastIndexOf('! '));
    if (cut < limit / 2) cut = head.lastIndexOf(' ');
    if (cut < limit / 2) cut = limit - 1;
    out.push(rest.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1).trim();
  }
  if (rest) out.push(rest);
  return out;
}

const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&#x27;': "'", '&apos;': "'" };
export function decodeEntities(text) {
  return text.replace(/&(?:amp|lt|gt|quot|apos|#39|#x27);|&#(\d+);/g, (m, dec) => (dec ? String.fromCodePoint(Number(dec)) : ENTITIES[m]));
}

// Chrome's on-device translator can take minutes to download its model, or never answer at all.
// Nothing may wait on it for long, or the subtitles would freeze.
function within(promise, ms, kind) {
  let timer;
  const late = new Promise((_, reject) => { timer = setTimeout(() => reject(new TranslateError(kind)), ms); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

async function request(fetchImpl, url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetchImpl(url, { ...init, signal: controller.signal });
  } catch {
    throw new TranslateError('network');
  } finally {
    clearTimeout(timer);
  }
}

async function googleTranslate(fetchImpl, key, text, src) {
  if (!key) throw new TranslateError('auth', 'no Google API key');
  const res = await request(fetchImpl, `https://translation.googleapis.com/language/translate/v2?key=${encodeURIComponent(key)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q: text, source: src, target: TARGET, format: 'text' }),
  });
  if (!res.ok) {
    const detail = JSON.stringify(await res.json().catch(() => ({})));
    if (res.status === 429 || /limit|quota/i.test(detail)) throw new TranslateError('quota');
    if (res.status === 401 || res.status === 403 || /api[_ ]?key|keyInvalid|referer|permission/i.test(detail)) throw new TranslateError('auth');
    throw new TranslateError('failed', `google ${res.status}`);
  }
  const out = (await res.json())?.data?.translations?.[0]?.translatedText;
  if (typeof out !== 'string') throw new TranslateError('failed', 'google: unexpected reply');
  return out;
}

async function myMemoryTranslate(fetchImpl, text, src) {
  const pair = `${MYMEMORY_CODES[src] || src}|${TARGET}`;
  const res = await request(fetchImpl, `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text)}&langpair=${encodeURIComponent(pair)}`);
  if (res.status === 429) throw new TranslateError('quota');
  if (!res.ok) throw new TranslateError('failed', `mymemory ${res.status}`);
  const data = await res.json();
  const out = data?.responseData?.translatedText;
  if (data?.quotaFinished || (typeof out === 'string' && /MYMEMORY WARNING/i.test(out))) throw new TranslateError('quota');
  if (Number(data?.responseStatus) !== 200 || typeof out !== 'string') throw new TranslateError('failed', 'mymemory: unexpected reply');
  return decodeEntities(out);
}

function createDeviceEngine(scope, { onProgress, waitMs, translateMs }) {
  const pending = new Map();
  const ready = new Map();
  function get(src) {
    if (!pending.has(src)) {
      const promise = (async () => {
        const Translator = scope.Translator;
        if (!Translator) throw new TranslateError('unsupported');
        const options = { sourceLanguage: src, targetLanguage: TARGET };
        if ((await Translator.availability(options)) === 'unavailable') throw new TranslateError('unsupported');
        const translator = await Translator.create({
          ...options,
          monitor(m) { m.addEventListener('downloadprogress', (e) => onProgress?.(e.loaded)); },
        });
        ready.set(src, translator);
        return translator;
      })();
      promise.catch(() => pending.delete(src));
      pending.set(src, promise);
    }
    return pending.get(src);
  }
  return {
    available: () => Boolean(scope.Translator),
    ready: (src) => ready.has(src),
    prepare: (src) => get(src),
    async translate(text, src) {
      try {
        const translator = ready.get(src) || (await within(get(src), waitMs, 'loading'));
        return await within(translator.translate(text), translateMs, 'failed');
      } catch (err) {
        throw err instanceof TranslateError ? err : new TranslateError('failed', String(err?.message || err));
      }
    },
  };
}

export function createTranslator({ engine = 'auto', googleKey = '', fetchImpl, scope = globalThis, now = Date.now, onProgress, deviceWaitMs = DEVICE_WAIT_MS, deviceTranslateMs = DEVICE_TRANSLATE_MS } = {}) {
  const doFetch = fetchImpl || ((...args) => scope.fetch(...args));
  const device = createDeviceEngine(scope, { onProgress, waitMs: deviceWaitMs, translateMs: deviceTranslateMs });
  const cache = new Map();
  const pausedUntil = new Map();
  let options = { engine, googleKey };
  let active = '';

  function chain() {
    if (options.engine !== 'auto') return [options.engine];
    return [device.available() && 'device', options.googleKey && 'google', 'mymemory'].filter(Boolean);
  }

  // Device translators depend on the language pair, the others don't.
  const pauseKey = (name, src) => (name === 'device' ? `device:${src}` : name);

  function run(name, text, src) {
    if (name === 'device') return device.translate(text, src);
    if (name === 'google') return googleTranslate(doFetch, options.googleKey, text, src);
    if (name === 'mymemory') return splitForLimit(text).reduce(async (acc, part) => `${await acc} ${await myMemoryTranslate(doFetch, part, src)}`.trim(), '');
    throw new TranslateError('unsupported', `unknown engine ${name}`);
  }

  return {
    setOptions(next) {
      options = { ...options, ...next };
      pausedUntil.clear();
    },
    // Call from a click: Chrome only downloads its on-device model while the user is interacting.
    prepare(src) {
      if (options.engine === 'auto' || options.engine === 'device') device.prepare(src).catch(() => {});
    },
    get active() { return active; },
    async translate(text, src) {
      text = text.trim();
      if (!text) return '';
      const key = `${src}|${text}`;
      if (cache.has(key)) return cache.get(key);
      let last = new TranslateError('unsupported', 'no translation engine available');
      for (const name of chain()) {
        const paused = (pausedUntil.get(pauseKey(name, src)) || 0) > now();
        if (paused && !(name === 'device' && device.ready(src))) continue;
        try {
          const out = (await run(name, text, src)).trim();
          active = name;
          cache.set(key, out);
          if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value);
          return out;
        } catch (err) {
          last = err instanceof TranslateError ? err : new TranslateError('failed', String(err?.message || err));
          if (PERMANENT.has(last.kind)) pausedUntil.set(pauseKey(name, src), now() + PAUSE_MS);
          else if (last.kind === 'loading') pausedUntil.set(pauseKey(name, src), now() + LOADING_PAUSE_MS);
        }
      }
      throw last;
    },
  };
}
