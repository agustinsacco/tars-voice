// DOM rendering only. Conversation text is always set with textContent, never as HTML.

const $ = selector => document.querySelector(selector);

export const el = {
  app: $('.app'),
  monolith: $('#monolith'),
  status: $('#status'),
  sub: $('#sub'),
  talk: $('#talk'),
  typeFirst: $('#type-first'),
  timeline: $('#timeline'),
  controls: $('#controls'),
  type: $('#type'),
  mute: $('#mute'),
  end: $('#end'),
  composer: $('#composer'),
  speak: $('#speak'),
  text: $('#text'),
};

const slabs = [...el.monolith.querySelectorAll('i')];
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let level = 0;
let lastJitter = 0;

export function showScreen(name) {
  el.app.dataset.screen = name;
}

export function setReady(ready) {
  el.talk.disabled = !ready;
  el.typeFirst.disabled = !ready;
}

// state: ready | listening | hearing | thinking | speaking | muted | error
export function setMonolith(state) {
  if (el.monolith.dataset.s === state) return;
  el.monolith.dataset.s = state;
  el.app.classList.toggle('speaking', state === 'speaking');
  const stoppable = state === 'speaking';
  el.monolith.setAttribute('aria-hidden', String(!stoppable));
  el.monolith.tabIndex = stoppable ? 0 : -1;
}

// tone: '' (quiet) | on (waiting for you) | acc (Tars acting) | bad (problem)
export function setStatus(word, tone = '', sub = '') {
  if (el.status.textContent !== word) el.status.textContent = word;
  el.status.dataset.tone = tone;
  el.sub.textContent = sub;
}

export function setLevel(value) {
  level = value;
}

// Slab heights follow the voice; a little per-slab jitter keeps it from looking like one bar.
function meter(now) {
  el.monolith.style.setProperty('--lvl', Math.min(1, level * 16).toFixed(3));
  if (!reducedMotion.matches && now - lastJitter > 110) {
    lastJitter = now;
    for (const slab of slabs) slab.style.setProperty('--j', (.55 + Math.random() * .45).toFixed(2));
  }
  requestAnimationFrame(meter);
}
requestAnimationFrame(meter);

export function setMuted(muted) {
  el.mute.setAttribute('aria-pressed', String(muted));
  el.mute.setAttribute('aria-label', muted ? 'Unmute' : 'Mute');
}

export function setTyping(typing) {
  el.app.classList.toggle('typing', typing);
  el.composer.hidden = !typing;
  el.controls.hidden = typing;
  if (typing) el.text.focus();
}

function scrollToEnd() {
  el.timeline.scrollTop = el.timeline.scrollHeight;
}

function entry(who, text, {id = '', interim = false} = {}) {
  const node = document.createElement('div');
  node.className = `turn ${who}`;
  if (id) node.dataset.id = id;
  const label = document.createElement('div');
  label.className = 'who';
  label.textContent = who === 'you' ? 'You' : 'Tars';
  const body = document.createElement('p');
  body.className = 'tx';
  body.classList.toggle('interim', interim);
  body.textContent = text;
  node.append(label, body);
  return node;
}

function find(id) {
  return id ? el.timeline.querySelector(`[data-id="${CSS.escape(id)}"]`) : null;
}

export function append(node) {
  el.timeline.append(node);
  while (el.timeline.children.length > 80) el.timeline.firstElementChild.remove();
  scrollToEnd();
}

export function you(text) {
  append(entry('you', text));
}

export function interim(text) {
  const node = find('interim');
  if (node) {
    node.querySelector('.tx').textContent = text;
    scrollToEnd();
  } else {
    append(entry('you', text, {id: 'interim', interim: true}));
  }
}

export function finalizeInterim(text) {
  const node = find('interim');
  if (!node) return you(text);
  node.removeAttribute('data-id');
  const body = node.querySelector('.tx');
  body.textContent = text;
  body.classList.remove('interim');
  scrollToEnd();
}

export function dropInterim() {
  find('interim')?.remove();
}

// Replies stream per turn; later chunks of the same turn extend the same entry.
export function tars(turnId, text) {
  const node = find(`tars-${turnId}`);
  if (node) {
    node.querySelector('.tx').textContent += text;
    scrollToEnd();
  } else {
    append(entry('tars', text, {id: `tars-${turnId}`}));
  }
}

export function note(text) {
  const node = document.createElement('p');
  node.className = 'note';
  node.textContent = text;
  append(node);
}

export function clearTimeline() {
  el.timeline.replaceChildren();
}
