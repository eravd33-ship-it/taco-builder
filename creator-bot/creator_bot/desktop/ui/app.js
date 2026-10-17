'use strict';
/* Creator Desk UI. Plain JavaScript, no dependencies, no network except the local API.
   Text coming from the user is only ever inserted with textContent (see h()). */

const TOKEN = document.querySelector('meta[name="token"]').content;
const $ = (selector) => document.querySelector(selector);

// ---------- helpers ----------
async function api(method, path, body) {
  const headers = { 'X-Token': TOKEN };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'משהו השתבש');
  return data;
}

/** Build a DOM element. Strings become text nodes, never HTML. */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null) continue;
    if (key.startsWith('aria-')) { el.setAttribute(key, String(value)); continue; } // "false" is meaningful for ARIA
    if (value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key in el) el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const pad = (n) => String(n).padStart(2, '0');
function isoDate(date) { return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`; }
function inDays(n) { const d = new Date(); d.setDate(d.getDate() + n); return isoDate(d); }
function daysText(n) { return n === 1 ? 'יום אחד' : n === 2 ? 'יומיים' : `${n} ימים`; }
function plural(n, one, many) { return n === 1 ? one : `${n} ${many}`; }
const number = (n) => Number(n).toLocaleString('he-IL');
function longDate(iso) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString('he-IL', { weekday: 'long', day: 'numeric', month: 'long' });
}

function toast(text, kind = 'info', ms = 5000) {
  const box = $('#toasts');
  const el = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, text);
  box.append(el);
  while (box.children.length > 3) box.firstChild.remove(); // never bury the screen in messages
  setTimeout(() => el.remove(), ms);
}

/** Run an action from a button: disable it meanwhile, report errors, then refresh the screen. */
async function act(button, fn, message) {
  if (button) button.disabled = true;
  try {
    const result = await fn();
    const text = typeof message === 'function' ? message(result) : message;
    if (text) toast(text);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
  await refreshCurrent();
  pollFeed();
}

/** Two-step delete: first click asks, second click does it. */
function deleteButton(label, onConfirm) {
  const box = h('span', { class: 'actions' });
  const reset = () => box.replaceChildren(h('button', { class: 'btn sm ghost danger', 'aria-label': label, onclick: ask }, '🗑️'));
  const ask = () => box.replaceChildren(
    h('span', { class: 'small muted' }, 'למחוק?'),
    h('button', { class: 'btn sm danger', onclick: (e) => act(e.currentTarget, onConfirm, '🗑️ נמחק') }, 'כן'),
    h('button', { class: 'btn sm', onclick: reset }, 'לא'),
  );
  reset();
  return box;
}

function progressBar(value, goal) {
  const percent = goal > 0 ? Math.min(100, Math.round((value / goal) * 100)) : 100;
  const bar = h('span');
  bar.style.width = `${percent}%`;
  return h('div', { class: `progress ${percent >= 100 ? 'ok' : ''}`, role: 'progressbar', 'aria-valuenow': percent, 'aria-valuemin': 0, 'aria-valuemax': 100 }, bar);
}

function dateChooser(initial = 7) {
  const input = h('input', { type: 'date', class: 'field', min: inDays(0), value: inDays(initial), required: true, 'aria-label': 'תאריך פרסום' });
  const quick = [['מחר', 1], ['בעוד 3 ימים', 3], ['בעוד שבוע', 7], ['בעוד שבועיים', 14]];
  const chips = h('div', { class: 'chips' }, quick.map(([label, days]) =>
    h('button', { type: 'button', class: 'chip', onclick: () => { input.value = inDays(days); } }, label)));
  return { input, chips };
}

function externalLink(url, text) {
  const link = h('a', { href: url, class: 'small', onclick: (e) => {
    e.preventDefault();
    api('POST', '/api/open', { url }).catch((error) => toast(error.message, 'error'));
  } }, text);
  return h('bdi', null, link); // isolate it, so English link texts do not scramble the Hebrew around them
}

const empty = (icon, text) => h('div', { class: 'empty' }, h('span', { class: 'big' }, icon), text);

// ---------- screens ----------
function weekMessage(w) {
  if (w.met) return '🎯 עמדת ביעד השבועי! כל העלאה נוספת היא בונוס 🎁';
  if (w.missing <= 0) return `👌 בדרך הנכונה – ${plural(w.scheduled, 'סרטון אחד מתוכנן', 'סרטונים מתוכננים')} עד סוף השבוע.`;
  const need = w.missing === 1 ? 'חסר עוד סרטון אחד' : `חסרים עוד ${w.missing} סרטונים`;
  return `⏳ ${need} ליעד, ונשארו ${daysText(w.days_left)}. אולי להפוך רעיון לסרטון? 💡`;
}

function weekCard(w, views) {
  return h('div', { class: 'card' },
    h('h2', null, '🚀 ההתקדמות שלך'),
    h('div', { class: 'stat' }, h('span', null, 'העלאות השבוע'), h('strong', null, `${w.done}/${w.goal}`)),
    progressBar(w.done, w.goal),
    h('p', { class: 'muted small' }, weekMessage(w)),
    w.streak >= 2 && h('p', null, h('span', { class: 'badge ok' }, `🔥 ${w.streak} שבועות ברצף ביעד`)),
    views && h('div', null,
      h('div', { class: 'stat' }, h('span', null, 'צפיות החודש'), h('strong', null, `${number(views.gained)}/${number(views.goal)}`)),
      progressBar(views.gained, views.goal)),
  );
}

function channelCard(c) {
  const delta = (n) => (n == null ? '' : ` (${n >= 0 ? '+' : ''}${number(n)})`);
  const refresh = h('button', { class: 'btn sm', onclick: (e) => act(e.currentTarget, () => api('POST', '/api/stats/refresh'), '📈 הנתונים עודכנו') }, '🔄 רענון');
  const body = c.empty
    ? h('p', { class: 'muted' }, 'עוד אין נתונים. לחץ על רענון.')
    : h('div', { class: 'row' },
        h('div', { class: 'grow' }, h('div', { class: 'muted small' }, 'צפיות'), h('strong', null, number(c.views)), h('span', { class: 'muted small' }, delta(c.views_delta))),
        c.subscribers != null && h('div', { class: 'grow' }, h('div', { class: 'muted small' }, 'מנויים'), h('strong', null, number(c.subscribers)), h('span', { class: 'muted small' }, delta(c.subscribers_delta))),
        h('div', { class: 'grow' }, h('div', { class: 'muted small' }, 'סרטונים'), h('strong', null, number(c.videos))));
  return h('div', { class: 'card' }, h('div', { class: 'video-head' }, h('h2', null, `📈 ${c.title || 'הערוץ'}`), refresh), body);
}

function videoTaskRow(t) {
  const done = (e) => act(e.currentTarget, () => api('POST', `/api/videos/${t.video_id}/done`, { step: t.key }), (r) => (r.changed ? r.message : 'כבר מסומן ✅'));
  const snooze = (e) => act(e.currentTarget, () => api('POST', `/api/videos/${t.video_id}/snooze`, { step: t.key }), '⏰ נדחה למחר');
  return h('li', { class: 'task' },
    h('span', { class: 'emoji', 'aria-hidden': true }, t.emoji),
    h('div', { class: 'grow' },
      h('div', { class: 'task-title' }, h('strong', null, `${t.verb}: `), t.title),
      t.late_days > 0 ? h('span', { class: 'badge warn' }, `⚠️ באיחור של ${daysText(t.late_days)}`) : h('span', { class: 'muted small' }, t.due_label)),
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary sm', onclick: done }, '✅ בוצע'),
      h('button', { class: 'btn sm', onclick: snooze }, '⏰ מחר')));
}

function routineRow(r) {
  const toggle = (e) => act(e.currentTarget, () => api('POST', `/api/routines/${r.id}/done`), r.done ? null : '✅ כל הכבוד!');
  return h('li', { class: 'task' },
    h('button', { class: `check ${r.done ? 'on' : ''}`, 'aria-label': `סמן ${r.title} כבוצע`, 'aria-pressed': r.done, disabled: r.done, onclick: toggle }, '✔'),
    h('div', { class: `grow task-title ${r.done ? 'done-text' : ''}` }, r.title));
}

const views = {
  today: {
    title: 'היום',
    async mount(root) {
      const box = h('div');
      root.append(box);
      const refresh = async () => {
        const d = await api('GET', '/api/today');
        const open = d.video_tasks.length + d.routines.filter((r) => !r.done).length;
        const parts = [
          h('div', { class: 'card hero' }, h('div', { class: 'date' }, longDate(d.date)), h('p', { class: 'quote' }, d.quote)),
        ];
        if (open === 0) {
          parts.push(h('div', { class: 'card' }, d.done_today > 0
            ? empty('🙌', 'סגרת את כל המשימות של היום. ככה בונים ערוץ!')
            : empty('📭', 'אין משימות להיום. זמן מעולה לתכנן את הסרטון הבא 🎬')));
        }
        if (d.video_tasks.length) parts.push(h('div', { class: 'card' }, h('h2', null, '🎬 סרטונים להיום'), h('ul', { class: 'list' }, d.video_tasks.map(videoTaskRow))));
        if (d.routines.length) parts.push(h('div', { class: 'card' }, h('h2', null, '🔁 משימות קבועות'), h('ul', { class: 'list' }, d.routines.map(routineRow))));
        if (d.nudge_idea) {
          parts.push(h('div', { class: 'card' }, h('div', { class: 'row' },
            h('div', { class: 'grow' }, `💡 אין סרטון בתהליך, ומחכים לך ${plural(d.ideas_open, 'רעיון אחד', 'רעיונות')}. מה דעתך להפוך אחד לסרטון?`),
            h('button', { class: 'btn primary', onclick: () => show('ideas') }, 'לרעיונות'))));
        }
        if (d.upcoming.length) {
          parts.push(h('div', { class: 'card' }, h('h2', null, '🗓️ בקרוב'), h('ul', { class: 'list' }, d.upcoming.map((t) =>
            h('li', { class: 'item' }, h('span', null, `${t.emoji} ${t.verb}: ${t.title}`), h('span', { class: 'muted small' }, t.due_label))))));
        }
        parts.push(weekCard(d.week, d.views));
        if (d.channel) parts.push(channelCard(d.channel));
        box.replaceChildren(...parts);
      };
      await refresh();
      return refresh;
    },
  },

  videos: {
    title: 'סרטונים',
    async mount(root) {
      const title = h('input', { class: 'field grow', placeholder: 'על מה הסרטון?', maxLength: 100, required: true, 'aria-label': 'שם הסרטון' });
      const { input: date, chips } = dateChooser(7);
      const submit = h('button', { class: 'btn primary', type: 'submit' }, '➕ תזמן סרטון');
      const form = h('form', { class: 'stack', onsubmit: (e) => {
        e.preventDefault();
        act(submit, async () => {
          await api('POST', '/api/videos', { title: title.value, date: date.value });
          title.value = '';
        }, '✅ הסרטון נכנס ללוח – אזכיר לך בכל שלב');
      } },
        h('div', { class: 'row' }, title, h('label', { class: 'row small muted' }, 'פרסום:', date), submit),
        chips,
        h('p', { class: 'muted small' }, 'הצילום יתוזמן 4 ימים לפני הפרסום, העריכה יומיים לפני, וההעלאה ביום הפרסום.'));
      const list = h('div');
      root.append(h('div', { class: 'card' }, h('h2', null, '🎬 סרטון חדש'), form), list);

      const refresh = async () => {
        const data = await api('GET', '/api/videos');
        const cards = data.active.map(videoCard);
        if (!cards.length) cards.push(h('div', { class: 'card' }, empty('📭', 'אין סרטונים בתהליך. תזמן את הראשון למעלה!')));
        if (data.published.length) {
          cards.push(h('details', { class: 'card' }, h('summary', null, `✅ פורסמו (${data.published.length})`),
            h('ul', { class: 'list' }, data.published.map((v) =>
              h('li', { class: 'item' }, h('span', { class: 'grow' }, v.title), h('span', { class: 'muted small' }, longDate(v.published_on)))))));
        }
        list.replaceChildren(...cards);
      };
      await refresh();
      return refresh;
    },
  },

  routines: {
    title: 'משימות קבועות',
    async mount(root) {
      const templates = ['💬 להגיב לתגובות', '📊 לבדוק אנליטיקס', '📣 פוסט בקהילה', '📱 להעלות שורט', '🔍 לחפש רעיונות וטרנדים'];
      const title = h('input', { class: 'field grow', placeholder: 'למשל: להגיב לתגובות', maxLength: 80, required: true, 'aria-label': 'שם המשימה' });
      const selected = new Set();
      const dayBox = h('div', { class: 'chips', role: 'group', 'aria-label': 'ימים' });
      const submit = h('button', { class: 'btn primary', type: 'submit' }, '➕ הוסף');
      const list = h('div');
      let meta = { week_order: [0, 1, 2, 3, 4, 5, 6], weekday_letters: ['ב׳', 'ג׳', 'ד׳', 'ה׳', 'ו׳', 'ש׳', 'א׳'] };

      const drawDays = () => dayBox.replaceChildren(
        ...meta.week_order.map((d) => h('button', { type: 'button', class: 'chip', 'aria-pressed': selected.has(d), onclick: () => {
          if (selected.has(d)) selected.delete(d); else selected.add(d);
          drawDays();
        } }, meta.weekday_letters[d])),
        h('button', { type: 'button', class: 'chip', onclick: () => { for (let i = 0; i < 7; i++) selected.add(i); drawDays(); } }, 'כל יום'));

      const form = h('form', { class: 'stack', onsubmit: (e) => {
        e.preventDefault();
        if (!selected.size) { toast('צריך לבחור לפחות יום אחד', 'error'); return; }
        act(submit, async () => {
          await api('POST', '/api/routines', { title: title.value, weekdays: [...selected] });
          title.value = ''; selected.clear(); drawDays();
        }, '✅ נשמר – אזכיר לך בבוקר 🔔');
      } },
        h('div', { class: 'chips' }, templates.map((t) => h('button', { type: 'button', class: 'chip', onclick: () => { title.value = t; title.focus(); } }, t))),
        h('div', { class: 'row' }, title, submit),
        dayBox);
      root.append(h('div', { class: 'card' }, h('h2', null, '🔁 משימה קבועה חדשה'), form), list);

      const refresh = async () => {
        const data = await api('GET', '/api/routines');
        meta = data;
        if (!dayBox.children.length) drawDays();
        list.replaceChildren(h('div', { class: 'card' }, h('h2', null, 'המשימות שלך'),
          data.routines.length
            ? h('ul', { class: 'list' }, data.routines.map((r) => h('li', { class: 'item' },
                h('div', { class: 'grow' }, h('div', { class: 'task-title' }, r.title), h('div', { class: 'muted small' }, r.days_label)),
                r.done_today && h('span', { class: 'badge ok' }, '✔ בוצע היום'),
                deleteButton(`מחק ${r.title}`, () => api('DELETE', `/api/routines/${r.id}`)))))
            : empty('🔁', 'עוד אין משימות קבועות. משימות שחוזרות כל שבוע – ואזכיר לך אותן בבוקר.')));
      };
      await refresh();
      return refresh;
    },
  },

  ideas: {
    title: 'רעיונות',
    async mount(root) {
      const text = h('input', { class: 'field grow', placeholder: 'רעיון לסרטון…', maxLength: 300, required: true, 'aria-label': 'רעיון' });
      const submit = h('button', { class: 'btn primary', type: 'submit' }, '💡 שמור');
      const list = h('div');
      const form = h('form', { class: 'row', onsubmit: (e) => {
        e.preventDefault();
        act(submit, async () => { await api('POST', '/api/ideas', { text: text.value }); text.value = ''; }, '💡 נשמר!');
      } }, text, submit);
      root.append(h('div', { class: 'card' }, h('h2', null, '💡 רעיון חדש'), form), aiIdeasCard(), list);

      const refresh = async () => {
        const { ideas } = await api('GET', '/api/ideas');
        list.replaceChildren(h('div', { class: 'card' }, h('h2', null, `הרעיונות שלך (${ideas.length})`),
          ideas.length
            ? h('ul', { class: 'list' }, ideas.map(ideaRow))
            : empty('💡', 'הרשימה ריקה. כל רעיון שעולה – תכתוב כאן, ובלחיצה הוא יהפוך לסרטון מתוזמן.')));
      };
      await refresh();
      return refresh;
    },
  },

  assistant: {
    title: 'עוזר',
    async mount(root) {
      const box = h('div');
      root.append(box);
      const draw = async () => {
        const a = await api('GET', '/api/assistant');
        if (!a.configured) {
          box.replaceChildren(h('div', { class: 'card stack' }, h('h2', null, '🤖 העוזר האישי שלך'),
            h('p', { class: 'muted' }, 'עוזר בינה מלאכותית שמכיר את הערוץ שלך: מציע רעיונות וכותרות, עוזר לתכנן ועונה על שאלות. כדי להפעיל אותו צריך מפתח (חד-פעמי). אפשר לקבל מפתח חינם:'),
            assistantKeyForm(draw)));
          return;
        }
        box.replaceChildren(chatUi(a, draw));
      };
      await draw();
      return null;
    },
  },

  goals: {
    title: 'יעדים',
    async mount(root) {
      const box = h('div');
      root.append(box);
      const refresh = async () => {
        const g = await api('GET', '/api/goals');
        const set = (body, message) => (e) => act(e.currentTarget, () => api('PUT', '/api/goals', body), message);
        const uploads = h('div', { class: 'card' }, h('h2', null, '🚀 יעד העלאות שבועי'),
          h('div', { class: 'chips', role: 'group', 'aria-label': 'סרטונים בשבוע' }, [1, 2, 3, 4, 5, 6, 7].map((n) =>
            h('button', { class: 'chip', 'aria-pressed': g.uploads_per_week === n, onclick: set({ uploads_per_week: n }, `✅ היעד עודכן: ${plural(n, 'סרטון אחד', 'סרטונים')} בשבוע`) }, String(n)))),
          h('div', { class: 'stack' }, weekCardBody(g.week)));
        const parts = [uploads];
        if (g.stats_enabled) {
          const input = h('input', { class: 'field', type: 'number', min: 0, step: 100, value: g.views_per_month || '', placeholder: 'למשל 10000', 'aria-label': 'יעד צפיות חודשי' });
          const save = h('button', { class: 'btn primary', type: 'submit' }, 'שמירה');
          parts.push(h('div', { class: 'card' }, h('h2', null, '👀 יעד צפיות חודשי'),
            h('form', { class: 'row', onsubmit: (e) => {
              e.preventDefault();
              act(save, () => api('PUT', '/api/goals', { views_per_month: Number(input.value || 0) }), '✅ היעד עודכן');
            } }, input, save),
            g.views && h('div', { class: 'stack' }, h('div', { class: 'stat' }, h('span', null, 'החודש'), h('strong', null, `${number(g.views.gained)}/${number(g.views.goal)}`)), progressBar(g.views.gained, g.views.goal))));
        }
        box.replaceChildren(...parts);
      };
      await refresh();
      return refresh;
    },
  },

  settings: {
    title: 'הגדרות',
    async mount(root) {
      const s = await api('GET', '/api/settings');
      const save = (body, message) => api('PUT', '/api/settings', body).then(() => message && toast(message)).catch((e) => toast(e.message, 'error'));
      const time = (id, label, value) => {
        const input = h('input', { class: 'field', type: 'time', value, required: true, 'aria-label': label, onchange: () => input.value && save({ [id]: input.value }, '✅ נשמר') });
        return h('label', { class: 'row' }, h('span', { class: 'grow' }, label), input);
      };
      const toggle = (key, label, hint, checked) => {
        const input = h('input', { type: 'checkbox', checked, onchange: () => save({ [key]: input.checked }, '✅ נשמר') });
        return h('label', { class: 'row' }, h('span', { class: 'grow' }, label, h('div', { class: 'muted small' }, hint)), input);
      };
      const testBtn = h('button', { class: 'btn', onclick: (e) => act(e.currentTarget, () => api('POST', '/api/test-notification'), (r) =>
        r.shown ? '🔔 שלחתי התראה למערכת' : 'התראות המערכת לא זמינות במחשב הזה – ההתראות יופיעו כאן באפליקציה') }, '🔔 שלח התראת בדיקה');
      const quit = h('button', { class: 'btn danger', onclick: () => {
        api('POST', '/api/quit').then(() => { document.body.replaceChildren(h('p', { class: 'empty' }, 'Creator Desk נסגר. אפשר לסגור את החלון.')); });
      } }, '⏻ יציאה מהאפליקציה');

      root.append(
        h('div', { class: 'card stack' }, h('h2', null, '⏰ תזכורות'),
          time('morning', 'סיכום בוקר – המשימות להיום', s.morning),
          time('evening', "צ'ק-אין ערב – מה שנשאר פתוח", s.evening),
          toggle('os_notifications', 'התראות במערכת ההפעלה', 'חלון קופץ גם כשהאפליקציה ברקע', s.os_notifications),
          toggle('autostart', 'הפעלה אוטומטית עם המחשב', 'האפליקציה תיפתח ממוזערת כשנכנסים למחשב, כדי שהתזכורות תמיד יגיעו', s.autostart),
          h('div', null, testBtn)),
        googleCard(),
        assistantCard(),
        h('div', { class: 'card stack' }, h('h2', null, 'ℹ️ מידע'),
          h('div', { class: 'muted small' }, `גרסה ${s.version}`),
          h('div', { class: 'muted small', dir: 'ltr' }, s.data_dir),
          h('div', null, quit)));
      return null;
    },
  },
};

/** Two-step button: first click asks `question`, the second click does it. */
function twoStep(label, question, onConfirm) {
  const box = h('span', { class: 'actions' });
  const reset = () => box.replaceChildren(h('button', { class: 'btn sm danger', onclick: ask }, label));
  const ask = () => box.replaceChildren(
    h('span', { class: 'small muted' }, question),
    h('button', { class: 'btn sm danger', onclick: (e) => act(e.currentTarget, onConfirm) }, 'כן'),
    h('button', { class: 'btn sm', onclick: reset }, 'לא'),
  );
  reset();
  return box;
}

/** Connecting to the user's Google account (YouTube stats + Drive) without touching any file. */
function googleCard() {
  const box = h('div', { class: 'card stack' });
  let poll = null;
  const stopPolling = () => { if (poll) { clearInterval(poll); poll = null; } };

  const startPolling = () => {
    stopPolling();
    const started = Date.now();
    poll = setInterval(async () => {
      if (!box.isConnected || Date.now() - started > 10 * 60 * 1000) { stopPolling(); return; }
      try {
        const g = await api('GET', '/api/google');
        if (g.connected) { stopPolling(); toast('✅ התחברת לחשבון גוגל'); await draw(); pollFeed(); }
        else if (!g.waiting) { stopPolling(); toast('החיבור לא הושלם. אפשר לנסות שוב.', 'error'); await draw(); }
      } catch { /* the app may be closing */ }
    }, 2000);
  };

  const guide = (open) => h('details', { open },
    h('summary', null, 'איך משיגים מזהה לקוח? (חד-פעמי, כ-5 דקות)'),
    h('ol', { class: 'steps' },
      h('li', null, 'פותחים את ', externalLink('https://console.cloud.google.com/projectcreate', 'Google Cloud Console'), ' ויוצרים פרויקט חדש (שם חופשי).'),
      h('li', null, 'מפעילים (', h('bdi', null, 'Enable'), ') את שני ה-API: ',
        externalLink('https://console.cloud.google.com/apis/library/youtube.googleapis.com', 'YouTube Data API v3'), ' ו-',
        externalLink('https://console.cloud.google.com/apis/library/drive.googleapis.com', 'Google Drive API'), '.'),
      h('li', null, 'פותחים את ', externalLink('https://console.cloud.google.com/apis/credentials/consent', 'OAuth consent screen'),
        ': בוחרים ', h('bdi', null, 'External'), ', ממלאים שם לאפליקציה ואת המייל שלך, ומוסיפים את המייל שלך גם כ-', h('bdi', null, 'Test user'), '.'),
      h('li', null, 'ב-', externalLink('https://console.cloud.google.com/apis/credentials', 'Credentials'), ' לוחצים ', h('bdi', null, 'Create credentials'),
        ' ← ', h('bdi', null, 'OAuth client ID'), ', ובסוג האפליקציה בוחרים ', h('b', null, h('bdi', null, 'Desktop app')), '.'),
      h('li', null, 'מעתיקים את ', h('bdi', null, 'Client ID'), ' ואת ', h('bdi', null, 'Client secret'), ' ומדביקים כאן.'),
    ),
    h('p', { class: 'muted small' },
      'טיפ: במסך ההסכמה לוחצים ', h('bdi', null, 'Publish app'), ', כדי שהחיבור לא יפוג אחרי 7 ימים. בכניסה תופיע אזהרה ',
      h('bdi', null, "Google hasn't verified this app"), ': לוחצים ', h('bdi', null, 'Advanced'), ' ואז ', h('bdi', null, 'Go to …'),
      '. זו האפליקציה שלך, והיא מבקשת רק לראות את נתוני הערוץ ולנהל קבצים שהיא עצמה יצרה.'));

  const setup = (g) => {
    const id = h('input', { class: 'field', value: g.client_id, dir: 'ltr', placeholder: '1234…apps.googleusercontent.com', required: true, autocomplete: 'off', 'aria-label': 'Client ID' });
    const secret = h('input', { class: 'field', type: 'password', dir: 'ltr', placeholder: 'GOCSPX-…', required: true, autocomplete: 'off', 'aria-label': 'Client secret' });
    const go = h('button', { class: 'btn primary', type: 'submit' }, '🔗 התחבר לגוגל');
    const form = h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      go.disabled = true;
      try {
        await api('POST', '/api/google/connect', { client_id: id.value, client_secret: secret.value });
        toast('נפתח דפדפן לאישור. אחרי האישור חוזרים לכאן.');
        startPolling();
        await draw();
      } catch (error) {
        toast(error.message, 'error');
        go.disabled = false;
      }
    } }, h('label', { class: 'labeled small' }, h('span', null, 'Client ID'), id), h('label', { class: 'labeled small' }, h('span', null, 'Client secret'), secret), h('div', null, go));
    return [
      h('p', { class: 'muted' }, 'כדי לראות כאן את נתוני הערוץ ולקבל תיקייה בדרייב לכל סרטון, מתחברים לחשבון הגוגל שלך. זה חינמי, והחיבור אפשר להסרה בכל רגע.'),
      g.waiting
        ? h('div', { class: 'note-box' }, '⏳ ממתין לאישור בדפדפן. אחרי שתאשר, החלון הזה יתעדכן לבד.')
        : null,
      guide(!g.has_client),
      form,
    ];
  };

  const connected = () => [
    h('p', null, '✅ מחובר לחשבון הגוגל שלך. נתוני הערוץ מתעדכנים כל יום, והגיבוי לדרייב רץ כל יום.'),
    h('div', { class: 'row' },
      twoStep('נתק חיבור', 'לנתק?', async () => { await api('DELETE', '/api/google'); await draw(); }),
      externalLink('https://myaccount.google.com/permissions', 'ניהול ההרשאות בחשבון גוגל')),
  ];

  const draw = async () => {
    let g;
    try { g = await api('GET', '/api/google'); } catch (error) {
      box.replaceChildren(h('h2', null, '🔌 חיבור לחשבון גוגל'), h('p', { class: 'muted' }, error.message));
      return;
    }
    const status = (ok) => h('span', { class: `badge ${ok ? 'ok' : 'warn'}` }, ok ? 'מחובר' : 'לא מחובר');
    const backup = g.drive && h('button', { class: 'btn sm', onclick: (e) => act(e.currentTarget, () => api('POST', '/api/drive/backup'), '💾 גובה לדרייב') }, 'גבה עכשיו');
    box.replaceChildren(
      h('h2', null, '🔌 חיבור לחשבון גוגל'),
      h('div', { class: 'row' }, h('span', { class: 'grow' }, '📈 נתוני הערוץ מיוטיוב'), status(g.youtube)),
      h('div', { class: 'row' }, h('span', { class: 'grow' }, '📁 גוגל דרייב (תיקייה לכל סרטון + גיבוי)'), backup, status(g.drive)),
      ...(g.source === 'env' ? [h('p', { class: 'muted small' }, 'החיבור מוגדר דרך קובץ ‎.env.')] : g.connected ? connected() : setup(g)).filter(Boolean),
    );
  };

  draw();
  return box;
}

/** Asking for an assistant's API key: free Gemini (offered first) or paid Claude. Shared by the assistant screen and the settings. */
const ASSISTANTS = {
  gemini: {
    tab: '🆓 Gemini (חינם)', placeholder: 'AIza…', aria: 'מפתח API של Gemini',
    steps: () => [
      h('li', null, 'נכנסים ל-', externalLink('https://aistudio.google.com/apikey', 'aistudio.google.com/apikey'), ' עם חשבון הגוגל שלך.'),
      h('li', null, 'לוחצים ', h('b', null, 'Create API key'), ' (אם מבקשים לבחור פרויקט, אפשר ליצור חדש). בלי כרטיס אשראי.'),
      h('li', null, 'מעתיקים את המפתח ומדביקים כאן.')],
    note: 'המפתח חינמי, עם מגבלת שימוש יומית (לרוב מספיקה בהחלט). בשירות החינמי גוגל עשויה להשתמש במה שנשלח אליה כדי לשפר את המוצרים שלה, ולכן כדאי לא לכתוב לעוזר מידע פרטי. מה שאתה כותב לעוזר, ורשימת הסרטונים, הרעיונות ונתוני הערוץ, נשלחים לגוגל כדי שיוכל לענות. שום דבר לא נשלח אם אתה לא משתמש בעוזר.',
  },
  claude: {
    tab: 'Claude (בתשלום)', placeholder: 'sk-ant-…', aria: 'מפתח API של Anthropic',
    steps: () => [
      h('li', null, 'נכנסים ל-', externalLink('https://console.anthropic.com/settings/keys', 'console.anthropic.com'), ' ויוצרים חשבון.'),
      h('li', null, 'ב-', externalLink('https://console.anthropic.com/settings/billing', 'Billing'), ' מוסיפים אשראי. התשלום לפי שימוש, וכל תשובה עולה בדרך כלל כמה סנטים.'),
      h('li', null, 'ב-', externalLink('https://console.anthropic.com/settings/keys', 'API keys'), ' יוצרים מפתח, מעתיקים ומדביקים כאן.')],
    note: 'ההבדל: Claude מדויק יותר בעברית, אבל עולה כסף לפי שימוש. מה שאתה כותב לעוזר, ורשימת הסרטונים, הרעיונות ונתוני הערוץ, נשלחים ל-Anthropic כדי שיוכל לענות. שום דבר לא נשלח אם אתה לא משתמש בעוזר.',
  },
};

function assistantKeyForm(onDone) {
  const body = h('div', { class: 'stack' });
  const tabs = h('div', { class: 'chips', role: 'group', 'aria-label': 'איזה עוזר' });
  const choose = (provider) => {
    const info = ASSISTANTS[provider];
    const key = h('input', { class: 'field grow', type: 'password', dir: 'ltr', placeholder: info.placeholder, required: true, autocomplete: 'off', 'aria-label': info.aria });
    const save = h('button', { class: 'btn primary', type: 'submit' }, 'שמור ובדוק');
    [...tabs.children].forEach((tab) => tab.setAttribute('aria-pressed', String(tab.dataset.provider === provider)));
    body.replaceChildren(
      h('ol', { class: 'steps' }, ...info.steps()),
      h('form', { class: 'row', onsubmit: async (e) => {
        e.preventDefault();
        save.disabled = true;
        try {
          await api('PUT', '/api/assistant', { provider, api_key: key.value });
          toast('✅ העוזר מוכן');
          await onDone();
        } catch (error) {
          toast(error.message, 'error');
          save.disabled = false;
        }
      } }, key, save),
      h('p', { class: 'muted small' }, info.note));
  };
  Object.entries(ASSISTANTS).forEach(([provider, info]) =>
    tabs.append(h('button', { type: 'button', class: 'chip', 'data-provider': provider, 'aria-pressed': 'false', onclick: () => choose(provider) }, info.tab)));
  choose('gemini');
  return h('div', { class: 'stack' }, tabs, body);
}

/** The chat: bubbles, a text box, and a few starter questions. Text is only ever inserted as text. */
function chatUi(a, redraw) {
  const log = h('div', { class: 'chat-log', role: 'log', 'aria-live': 'polite' });
  const scroll = () => { log.scrollTop = log.scrollHeight; };
  const add = (role, text, extra = '') => { const m = h('div', { class: `msg ${role} ${extra}` }, text); log.append(m); scroll(); return m; };
  const starters = h('div', { class: 'chips' });
  a.messages.forEach((m) => add(m.role === 'user' ? 'user' : 'bot', m.text));

  const input = h('textarea', { class: 'field grow', rows: 2, maxLength: 4000, placeholder: 'שאל את העוזר…  (Enter לשליחה, Shift+Enter לשורה חדשה)', 'aria-label': 'הודעה לעוזר' });
  const send = h('button', { class: 'btn primary', type: 'submit' }, 'שלח');
  let busy = false;

  const submit = async (raw) => {
    const text = raw.trim();
    if (!text || busy) return;
    busy = true; send.disabled = true; input.value = ''; starters.hidden = true;
    add('user', text);
    const pending = add('bot', '⏳ חושב…', 'pending');
    try {
      const { reply } = await api('POST', '/api/assistant/chat', { message: text });
      pending.textContent = reply;
      pending.classList.remove('pending');
    } catch (error) {
      pending.textContent = error.message;
      pending.className = 'msg bot error';
      input.value = text; // the question was not kept, so it can simply be sent again
    } finally {
      busy = false; send.disabled = false; input.focus(); scroll();
    }
  };

  ['הצע לי 5 כותרות לסרטון הבא', 'איך אפשר להגדיל את הצפיות בערוץ?', 'תכנן לי שבוע תוכן', 'איך מתחילים סרטון בצורה שתופסת?'].forEach((q) =>
    starters.append(h('button', { type: 'button', class: 'chip', onclick: () => submit(q) }, q)));
  starters.hidden = a.messages.length > 0;
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(input.value); } });

  return h('div', null,
    h('div', { class: 'card stack' },
      h('div', { class: 'video-head' },
        h('span', { class: 'muted small' }, `🤖 ${a.model} · מכיר את הסרטונים, הרעיונות והיעדים שלך`),
        h('button', { class: 'btn sm', onclick: async () => { await api('DELETE', '/api/assistant/chat'); await redraw(); } }, 'שיחה חדשה')),
      log, starters,
      h('form', { class: 'row', onsubmit: (e) => { e.preventDefault(); submit(input.value); } }, input, send)));
}

/** "Suggest ideas" for the ideas screen. Saving one adds it to the list below. */
function aiIdeasCard() {
  const box = h('div', { class: 'card stack' });
  const shown = []; // titles already suggested, so "more ideas" does not repeat them

  const suggestion = (idea) => {
    const save = h('button', { class: 'btn sm', onclick: async () => {
      save.disabled = true;
      try {
        await api('POST', '/api/ideas', { text: idea.title });
        save.textContent = '✔ נשמר';
        toast('💡 נשמר ברשימת הרעיונות');
        await refreshCurrent();
      } catch (error) { toast(error.message, 'error'); save.disabled = false; }
    } }, '💡 שמור');
    return h('div', { class: 'item' },
      h('div', { class: 'grow' }, h('div', { class: 'task-title' }, idea.title), idea.why ? h('div', { class: 'muted small' }, idea.why) : null), save);
  };

  const draw = async () => {
    let a;
    try { a = await api('GET', '/api/assistant'); } catch { return; }
    if (!a.configured) {
      box.replaceChildren(h('h2', null, '🤖 רעיונות מהעוזר'),
        h('div', { class: 'row' }, h('span', { class: 'grow muted' }, 'עוזר בינה מלאכותית שמציע רעיונות שמתאימים לערוץ שלך.'),
          h('button', { class: 'btn', onclick: () => show('assistant') }, 'להפעלת העוזר')));
      return;
    }
    const topic = h('input', { class: 'field grow', placeholder: 'נושא (לא חובה), למשל: עריכה בטלפון', maxLength: 300, 'aria-label': 'נושא לרעיונות' });
    const results = h('div', { class: 'stack' });
    const go = h('button', { class: 'btn primary', type: 'submit' }, '✨ הצע רעיונות');
    box.replaceChildren(h('h2', null, '🤖 רעיונות מהעוזר'),
      h('form', { class: 'row', onsubmit: async (e) => {
        e.preventDefault();
        go.disabled = true; go.textContent = '⏳ חושב…';
        try {
          const { ideas } = await api('POST', '/api/assistant/ideas', { topic: topic.value, avoid: shown.slice(-30) });
          shown.push(...ideas.map((i) => i.title));
          results.replaceChildren(...ideas.map(suggestion));
        } catch (error) { toast(error.message, 'error'); }
        go.disabled = false; go.textContent = shown.length ? '✨ עוד רעיונות' : '✨ הצע רעיונות';
      } }, topic, go), results);
  };
  draw();
  return box;
}

/** The assistant's settings: which one is on, removing its key, and adding the other one. */
function assistantCard() {
  const box = h('div', { class: 'card stack' });
  const draw = async () => {
    let a;
    try { a = await api('GET', '/api/assistant'); } catch (error) { box.replaceChildren(h('h2', null, '🤖 עוזר בינה מלאכותית'), h('p', { class: 'muted' }, error.message)); return; }
    box.replaceChildren(h('h2', null, '🤖 עוזר בינה מלאכותית'),
      h('div', { class: 'row' }, h('span', { class: 'grow' }, `עוזר: ${a.model}`), h('span', { class: `badge ${a.configured ? 'ok' : 'warn'}` }, a.configured ? 'מוכן' : 'לא מוגדר')),
      a.configured
        ? h('div', { class: 'stack' },
            h('div', { class: 'row' }, h('span', { class: 'muted small grow' }, `מפתח ${a.key_hint}${a.source === 'env' ? ' (מקובץ ‎.env)' : ''}`),
              a.source === 'app' ? twoStep('הסר מפתח', 'להסיר?', async () => { await api('DELETE', '/api/assistant'); await draw(); }) : null),
            h('details', null, h('summary', null, 'החלפת עוזר או מפתח'), assistantKeyForm(draw)))
        : assistantKeyForm(draw));
  };
  draw();
  return box;
}

function weekCardBody(w) {
  return h('div', null,
    h('div', { class: 'stat' }, h('span', null, 'העלאות השבוע'), h('strong', null, `${w.done}/${w.goal}`)),
    progressBar(w.done, w.goal),
    h('p', { class: 'muted small' }, weekMessage(w)),
    w.streak >= 2 && h('span', { class: 'badge ok' }, `🔥 ${w.streak} שבועות ברצף ביעד`));
}

function videoCard(v) {
  const current = v.steps.find((s) => s.state === 'current');
  const stepper = h('div', { class: 'stepper' }, v.steps.map((s) =>
    h('div', { class: s.state }, `${s.state === 'done' ? '✅' : s.emoji} ${s.name}`, h('small', null, s.state === 'done' ? 'בוצע' : s.due_label))));
  const done = (e) => act(e.currentTarget, () => api('POST', `/api/videos/${v.id}/done`, { step: current.key }), (r) => (r.changed ? r.message : 'כבר מסומן ✅'));
  const snooze = (e) => act(e.currentTarget, () => api('POST', `/api/videos/${v.id}/snooze`, { step: current.key }), '⏰ נדחה למחר');
  return h('div', { class: 'card' },
    h('div', { class: 'video-head' },
      h('div', null, h('div', { class: 'video-title' }, v.title), h('div', { class: 'muted small' }, `פרסום: ${longDate(v.publish_date)}`)),
      deleteButton(`מחק את ${v.title}`, () => api('DELETE', `/api/videos/${v.id}`))),
    stepper,
    h('div', { class: 'row' },
      current && h('button', { class: 'btn primary sm', onclick: done }, `✅ ${current.name} בוצע`),
      current && h('button', { class: 'btn sm', onclick: snooze }, '⏰ מחר'),
      v.drive_url && externalLink(v.drive_url, '📁 תיקייה בדרייב')));
}

function ideaRow(idea) {
  const panel = h('div', { class: 'stack', hidden: true });
  const { input, chips } = dateChooser(7);
  const create = h('button', { class: 'btn primary sm', type: 'submit' }, '🎬 תזמן');
  panel.append(h('form', { class: 'stack', onsubmit: (e) => {
    e.preventDefault();
    act(create, () => api('POST', `/api/ideas/${idea.id}/video`, { date: input.value }), '🎬 הרעיון הפך לסרטון מתוזמן');
  } }, h('div', { class: 'row' }, h('label', { class: 'row small muted' }, 'מתי לפרסם?', input), create), chips));
  return h('li', { class: 'item' },
    h('div', { class: 'grow' },
      h('div', { class: 'row' }, h('div', { class: 'grow task-title' }, idea.text),
        h('div', { class: 'actions' },
          h('button', { class: 'btn sm', onclick: () => { panel.hidden = !panel.hidden; } }, '🎬 הפוך לסרטון'),
          deleteButton(`מחק את הרעיון`, () => api('DELETE', `/api/ideas/${idea.id}`)))),
      panel));
}

// ---------- navigation ----------
let current = { name: null, refresh: null };
let renderToken = 0;

async function show(name) {
  const view = views[name] || views.today;
  const token = ++renderToken;
  document.querySelectorAll('#nav button').forEach((b) => {
    if (b.dataset.view === name) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  $('#title').textContent = view.title;
  closeInbox();
  const root = h('div');
  try {
    const refresh = await view.mount(root);
    if (token !== renderToken) return; // the user already moved on
    $('#view').replaceChildren(root);
    current = { name, refresh };
    try { localStorage.setItem('view', name); } catch { /* storage may be unavailable */ }
  } catch (error) {
    if (token !== renderToken) return;
    $('#view').replaceChildren(h('div', { class: 'card error-card' }, `לא הצלחתי לטעון: ${error.message}`));
    current = { name, refresh: null };
  }
}

async function refreshCurrent() {
  if (!current.refresh) return;
  const token = renderToken;
  try { await current.refresh(); } catch (error) { if (token === renderToken) toast(error.message, 'error'); }
}

// ---------- inbox & notifications ----------
let lastSeenId = null;
let feedState = { items: [], unread: 0 };

function drawBadge() {
  const badge = $('#badge');
  badge.hidden = feedState.unread === 0;
  badge.textContent = feedState.unread;
  document.title = feedState.unread ? `(${feedState.unread}) Creator Desk` : 'Creator Desk';
}

function drawInbox() {
  const box = $('#inbox');
  const clear = h('button', { class: 'btn sm', onclick: async () => { feedState = await api('POST', '/api/feed/read'); drawBadge(); drawInbox(); } }, 'סמן הכל כנקרא');
  box.replaceChildren(h('div', { class: 'video-head' }, h('h2', null, 'התראות'), clear),
    feedState.items.length
      ? h('ul', { class: 'list' }, feedState.items.map((item) => h('li', { class: 'item' },
          h('p', { class: item.read ? 'muted' : '' }, item.text),
          h('span', { class: 'muted small' }, new Date(item.time).toLocaleString('he-IL', { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'numeric' })),
          item.link && externalLink(item.link, 'פתיחת הקישור'))))
      : empty('🔔', 'אין התראות עדיין'));
}

function closeInbox() { $('#inbox').hidden = true; $('#bell').setAttribute('aria-expanded', 'false'); }

async function pollFeed() {
  try {
    const state = await api('GET', `/api/feed?focused=${document.hasFocus() ? 1 : 0}`);
    if (lastSeenId !== null) {
      state.items.filter((item) => item.id > lastSeenId).reverse().forEach((item) => toast(item.text, 'info', 7000));
    }
    lastSeenId = state.last_id;
    feedState = state;
    drawBadge();
    if (!$('#inbox').hidden) drawInbox();
  } catch { /* the app may be closing */ }
}

// ---------- start ----------
document.querySelectorAll('#nav button').forEach((b) => b.addEventListener('click', () => show(b.dataset.view)));
$('#bell').addEventListener('click', () => {
  const open = $('#inbox').hidden;
  if (open) drawInbox();
  $('#inbox').hidden = !open;
  $('#bell').setAttribute('aria-expanded', String(open));
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeInbox(); });
window.addEventListener('focus', () => { refreshCurrent(); pollFeed(); });

let first = 'today';
try { first = localStorage.getItem('view') || 'today'; } catch { /* ignore */ }
show(views[first] ? first : 'today');
pollFeed();
setInterval(pollFeed, 15000);
setInterval(() => { if (!document.hidden) refreshCurrent(); }, 30000);
