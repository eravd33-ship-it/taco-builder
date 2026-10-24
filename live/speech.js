// Live speech recognition. Wraps the browser's Web Speech API so it keeps listening:
// browsers end a session after every pause, so this restarts it, de-duplicates the
// results some Android versions repeat, and reports problems as simple error codes.

export function speechSupported(scope = globalThis) {
  return Boolean(scope.SpeechRecognition || scope.webkitSpeechRecognition);
}

const FATAL = new Set(['not-allowed', 'service-not-allowed', 'audio-capture', 'language-not-supported']);
const QUICK_END_MS = 1500;
const MAX_QUICK_ENDS = 6;
const MAX_NETWORK_ERRORS = 3;

export function createRecognizer({ scope = globalThis, lang = 'en-US', onInterim, onFinal, onState, onError, setTimer = setTimeout, clearTimer = clearTimeout, now = Date.now }) {
  const Recognition = scope.SpeechRecognition || scope.webkitSpeechRecognition;
  let rec = null;
  let wanted = false;
  let restartTimer = null;
  let startedAt = 0;
  let quickEnds = 0;
  let networkErrors = 0;
  let interimText = '';
  let committed = new Map();

  function spawn() {
    const me = new Recognition();
    const sessionLang = lang; // results are reported with the language they were spoken in, even if the user switches meanwhile
    rec = me;
    committed = new Map();
    interimText = '';
    me.continuous = true;
    me.interimResults = true;
    me.maxAlternatives = 1;
    me.lang = lang;

    me.onstart = () => {
      if (me === rec) onState?.('listening');
    };

    me.onresult = (event) => {
      if (me !== rec) return;
      quickEnds = 0;
      networkErrors = 0;
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const text = (result[0]?.transcript || '').trim();
        if (result.isFinal) {
          // Some Android versions re-deliver finals that were already handled.
          if (text && committed.get(i) !== text) {
            committed.set(i, text);
            onFinal?.(text, sessionLang);
          }
        } else {
          committed.delete(i);
          if (text) interim += (interim ? ' ' : '') + text;
        }
      }
      interimText = interim;
      onInterim?.(interim, sessionLang);
    };

    me.onerror = (event) => {
      if (me !== rec) return;
      const code = event.error;
      if (FATAL.has(code)) {
        wanted = false;
        onError?.(code);
      } else if (code === 'network' && ++networkErrors >= MAX_NETWORK_ERRORS) {
        wanted = false;
        onError?.('network');
      }
      // 'no-speech' and 'aborted' are normal; the end handler restarts the session.
    };

    me.onend = () => {
      if (me !== rec) return;
      rec = null;
      if (interimText) {
        // The session ended before the browser finalised the last words: keep them.
        const left = interimText;
        interimText = '';
        onInterim?.('', sessionLang);
        onFinal?.(left, sessionLang);
      }
      if (!wanted) {
        onState?.('idle');
        return;
      }
      quickEnds = now() - startedAt < QUICK_END_MS ? quickEnds + 1 : 0;
      if (quickEnds >= MAX_QUICK_ENDS) {
        wanted = false;
        onState?.('idle');
        onError?.('unstable');
        return;
      }
      restartTimer = setTimer(() => {
        restartTimer = null;
        if (wanted) spawn();
      }, quickEnds ? Math.min(250 * quickEnds, 1500) : 150);
    };

    startedAt = now();
    try {
      me.start();
    } catch {
      // Already started: the existing session carries on.
    }
  }

  return {
    start() {
      if (wanted) return;
      wanted = true;
      quickEnds = 0;
      networkErrors = 0;
      if (!rec && !restartTimer) spawn();
    },
    stop() {
      wanted = false;
      if (restartTimer) {
        clearTimer(restartTimer);
        restartTimer = null;
      }
      if (rec) {
        try { rec.stop(); } catch { /* already stopped */ }
      } else {
        onState?.('idle');
      }
    },
    // Switching language ends the current session; the end handler starts a new one in the new language.
    setLang(next) {
      if (next === lang) return;
      lang = next;
      if (rec) {
        try { rec.stop(); } catch { /* already stopped */ }
      }
    },
    // After the page was in the background the browser may have dropped the session.
    resume() {
      if (wanted && !rec && !restartTimer) spawn();
    },
    get listening() { return wanted; },
  };
}
