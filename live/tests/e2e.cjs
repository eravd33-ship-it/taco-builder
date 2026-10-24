// End-to-end check in a real (headless) Chromium with a phone-sized screen.
// The browser's speech recogniser and the translation service are replaced with scripted fakes.
// Needs Playwright:  npm i -g playwright && npx playwright install chromium   then:  node tests/e2e.cjs [screenshot-dir]
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const SITE_ROOT = path.resolve(__dirname, '..', '..');
const SHOTS = process.argv[2] ? path.resolve(process.argv[2]) : null;
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.png': 'image/png', '.svg': 'image/svg+xml' };

const HEBREW = {
  'hello world': 'שלום עולם',
  'how are you today': 'מה שלומך היום',
  'the weather is lovely': 'מזג האוויר נפלא',
  'we are going to the beach': 'אנחנו הולכים לים',
  'bring a towel and some water': 'תביאו מגבת וקצת מים',
  'see you there': 'נתראה שם',
  'thank you very much': 'תודה רבה',
  'مرحبا': 'שלום',
};

function serve() {
  const server = http.createServer((req, res) => {
    const file = path.join(SITE_ROOT, decodeURIComponent(new URL(req.url, 'http://x').pathname));
    if (!file.startsWith(SITE_ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      const index = path.join(file, 'index.html');
      if (file.startsWith(SITE_ROOT) && fs.existsSync(index)) return (res.setHeader('content-type', 'text/html'), res.end(fs.readFileSync(index)));
      res.statusCode = 404;
      return res.end('not found');
    }
    res.setHeader('content-type', TYPES[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

const FAKE_SPEECH = () => {
  window.__recs = [];
  class FakeRecognition {
    constructor() { window.__recs.push(this); this.started = false; }
    start() { this.started = true; setTimeout(() => this.onstart && this.onstart(), 0); }
    stop() { this.started = false; setTimeout(() => this.onend && this.onend(), 0); }
  }
  window.SpeechRecognition = window.webkitSpeechRecognition = FakeRecognition; // recent Chromium has both names
  // Deliver one recognition event the way browsers do: the whole result list of the session, with resultIndex marking what changed.
  // parts is [[text, isFinal], ...] placed from `index` on.
  window.__say = (parts, index = 0) => {
    const rec = window.__recs[window.__recs.length - 1];
    rec.list = rec.list || [];
    parts.forEach(([transcript, isFinal], i) => { rec.list[index + i] = Object.assign([{ transcript }], { isFinal }); });
    rec.onresult({ resultIndex: index, results: rec.list.slice() });
  };
  window.__fail = (error) => { const rec = window.__recs[window.__recs.length - 1]; rec.onerror({ error }); rec.onend(); };
};

async function translationRoute(context, { quota = false, log = [], pairs = [] } = {}) {
  await context.route('https://api.mymemory.translated.net/**', (route) => {
    const q = new URL(route.request().url()).searchParams.get('q');
    log.push(q);
    pairs.push(`${q}|${new URL(route.request().url()).searchParams.get('langpair')}`);
    const body = quota
      ? { responseData: { translatedText: 'MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY' }, quotaFinished: true, responseStatus: 200 }
      : { responseData: { translatedText: HEBREW[q] || `תרגום: ${q}` }, quotaFinished: false, responseStatus: 200 };
    route.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(body) });
  });
}

(async () => {
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}/live/`;
  const browser = await chromium.launch();
  const phone = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'he-IL', serviceWorkers: 'allow' };
  const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, `${name}.png`) }); } };
  const text = (page, sel) => page.locator(sel).innerText();
  let passed = 0;
  const step = async (name, fn) => { await fn(); passed++; console.log(`ok - ${name}`); };

  const context = await browser.newContext(phone);
  await context.addInitScript(FAKE_SPEECH);
  const requests = [];
  const pairs = [];
  await translationRoute(context, { log: requests, pairs });
  const page = await context.newPage();
  const problems = [];
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`); });
  await page.goto(base);

  await step('opens with the instructions and a language list', async () => {
    assert.match(await text(page, '#empty h1'), /כתוביות בעברית/);
    assert.equal(await page.locator('#lang option').count(), 17);
    assert.equal(await page.locator('#lang').inputValue(), 'en');
    assert.equal(await page.locator('#btn-mic').getAttribute('aria-pressed'), 'false');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await shot(page, '1-empty');
  });

  await step('the microphone starts listening in the chosen language', async () => {
    await page.click('#btn-mic');
    await page.waitForFunction(() => window.__recs.length === 1 && window.__recs[0].started);
    assert.equal(await page.evaluate(() => window.__recs[0].lang), 'en-US');
    assert.equal(await page.evaluate(() => window.__recs[0].continuous && window.__recs[0].interimResults), true);
    assert.equal(await page.locator('#btn-mic').getAttribute('aria-pressed'), 'true');
    assert.equal(await text(page, '#status-text'), 'מאזין');
    assert.match(await text(page, '#live'), /מקשיב/);
    assert.equal(await page.locator('#empty').isHidden(), true);
    await shot(page, '2-listening');
  });

  await step('words show as they are spoken, then become Hebrew subtitles with the original underneath', async () => {
    await page.evaluate(() => window.__say([['hello wor', false]]));
    assert.equal(await text(page, '#live'), 'hello wor');
    await page.evaluate(() => window.__say([['hello world', true]]));
    await page.waitForSelector('#lines .line .he:text("שלום עולם")');
    assert.equal(await text(page, '#lines .line .orig'), 'hello world');
    assert.equal(await page.locator('#live').isHidden(), true);
    assert.equal(await page.locator('#lines .line .he').first().getAttribute('dir'), 'rtl');
    assert.deepEqual(requests, ['hello world']);
    await shot(page, '3-first-line');
  });

  await step('a conversation fills the screen, newest at the bottom, older lines dimmed', async () => {
    const phrases = ['how are you today', 'the weather is lovely', 'we are going to the beach', 'bring a towel and some water', 'see you there'];
    for (let i = 0; i < phrases.length; i++) {
      await page.evaluate(([p, idx]) => window.__say([[p, true]], idx), [phrases[i], i + 1]);
    }
    await page.evaluate(() => window.__say([['thank you very', false]], 6));
    await page.waitForSelector('#lines .line .he:text("נתראה שם")');
    assert.equal(await text(page, '#live'), 'thank you very');
    const box = await page.evaluate(() => {
      const r = (s) => document.querySelector(s).getBoundingClientRect();
      return { stage: r('#stage'), live: r('#live'), lines: r('#lines') };
    });
    assert.ok(box.live.bottom <= box.stage.bottom + 1 && box.live.top >= box.stage.top, 'live line is inside the stage');
    assert.ok(box.lines.bottom <= box.live.top + 1, 'finished lines sit above the live line');
    assert.ok(await page.locator('#lines .line.dim').count() >= 5);
    assert.ok(await page.locator('#lines .line').count() <= 6);
    await shot(page, '4-conversation');
  });

  await step('bigger text, and hiding the originals', async () => {
    await page.click('#btn-bigger');
    await page.click('#btn-bigger');
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--fs').trim()), '44px');
    await page.click('#btn-orig');
    assert.equal(await page.locator('#lines .orig').count(), 0);
    assert.equal(await page.locator('#btn-orig').getAttribute('aria-pressed'), 'false');
    await shot(page, '5-big-no-original');
    await page.click('#btn-orig');
    assert.ok(await page.locator('#lines .orig').count() > 0);
  });

  await step('changing the language restarts listening in that language', async () => {
    await page.selectOption('#lang', 'ar');
    await page.waitForFunction(() => window.__recs.length === 2 && window.__recs[1].started);
    assert.equal(await page.evaluate(() => window.__recs[1].lang), 'ar-SA');
    await page.evaluate(() => window.__say([['مرحبا', true]]));
    await page.waitForSelector('#lines .line .he:text("שלום")');
    assert.ok(requests.includes('مرحبا'));
  });

  await step('the transcript lists every line and can be copied', async () => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: new URL(base).origin });
    await page.click('#btn-transcript');
    await page.waitForSelector('#dlg-transcript[open]');
    assert.equal(await page.locator('#transcript .item').count(), 8); // includes the sentence cut off by switching language
    assert.ok(pairs.includes('thank you very|en|he'), 'the cut-off sentence was still translated from English');
    assert.ok(pairs.includes('مرحبا|ar|he'));
    await shot(page, '6-transcript');
    await page.click('#btn-copy');
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    assert.match(copied, /^שלום עולם\n\(hello world\)\n\nמה שלומך היום\n\(how are you today\)/);
    await page.click('#dlg-transcript button[value=close]');
  });

  await step('settings are saved and survive a reload', async () => {
    await page.click('#btn-settings');
    await page.waitForSelector('#dlg-settings[open]');
    await page.selectOption('#set-engine', 'mymemory');
    await page.fill('#set-key', 'AIza-test');
    await shot(page, '7-settings');
    await page.click('#dlg-settings button[value=close]');
    await page.reload();
    assert.equal(await page.locator('#lang').inputValue(), 'ar');
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--fs').trim()), '44px');
    await page.click('#btn-settings');
    assert.equal(await page.locator('#set-engine').inputValue(), 'mymemory');
    assert.equal(await page.locator('#set-key').inputValue(), 'AIza-test');
    await page.click('#dlg-settings button[value=close]');
  });

  await step('clear and stop', async () => {
    await page.click('#btn-settings');
    await page.click('#btn-clear');
    assert.equal(await page.locator('#lines .line').count(), 0);
    assert.equal(await page.locator('#empty').isVisible(), true);
  });

  await step('the app works offline once it has been opened (service worker)', async () => {
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload();
    await page.waitForFunction(() => navigator.serviceWorker.controller);
    await context.setOffline(true);
    await page.reload();
    assert.match(await text(page, '#empty h1'), /כתוביות בעברית/);
    await context.setOffline(false);
  });

  await step('the manifest and every icon it names are served', async () => {
    const manifest = await (await page.request.get(`${base}manifest.webmanifest`)).json();
    assert.equal(manifest.dir, 'rtl');
    for (const icon of manifest.icons) assert.equal((await page.request.get(new URL(icon.src, base).href)).status(), 200, icon.src);
    assert.equal((await page.request.get(`${base}icons/apple-touch-icon.png`)).status(), 200);
  });

  await step('the demo plays without a microphone or network, and stopping clears it', async () => {
    const demoPage = await (await browser.newContext({ ...phone, serviceWorkers: 'block' })).newPage();
    const offlineRequests = [];
    demoPage.on('request', (r) => { if (!r.url().startsWith(base)) offlineRequests.push(r.url()); });
    await demoPage.goto(base);
    await demoPage.click('#btn-demo');
    assert.equal(await demoPage.locator('#status-text').innerText(), 'הדגמה');
    await demoPage.waitForSelector('#lines .line .he:text("שלום לכולם")', { timeout: 8000 });
    await demoPage.waitForSelector('#lines .line .he:text("הכתוביות מופיעות")', { timeout: 15000 });
    await shot(demoPage, '8-demo');
    await demoPage.click('#btn-mic');
    assert.equal(await demoPage.locator('#lines .line').count(), 0);
    assert.equal(await demoPage.locator('#status-text').innerText(), 'מוכן');
    assert.deepEqual(offlineRequests, []);
    await demoPage.context().close();
  });

  await step('permission and quota problems are explained in Hebrew', async () => {
    const ctx = await browser.newContext(phone);
    await ctx.addInitScript(FAKE_SPEECH);
    await translationRoute(ctx, { quota: true });
    const p = await ctx.newPage();
    await p.goto(base);
    await p.click('#btn-mic');
    await p.waitForFunction(() => window.__recs.length === 1);
    await p.evaluate(() => window.__say([['hello world', true]]));
    await p.waitForSelector('#notice:not([hidden])');
    assert.match(await text(p, '#notice-text'), /נגמרה המכסה/);
    assert.equal(await p.locator('#lines .line.failed').count(), 1);
    assert.equal(await text(p, '#lines .line .he'), 'hello world');
    await shot(p, '9-quota');
    await p.click('#notice-close');
    await p.evaluate(() => window.__fail('not-allowed'));
    await p.waitForSelector('#notice:not([hidden])');
    assert.match(await text(p, '#notice-text'), /הרשאה למיקרופון/);
    assert.equal(await p.locator('#btn-mic').getAttribute('aria-pressed'), 'false');
    await shot(p, '10-denied');
    await ctx.close();
  });

  await step('a browser without speech recognition gets a clear message instead of a dead button', async () => {
    const ctx = await browser.newContext(phone);
    const p = await ctx.newPage();
    await p.addInitScript(() => { delete window.webkitSpeechRecognition; delete window.SpeechRecognition; });
    await p.goto(base);
    await p.click('#btn-mic');
    assert.match(await text(p, '#notice-text'), /לא תומך בזיהוי דיבור/);
    await ctx.close();
  });

  await step('with fast translation on, the sentence being spoken is translated before it ends', async () => {
    const ctx = await browser.newContext(phone);
    await ctx.addInitScript(FAKE_SPEECH);
    await translationRoute(ctx);
    const p = await ctx.newPage();
    await p.goto(base);
    await p.click('#btn-settings');
    await p.check('#set-live');
    await p.click('#dlg-settings button[value=close]');
    await p.click('#btn-mic');
    await p.waitForFunction(() => window.__recs.length === 1);
    await p.evaluate(() => window.__say([['hello world', false]]));
    await p.waitForSelector('#live .he:not(.src-only)', { timeout: 4000 });
    assert.equal(await text(p, '#live .he'), 'שלום עולם');
    await shot(p, '11-live-translation');
    await ctx.close();
  });

  await step('the downloadable single file works when opened straight from disk', async () => {
    const ctx = await browser.newContext(phone);
    await ctx.addInitScript(FAKE_SPEECH);
    const outside = [];
    await translationRoute(ctx);
    const p = await ctx.newPage();
    const errors = [];
    p.on('pageerror', (e) => errors.push(e.message));
    p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    p.on('request', (r) => { if (!r.url().startsWith('file:') && !r.url().startsWith('data:')) outside.push(r.url()); });
    await p.goto(`file://${path.resolve(__dirname, '..', 'live-subtitles.html')}`);
    assert.match(await text(p, '#empty h1'), /כתוביות בעברית/);
    assert.equal(await p.evaluate(() => document.querySelector('.brand img').complete && document.querySelector('.brand img').naturalWidth > 0), true);
    assert.equal(await p.locator('#btn-settings').count(), 1);
    await p.click('#btn-settings');
    assert.equal(await p.locator('#dlg-settings a[download]').count(), 0, 'no link to itself');
    await p.click('#dlg-settings button[value=close]');
    await p.click('#btn-mic');
    await p.waitForFunction(() => window.__recs.length === 1);
    await p.evaluate(() => window.__say([['hello world', true]]));
    await p.waitForSelector('#lines .line .he:text("שלום עולם")');
    assert.deepEqual(errors, []);
    assert.deepEqual(outside, ['https://api.mymemory.translated.net/get?q=hello%20world&langpair=en%7Che']);
    await shot(p, '12-single-file');
    await ctx.close();
  });

  assert.deepEqual(problems, [], 'no page errors or console errors');
  console.log(`\n${passed} steps passed`);
  await browser.close();
  server.close();
})().catch((err) => { console.error(err); process.exit(1); });
