import test from 'node:test';
import assert from 'node:assert/strict';
import { createTranslator, splitForLimit, decodeEntities, TranslateError } from '../translate.js';

const json = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const myMemory = (text, extra = {}) => json({ responseData: { translatedText: text }, responseStatus: 200, quotaFinished: false, ...extra });

function recorder(handler) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url: String(url), init });
    return handler(String(url), init, calls.length);
  };
  return { fetchImpl, calls };
}

test('splitForLimit keeps short text whole and splits long text at sentence or word edges', () => {
  assert.deepEqual(splitForLimit('hello there'), ['hello there']);
  const long = ('This is a sentence. ').repeat(40).trim();
  const parts = splitForLimit(long);
  assert.ok(parts.length > 1);
  assert.ok(parts.every((p) => p.length <= 450));
  assert.equal(parts.join(' '), long);
  assert.ok(parts.slice(0, -1).every((p) => p.endsWith('.')));
});

test('splitForLimit uses a smaller limit for non-Latin text and cuts text without spaces', () => {
  const arabic = 'مرحبا '.repeat(60).trim();
  assert.ok(splitForLimit(arabic).every((p) => p.length <= 150));
  const cjk = '你好'.repeat(200);
  const parts = splitForLimit(cjk);
  assert.ok(parts.length > 1 && parts.every((p) => p.length <= 150));
  assert.equal(parts.join(''), cjk);
});

test('decodeEntities', () => {
  assert.equal(decodeEntities('it&#39;s &quot;fine&quot; &amp; &lt;ok&gt; &#8211; &#x27;'), 'it\'s "fine" & <ok> – \'');
});

test('mymemory: sends the language pair and returns the translation', async () => {
  const { fetchImpl, calls } = recorder(() => myMemory('שלום עולם'));
  const t = createTranslator({ engine: 'mymemory', fetchImpl, scope: {} });
  assert.equal(await t.translate('hello world', 'en'), 'שלום עולם');
  const url = new URL(calls[0].url);
  assert.equal(url.hostname, 'api.mymemory.translated.net');
  assert.equal(url.searchParams.get('q'), 'hello world');
  assert.equal(url.searchParams.get('langpair'), 'en|he');
});

test('mymemory: Chinese uses its regional code', async () => {
  const { fetchImpl, calls } = recorder(() => myMemory('שלום'));
  await createTranslator({ engine: 'mymemory', fetchImpl, scope: {} }).translate('你好', 'zh');
  assert.equal(new URL(calls[0].url).searchParams.get('langpair'), 'zh-CN|he');
});

test('mymemory: a long text goes out in several requests and is joined in order', async () => {
  const { fetchImpl, calls } = recorder((url) => myMemory(`<${new URL(url).searchParams.get('q').length}>`));
  const text = ('word '.repeat(300)).trim();
  const out = await createTranslator({ engine: 'mymemory', fetchImpl, scope: {} }).translate(text, 'en');
  assert.ok(calls.length >= 4);
  assert.equal(out.split(' ').length, calls.length);
});

test('mymemory: quota and warning texts are reported as quota, other failures as failed', async () => {
  for (const reply of [
    myMemory('x', { quotaFinished: true }),
    myMemory('MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY.', { responseStatus: 429 }),
    json({}, 429),
  ]) {
    const { fetchImpl } = recorder(() => reply);
    await assert.rejects(createTranslator({ engine: 'mymemory', fetchImpl, scope: {} }).translate('hi', 'en'), { kind: 'quota' });
  }
  const { fetchImpl } = recorder(() => json({ responseData: { translatedText: 'x' }, responseStatus: '403' }));
  await assert.rejects(createTranslator({ engine: 'mymemory', fetchImpl, scope: {} }).translate('hi', 'en'), { kind: 'failed' });
});

test('network errors are reported as network', async () => {
  const fetchImpl = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(createTranslator({ engine: 'mymemory', fetchImpl, scope: {} }).translate('hi', 'en'), { kind: 'network' });
});

test('google: posts the text with the key and returns the translation', async () => {
  const { fetchImpl, calls } = recorder(() => json({ data: { translations: [{ translatedText: 'שלום' }] } }));
  const t = createTranslator({ engine: 'google', googleKey: 'K 1', fetchImpl, scope: {} });
  assert.equal(await t.translate('hello', 'en'), 'שלום');
  assert.equal(calls[0].url, 'https://translation.googleapis.com/language/translate/v2?key=K%201');
  assert.deepEqual(JSON.parse(calls[0].init.body), { q: 'hello', source: 'en', target: 'he', format: 'text' });
});

test('google: bad key, quota and missing key', async () => {
  const bad = recorder(() => json({ error: { code: 400, message: 'API key not valid. Please pass a valid API key.' } }, 400));
  await assert.rejects(createTranslator({ engine: 'google', googleKey: 'x', fetchImpl: bad.fetchImpl, scope: {} }).translate('a', 'en'), { kind: 'auth' });
  const denied = recorder(() => json({ error: { code: 403, message: 'Requests from referer are blocked.' } }, 403));
  await assert.rejects(createTranslator({ engine: 'google', googleKey: 'x', fetchImpl: denied.fetchImpl, scope: {} }).translate('a', 'en'), { kind: 'auth' });
  const limited = recorder(() => json({ error: { code: 403, errors: [{ reason: 'dailyLimitExceeded' }] } }, 403));
  await assert.rejects(createTranslator({ engine: 'google', googleKey: 'x', fetchImpl: limited.fetchImpl, scope: {} }).translate('a', 'en'), { kind: 'quota' });
  await assert.rejects(createTranslator({ engine: 'google', fetchImpl: bad.fetchImpl, scope: {} }).translate('a', 'en'), { kind: 'auth' });
});

function fakeTranslatorScope({ availability = 'available', translate = (t) => `he:${t}` } = {}) {
  const created = [];
  class Translator {
    static async availability(opts) { return typeof availability === 'function' ? availability(opts) : availability; }
    static async create(opts) {
      created.push(opts);
      return { translate: async (t) => translate(t) };
    }
  }
  return { scope: { Translator }, created };
}

test('device: uses the browser translator, creating it once per language', async () => {
  const { scope, created } = fakeTranslatorScope();
  const { fetchImpl, calls } = recorder(() => myMemory('should not be used'));
  const t = createTranslator({ engine: 'auto', fetchImpl, scope });
  assert.equal(await t.translate('hello', 'en'), 'he:hello');
  assert.equal(await t.translate('bye', 'en'), 'he:bye');
  assert.equal(created.length, 1);
  assert.deepEqual([created[0].sourceLanguage, created[0].targetLanguage], ['en', 'he']);
  assert.equal(calls.length, 0);
  assert.equal(t.active, 'device');
});

test('auto: falls back to the next engine, and stops retrying one that can never work', async () => {
  let asked = 0;
  const { scope } = fakeTranslatorScope({ availability: () => { asked++; return 'unavailable'; } });
  const { fetchImpl, calls } = recorder(() => json({ data: { translations: [{ translatedText: 'מגוגל' }] } }));
  const t = createTranslator({ engine: 'auto', googleKey: 'k', fetchImpl, scope });
  assert.equal(await t.translate('one', 'en'), 'מגוגל');
  assert.equal(await t.translate('two', 'en'), 'מגוגל');
  assert.equal(asked, 1);
  assert.equal(calls.length, 2);
  assert.equal(t.active, 'google');
});

test('auto: order is device, google (only with a key), then mymemory; quota pauses an engine until options change', async () => {
  let now = 0;
  const hits = [];
  const fetchImpl = async (url) => {
    hits.push(new URL(url).hostname);
    return url.includes('googleapis') ? json({}, 429) : myMemory('חינמי');
  };
  const t = createTranslator({ engine: 'auto', googleKey: 'k', fetchImpl, scope: {}, now: () => now });
  assert.equal(await t.translate('a', 'en'), 'חינמי');
  assert.equal(await t.translate('b', 'en'), 'חינמי');
  assert.deepEqual(hits, ['translation.googleapis.com', 'api.mymemory.translated.net', 'api.mymemory.translated.net']);
  now = 11 * 60 * 1000;
  await t.translate('c', 'en');
  assert.equal(hits[3], 'translation.googleapis.com');
  hits.length = 0;
  t.setOptions({ googleKey: '' });
  await t.translate('d', 'en');
  assert.deepEqual(hits, ['api.mymemory.translated.net']);
});

test('a chosen engine is the only one used, and its failure is reported', async () => {
  const { fetchImpl, calls } = recorder(() => json({}, 500));
  await assert.rejects(createTranslator({ engine: 'google', googleKey: 'k', fetchImpl, scope: {} }).translate('a', 'en'), { kind: 'failed' });
  assert.equal(calls.length, 1);
  await assert.rejects(createTranslator({ engine: 'device', fetchImpl, scope: {} }).translate('a', 'en'), { kind: 'unsupported' });
  assert.equal(calls.length, 1);
});

test('results are cached per language and text; empty text is not sent', async () => {
  const { fetchImpl, calls } = recorder(() => myMemory('שלום'));
  const t = createTranslator({ engine: 'mymemory', fetchImpl, scope: {} });
  await t.translate('hello', 'en');
  await t.translate('hello', 'en');
  await t.translate('  ', 'en');
  assert.equal(calls.length, 1);
  await t.translate('hello', 'fr');
  assert.equal(calls.length, 2);
});

test('prepare warms the on-device translator without throwing when it is missing', async () => {
  const { scope, created } = fakeTranslatorScope();
  const t = createTranslator({ scope, fetchImpl: async () => myMemory('x') });
  t.prepare('en');
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(created.length, 1);
  assert.doesNotThrow(() => createTranslator({ scope: {} }).prepare('en'));
  assert.ok(new TranslateError('quota') instanceof Error);
});

test('device: a translator that never answers does not freeze the subtitles; an online engine takes over and the device is skipped for a while', async () => {
  let now = 0;
  const hang = { Translator: { availability: () => new Promise(() => {}), create: () => new Promise(() => {}) } };
  const { fetchImpl, calls } = recorder(() => myMemory('מהרשת'));
  const t = createTranslator({ scope: hang, fetchImpl, now: () => now, deviceWaitMs: 20 });
  const started = Date.now();
  assert.equal(await t.translate('one', 'en'), 'מהרשת');
  assert.ok(Date.now() - started >= 15, 'waited a moment for the device first');
  const second = Date.now();
  assert.equal(await t.translate('two', 'en'), 'מהרשת');
  assert.ok(Date.now() - second < 15, 'then did not wait again');
  assert.equal(calls.length, 2);
  now = 16 * 1000;
  await t.translate('three', 'en');
  assert.equal(calls.length, 3);
});

test('device: once the model finishes loading it is used again straight away, even while paused', async () => {
  let now = 0;
  let finish;
  const model = new Promise((r) => { finish = r; });
  const scope = { Translator: { availability: async () => 'downloadable', create: () => model } };
  const { fetchImpl, calls } = recorder(() => myMemory('מהרשת'));
  const t = createTranslator({ scope, fetchImpl, now: () => now, deviceWaitMs: 10 });
  assert.equal(await t.translate('one', 'en'), 'מהרשת');
  finish({ translate: async (x) => `מהמכשיר:${x}` });
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(await t.translate('two', 'en'), 'מהמכשיר:two');
  assert.equal(calls.length, 1);
});

test('device: a chosen device engine that is still loading says so', async () => {
  const scope = { Translator: { availability: async () => 'downloading', create: () => new Promise(() => {}) } };
  await assert.rejects(createTranslator({ engine: 'device', scope, deviceWaitMs: 10 }).translate('hi', 'en'), { kind: 'loading' });
});

test('device: a translate call that hangs falls back too', async () => {
  const scope = { Translator: { availability: async () => 'available', create: async () => ({ translate: () => new Promise(() => {}) }) } };
  const { fetchImpl } = recorder(() => myMemory('מהרשת'));
  const t = createTranslator({ scope, fetchImpl, deviceTranslateMs: 20 });
  assert.equal(await t.translate('hi', 'en'), 'מהרשת');
});
