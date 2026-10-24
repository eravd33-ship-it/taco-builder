import test from 'node:test';
import assert from 'node:assert/strict';
import { createCaptions, toTranscript } from '../captions.js';

const tick = () => new Promise((r) => setImmediate(r));

function setup({ live = true, translate } = {}) {
  const timers = [];
  const calls = [];
  const states = [];
  const errors = [];
  const captions = createCaptions({
    translate: translate || (async (text, lang) => { calls.push([text, lang]); return `he(${text})`; }),
    getLang: () => 'en',
    isLive: () => live,
    onChange: (s) => states.push(JSON.parse(JSON.stringify(s))),
    onError: (e) => errors.push(e.kind || e.message),
    setTimer: (fn) => { timers.push(fn); return timers.length; },
    clearTimer: (id) => { timers[id - 1] = null; },
  });
  const fire = async () => { timers.splice(0).forEach((fn) => fn?.()); await tick(); };
  return { captions, calls, states, errors, fire };
}

test('a committed sentence becomes a pending line, then gets its Hebrew', async () => {
  const { captions, states } = setup();
  captions.commit('  hello   world ');
  assert.deepEqual(states.at(-1).lines.map((l) => [l.src, l.he, l.status]), [['hello world', '', 'pending']]);
  await tick();
  assert.deepEqual(captions.lines.map((l) => [l.src, l.he, l.status, l.lang]), [['hello world', 'he(hello world)', 'done', 'en']]);
});

test('empty text is ignored', () => {
  const { captions, states } = setup();
  captions.commit('   ');
  captions.setInterim('');
  assert.equal(states.length, 0);
});

test('the live line shows the original at once and its translation after a pause in the speech', async () => {
  const { captions, calls, fire } = setup();
  captions.setInterim('hello');
  captions.setInterim('hello wor');
  assert.deepEqual(captions.interim, { src: 'hello wor', he: '' });
  assert.equal(calls.length, 0);
  await fire();
  assert.deepEqual(calls, [['hello wor', 'en']]);
  assert.deepEqual(captions.interim, { src: 'hello wor', he: 'he(hello wor)' });
});

test('with live translation off the interim text is never sent for translation', async () => {
  const { captions, calls, fire } = setup({ live: false });
  captions.setInterim('hello there');
  await fire();
  assert.equal(calls.length, 0);
  assert.equal(captions.interim.src, 'hello there');
});

test('a stale interim translation is dropped when newer words or a commit arrive', async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const { captions, fire } = setup({ translate: async (text) => (text === 'old words' ? slow.then(() => 'ישן') : `he(${text})`) });
  captions.setInterim('old words');
  await fire();
  captions.commit('old words are done');
  release();
  await tick();
  assert.deepEqual(captions.interim, { src: '', he: '' });
  assert.equal(captions.lines.length, 1);
});

test('the previous translation stays on screen while a longer one is on its way', async () => {
  const { captions, fire } = setup();
  captions.setInterim('hello');
  await fire();
  captions.setInterim('hello world');
  assert.deepEqual(captions.interim, { src: 'hello world', he: 'he(hello)' });
  captions.setInterim('');
  assert.deepEqual(captions.interim, { src: '', he: '' });
});

test('commit clears the live line and cancels its pending translation', async () => {
  const { captions, calls, fire } = setup();
  captions.setInterim('hello');
  captions.commit('hello');
  await fire();
  assert.deepEqual(captions.interim, { src: '', he: '' });
  assert.deepEqual(calls, [['hello', 'en']]);
});

test('a failed translation marks the line and reports the error once per failure', async () => {
  const { captions, errors } = setup({ translate: async () => { throw Object.assign(new Error('x'), { kind: 'quota' }); } });
  captions.commit('hello');
  await tick();
  assert.equal(captions.lines[0].status, 'error');
  assert.deepEqual(errors, ['quota']);
});

test('lines arrive in order even when their translations finish out of order', async () => {
  const resolvers = {};
  const { captions } = setup({ translate: (text) => new Promise((r) => { resolvers[text] = () => r(`he(${text})`); }) });
  captions.commit('first');
  captions.commit('second');
  resolvers.second();
  await tick();
  assert.deepEqual(captions.lines.map((l) => [l.src, l.status]), [['first', 'pending'], ['second', 'done']]);
  resolvers.first();
  await tick();
  assert.deepEqual(captions.lines.map((l) => l.he), ['he(first)', 'he(second)']);
});

test('clear empties everything; the line cap drops the oldest', async () => {
  const timers = [];
  const captions = createCaptions({ translate: async (t) => t, getLang: () => 'en', maxLines: 3, setTimer: (f) => timers.push(f), clearTimer() {} });
  for (const w of ['a', 'b', 'c', 'd']) captions.commit(w);
  assert.deepEqual(captions.lines.map((l) => l.src), ['b', 'c', 'd']);
  captions.clear();
  assert.equal(captions.lines.length, 0);
});

test('transcript: Hebrew first with the original under it; untranslated lines show the original', () => {
  const lines = [
    { src: 'hello', he: 'שלום', status: 'done' },
    { src: 'broken', he: '', status: 'error' },
    { src: 'wait', he: '', status: 'pending' },
  ];
  assert.equal(toTranscript(lines), 'שלום\n(hello)\n\nbroken\n\nwait');
  assert.equal(toTranscript(lines, { withOriginal: false }), 'שלום\n\nbroken\n\nwait');
});

test('a sentence keeps the language it was spoken in even if the selection changes before it is translated', async () => {
  let lang = 'en';
  const calls = [];
  const captions = createCaptions({ translate: async (text, l) => { calls.push([text, l]); return text; }, getLang: () => lang, isLive: () => true, setTimer: (f) => f(), clearTimer() {} });
  lang = 'ar';
  captions.commit('hello', 'en');
  captions.setInterim('hi there', 'en');
  await tick();
  assert.deepEqual(calls, [['hello', 'en'], ['hi there', 'en']]);
  assert.equal(captions.lines[0].lang, 'en');
});
