import test from 'node:test';
import assert from 'node:assert/strict';
import { createRecognizer, speechSupported } from '../speech.js';

// A stand-in for the browser's SpeechRecognition that the test drives by hand.
function makeScope() {
  const made = [];
  class FakeRecognition {
    constructor() { made.push(this); this.started = 0; this.stopped = 0; }
    start() { this.started++; queueMicrotask(() => this.onstart?.()); }
    stop() { this.stopped++; queueMicrotask(() => this.onend?.()); }
    say(results, resultIndex = 0) { this.onresult?.({ resultIndex, results }); }
  }
  return { scope: { webkitSpeechRecognition: FakeRecognition }, made };
}

const result = (text, isFinal) => Object.assign([{ transcript: text }], { isFinal });
const tick = () => new Promise((r) => setImmediate(r));

function setup(extra = {}) {
  const { scope, made } = makeScope();
  const log = { interim: [], final: [], state: [], error: [] };
  const timers = [];
  const rec = createRecognizer({
    scope,
    lang: 'en-US',
    onInterim: (t) => log.interim.push(t),
    onFinal: (t, lang) => log.final.push(lang === 'en-US' ? t : `${t} [${lang}]`),
    onState: (s) => log.state.push(s),
    onError: (e) => log.error.push(e),
    setTimer: (fn) => timers.push(fn) && timers.length,
    clearTimer: () => {},
    ...extra,
  });
  const fireTimers = () => timers.splice(0).forEach((fn) => fn());
  return { rec, made, log, timers, fireTimers };
}

test('speechSupported detects either constructor', () => {
  assert.equal(speechSupported({}), false);
  assert.equal(speechSupported({ webkitSpeechRecognition: function () {} }), true);
  assert.equal(speechSupported({ SpeechRecognition: function () {} }), true);
});

test('starts a continuous session with interim results in the chosen language', async () => {
  const { rec, made, log } = setup();
  rec.start();
  await tick();
  assert.equal(made.length, 1);
  assert.deepEqual([made[0].continuous, made[0].interimResults, made[0].lang, made[0].started], [true, true, 'en-US', 1]);
  assert.deepEqual(log.state, ['listening']);
});

test('interim text is reported, then a final result is committed once', async () => {
  const { rec, made, log } = setup();
  rec.start();
  await tick();
  made[0].say([result('hello', false)]);
  made[0].say([result('hello wor', false)]);
  made[0].say([result('hello world', true)]);
  assert.deepEqual(log.interim, ['hello', 'hello wor', '']);
  assert.deepEqual(log.final, ['hello world']);
});

test('only changed results are read, and repeated finals are not committed twice', async () => {
  const { rec, made, log } = setup();
  rec.start();
  await tick();
  made[0].say([result('one', true), result('two', false)]);
  made[0].say([result('one', true), result('two', true)], 1);
  made[0].say([result('one', true), result('two', true)], 0); // Android re-sends the whole list
  made[0].say([result('one', true), result('two', true), result('three', false)], 2);
  assert.deepEqual(log.final, ['one', 'two']);
  assert.equal(log.interim.at(-1), 'three');
});

test('the same sentence said twice in a row is kept when it arrives as a new result', async () => {
  const { rec, made, log } = setup();
  rec.start();
  await tick();
  made[0].say([result('yes', true)]);
  made[0].say([result('yes', true), result('yes', false)], 1);
  made[0].say([result('yes', true), result('yes', true)], 1);
  assert.deepEqual(log.final, ['yes', 'yes']);
});

test('when the browser ends a session the recogniser starts a new one', async () => {
  const { rec, made, log, fireTimers } = setup();
  rec.start();
  await tick();
  made[0].onend();
  assert.equal(made.length, 1);
  fireTimers();
  await tick();
  assert.equal(made.length, 2);
  assert.deepEqual(log.state, ['listening', 'listening']);
  assert.deepEqual(log.error, []);
});

test('words not yet finalised when a session ends are kept as a line', async () => {
  const { rec, made, log } = setup();
  rec.start();
  await tick();
  made[0].say([result('almost done', false)]);
  made[0].onend();
  assert.deepEqual(log.final, ['almost done']);
  assert.equal(log.interim.at(-1), '');
});

test('stop ends the session without restarting, and late results still arrive', async () => {
  const { rec, made, log, fireTimers } = setup();
  rec.start();
  await tick();
  rec.stop();
  made[0].say([result('last words', true)]);
  await tick();
  fireTimers();
  await tick();
  assert.equal(made.length, 1);
  assert.deepEqual(log.final, ['last words']);
  assert.equal(log.state.at(-1), 'idle');
  assert.equal(rec.listening, false);
});

test('fatal errors stop the recogniser and are reported', async () => {
  for (const code of ['not-allowed', 'audio-capture', 'language-not-supported', 'service-not-allowed']) {
    const { rec, made, log, fireTimers } = setup();
    rec.start();
    await tick();
    made[0].onerror({ error: code });
    made[0].onend();
    fireTimers();
    await tick();
    assert.deepEqual(log.error, [code]);
    assert.equal(made.length, 1);
    assert.equal(log.state.at(-1), 'idle');
  }
});

test('no-speech is ignored; repeated network errors stop with a network error', async () => {
  const { rec, made, log, fireTimers } = setup();
  rec.start();
  await tick();
  made[0].onerror({ error: 'no-speech' });
  made[0].onend();
  fireTimers();
  await tick();
  assert.deepEqual(log.error, []);
  for (let i = 0; i < 3; i++) {
    made.at(-1).onerror({ error: 'network' });
    made.at(-1).onend();
    fireTimers();
    await tick();
  }
  assert.deepEqual(log.error, ['network']);
  assert.equal(rec.listening, false);
});

test('a session that keeps dying at once is reported as unstable instead of looping forever', async () => {
  let t = 0;
  const { rec, made, log, fireTimers } = setup({ now: () => (t += 10) });
  rec.start();
  await tick();
  for (let i = 0; i < 10 && rec.listening; i++) {
    made.at(-1).onend();
    fireTimers();
    await tick();
  }
  assert.deepEqual(log.error, ['unstable']);
  assert.ok(made.length <= 7);
});

test('changing language restarts in the new language', async () => {
  const { rec, made, fireTimers } = setup();
  rec.start();
  await tick();
  rec.setLang('ar-SA');
  await tick();
  fireTimers();
  await tick();
  assert.equal(made.length, 2);
  assert.equal(made[1].lang, 'ar-SA');
});

test('resume starts a new session only if one was wanted and none is running', async () => {
  const { rec, made } = setup();
  rec.resume();
  assert.equal(made.length, 0);
  rec.start();
  await tick();
  rec.resume();
  assert.equal(made.length, 1);
  made[0].onend = null; // the browser dropped it silently while the page was hidden
  rec.stop();
});

test('results are reported in the language they were spoken in, even if the user switches meanwhile', async () => {
  const { rec, made, log } = setup();
  rec.start();
  await tick();
  made[0].say([result('still english', false)]);
  rec.setLang('ar-SA');
  await tick(); // the old session ends and its unfinished words are kept
  assert.deepEqual(log.final, ['still english']);
});
