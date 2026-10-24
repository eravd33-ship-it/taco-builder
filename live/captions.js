// The subtitle model: finished lines (original + Hebrew) and the line being spoken right now.
// It knows nothing about the page; app.js renders whatever it reports through `onChange`.

const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();

export function createCaptions({ translate, getLang, isLive = () => false, onChange, onError, delay = 700, maxLines = 1000, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let lines = [];
  let nextId = 1;
  let interim = { src: '', he: '' };
  let interimSeq = 0;
  let timer = null;

  const emit = () => onChange?.({ lines, interim });

  function cancelPending() {
    interimSeq++;
    if (timer) {
      clearTimer(timer);
      timer = null;
    }
  }

  return {
    // What is being said right now; translated (debounced) only when `isLive()`. `lang` defaults to the selected language.
    setInterim(text, lang = getLang()) {
      text = clean(text);
      if (text === interim.src) return;
      cancelPending();
      interim = { src: text, he: text ? interim.he : '' };
      emit();
      if (!text || !isLive()) return;
      const seq = interimSeq;
      timer = setTimer(async () => {
        timer = null;
        try {
          const he = await translate(text, lang);
          if (seq === interimSeq) {
            interim = { src: text, he };
            emit();
          }
        } catch (err) {
          if (seq === interimSeq) onError?.(err);
        }
      }, delay);
    },

    // A finished sentence: it becomes a line and is translated straight away.
    commit(text, lang = getLang()) {
      text = clean(text);
      if (!text) return;
      cancelPending();
      interim = { src: '', he: '' };
      const line = { id: nextId++, lang, src: text, he: '', status: 'pending' };
      lines = [...lines, line].slice(-maxLines);
      emit();
      translate(text, lang).then(
        (he) => {
          line.he = he;
          line.status = 'done';
          emit();
        },
        (err) => {
          line.status = 'error';
          emit();
          onError?.(err);
        },
      );
    },

    clear() {
      cancelPending();
      lines = [];
      interim = { src: '', he: '' };
      emit();
    },

    get lines() { return lines; },
    get interim() { return interim; },
  };
}

// Plain text for copying or saving: Hebrew first, the original in brackets underneath.
export function toTranscript(lines, { withOriginal = true } = {}) {
  return lines
    .map((line) => {
      const hebrew = line.status === 'done' && line.he ? line.he : line.src;
      return withOriginal && hebrew !== line.src ? `${hebrew}\n(${line.src})` : hebrew;
    })
    .join('\n\n');
}
