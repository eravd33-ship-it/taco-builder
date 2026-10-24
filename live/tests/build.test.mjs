import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { build } from '../build-single.mjs';

const html = build();

test('the committed live-subtitles.html is up to date (run `npm run build` after changing the app)', () => {
  assert.equal(readFileSync(new URL('../live-subtitles.html', import.meta.url), 'utf8'), html);
});

test('the single file needs nothing else: no local files, no module imports, no download link to itself', () => {
  const refs = [...html.matchAll(/(?:src|href)="([^"]*)"/g)].map((m) => m[1]);
  assert.ok(refs.length > 0 && refs.every((r) => r.startsWith('data:')), `unexpected references: ${refs.filter((r) => !r.startsWith('data:'))}`);
  assert.doesNotMatch(html, /^import /m);
  assert.doesNotMatch(html, /^export /m);
  assert.doesNotMatch(html, /live-subtitles\.html|rel="manifest"/);
  assert.match(html, /<title>כתוביות חיות/);
});

test('the bundled script is valid and declares nothing twice', () => {
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
  assert.doesNotThrow(() => new vm.Script(`(async () => {${script}})`));
});
