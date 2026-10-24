// Packs the app into one self-contained HTML file (live-subtitles.html) that can be saved, sent and opened from disk.
// Run `npm run build` after changing any app file; a test fails if the committed file is out of date.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const here = (name) => new URL(name, import.meta.url);
const read = (name) => readFileSync(here(name), 'utf8');

// Dependency order; app.js is last.
const MODULES = ['languages.js', 'translate.js', 'speech.js', 'captions.js', 'app.js'];

export function build() {
  const script = MODULES.map((name) => read(name)
    .replace(/^import .* from '.*';\n/gm, '')
    .replace(/^export (?=(async function|function|class|const|let) )/gm, '')).join('\n');
  const icon = `data:image/svg+xml;base64,${Buffer.from(read('icons/icon.svg')).toString('base64')}`;

  return read('index.html')
    .replace('<link rel="stylesheet" href="style.css">', () => `<style>\n${read('style.css')}</style>`)
    .replace('<script type="module" src="app.js"></script>', () => `<script type="module">\n${script}</script>`)
    .replace(/\s*<link rel="manifest"[^>]*>/, '')
    .replace(/\s*<link rel="apple-touch-icon"[^>]*>/, '')
    .replaceAll('icons/icon.svg', icon)
    .replace(/\s*<!--web-only-->[\s\S]*?<!--\/web-only-->/g, '');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const html = build();
  writeFileSync(here('live-subtitles.html'), html);
  console.log(`live-subtitles.html: ${(html.length / 1024).toFixed(1)} KB`);
}
