import {VoiceIO} from '/audio.js';
import * as view from '/view.js';

const {el} = view;
const NO_SPEECH = /no (clear )?speech/i;

const state = {
  screen: 'home',
  online: false,
  closeCode: 0,
  muted: false,
  typing: false,
  thinking: false, // between the end of an utterance and its transcript
  firstCall: !localStorage.getItem('tars_voice_seen'),
};
const busyTurns = new Set(); // turns started and not yet done
const ignoredTurns = new Set(); // turns whose remaining audio the owner interrupted
let ws;
let reconnectTimer;
let audioTurn = null; // the turn the next binary frame belongs to
let holdTimer; // safety net: a hold never outlives its capture by more than 10 s

const io = new VoiceIO({
  level: value => view.setLevel(value),
  speechStart(preRoll) {
    // Noise can start a capture too, so replies are held, not dropped, until it is confirmed.
    io.hold();
    clearTimeout(holdTimer);
    send({type: 'speech_start'});
    if (preRoll) sendAudio(preRoll);
    refresh();
  },
  frame: sendAudio,
  speechEnd() {
    holdTimer = setTimeout(releaseHold, 10000);
    send({type: 'speech_end'});
    state.thinking = true;
    refresh();
  },
  playing: refresh,
  blocked() {
    telemetry('audio_playback_failed');
    view.note('Sound is paused by the browser. Tap anywhere to hear Tars.');
    document.addEventListener('pointerdown', () => io.unlock().then(() => io.playNext()), {once: true});
  },
});

function telemetry(event, code = null) {
  fetch('/api/diagnostics/client', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({event, code}),
    keepalive: true,
  }).catch(() => {});
}

function send(message) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}

function sendAudio(buffer) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(buffer);
}

// One place decides what the monolith and status line show.
function refresh() {
  const inCall = state.screen === 'call';
  io.enabled = inCall && state.online && !state.typing;
  let mono = 'ready';
  let word = 'Ready';
  let tone = '';
  let sub = '';
  if (!state.online) {
    [mono, word, tone] = inCall || state.closeCode ? ['error', 'Offline', 'bad'] : ['ready', 'Connecting', ''];
    if (state.closeCode === 4429) sub = 'Tars Voice is open somewhere else';
    else if (inCall) sub = 'Reconnecting…';
  } else if (!inCall) {
    [mono, word] = ['ready', 'Ready'];
  } else if (io.playingTurn) {
    [mono, word, tone, sub] = ['speaking', 'Speaking', 'acc', 'tap to stop'];
  } else if (io.capturing) {
    [mono, word, tone] = ['hearing', 'Listening', 'on'];
  } else if (state.thinking || busyTurns.size) {
    [mono, word, tone] = ['thinking', 'Thinking', 'acc'];
  } else if (state.muted) {
    [mono, word, sub] = ['muted', 'Muted', state.typing ? 'typing · replies stay quiet' : 'mic off'];
  } else {
    [mono, word, tone] = ['listening', 'Listening', 'on'];
    if (state.firstCall) sub = 'Just talk. Pause when you’re done.';
  }
  view.setMonolith(mono);
  view.setStatus(word, tone, sub);
}

function connect() {
  clearTimeout(reconnectTimer);
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => telemetry('websocket_open');
  ws.onclose = event => {
    telemetry('websocket_close', event.code);
    state.online = false;
    state.closeCode = event.code;
    state.thinking = false;
    busyTurns.clear();
    if (io.capturing) io.finish();
    interruptPlayback(false);
    view.setReady(false);
    refresh();
    reconnectTimer = setTimeout(connect, event.code === 4429 ? 4000 : 1600);
  };
  ws.onerror = () => telemetry('websocket_error');
  ws.onmessage = event => handle(event.data);
}

function handle(data) {
  if (data instanceof ArrayBuffer) {
    const turnId = audioTurn;
    audioTurn = null;
    if (state.screen !== 'call') return; // never talk outside a call
    if (turnId && !ignoredTurns.has(turnId)) io.enqueue(turnId, new Blob([data], {type: 'audio/wav'}));
    return;
  }
  const event = JSON.parse(data);
  switch (event.type) {
    case 'ready':
      state.online = true;
      state.closeCode = 0;
      view.setReady(true);
      if (state.screen === 'call') {
        send({type: 'call_start'}); // reconnected mid-call: the new connection is part of it
        send({type: 'quiet', on: state.typing});
      } else {
        attemptAutoStart();
      }
      break;
    case 'partial_transcript':
      if (io.capturing) view.interim(event.text);
      break;
    case 'transcript':
      state.thinking = false;
      if (io.holding) interruptPlayback(false); // confirmed speech: the held reply is dropped
      if (io.capturing) io.hold(); // already talking again
      view.finalizeInterim(event.text);
      markSeen();
      break;
    case 'status':
      if (event.turnId) busyTurns.add(event.turnId);
      break;
    case 'answer':
      if (event.turnId) busyTurns.add(event.turnId);
      view.tars(event.turnId, event.text);
      break;
    case 'audio':
      audioTurn = event.turnId;
      break;
    case 'done':
      busyTurns.delete(event.turnId);
      break;
    case 'error':
      state.thinking = false;
      if (!event.turnId) releaseHold(); // the capture was noise or failed; carry on
      if (event.turnId) busyTurns.delete(event.turnId);
      if (NO_SPEECH.test(event.message || '')) view.dropInterim();
      else view.note(event.message || 'Something went wrong.');
      break;
  }
  refresh();
}

function markSeen() {
  if (!state.firstCall) return;
  state.firstCall = false;
  localStorage.setItem('tars_voice_seen', '1');
}

function interruptPlayback(tellServer) {
  const turnId = io.stopPlayback();
  if (turnId) ignoredTurns.add(turnId);
  clearTimeout(holdTimer);
  if (tellServer) send({type: 'interrupt'});
}

function releaseHold() {
  clearTimeout(holdTimer);
  if (!io.capturing) io.release();
}

async function startCall({typing = false} = {}) {
  if (!state.online) return;
  view.clearTimeline();
  busyTurns.clear();
  ignoredTurns.clear();
  state.screen = 'call';
  view.showScreen('call');
  await io.unlock().catch(() => {});
  send({type: 'call_start'});
  if (typing) {
    setTyping(true);
  } else {
    await useMic();
  }
  telemetry('handsfree_enabled');
  refresh();
}

async function useMic() {
  try {
    await io.startMic();
    telemetry('microphone_ready');
    localStorage.setItem('tars_voice_autostart', '1');
    return true;
  } catch {
    telemetry('microphone_denied');
    view.note('The microphone is blocked. Allow it in your browser’s site settings, or type instead.');
    setTyping(true);
    return false;
  }
}

function setTyping(typing) {
  state.typing = typing;
  setMuted(typing);
  if (typing) interruptPlayback(true);
  send({type: 'quiet', on: typing});
  view.setTyping(typing);
  refresh();
}

function setMuted(muted) {
  state.muted = muted;
  io.setMuted(muted);
  view.setMuted(muted);
  refresh();
}

function endCall() {
  send({type: 'end_call'});
  interruptPlayback(false);
  io.stopMic();
  localStorage.removeItem('tars_voice_autostart');
  state.screen = 'home';
  state.thinking = false;
  if (state.typing) setTyping(false);
  setMuted(false);
  view.showScreen('home');
  telemetry('handsfree_disabled');
  refresh();
}

// Reopening the app during a call picks the call back up, if the mic is already allowed.
async function attemptAutoStart() {
  if (localStorage.getItem('tars_voice_autostart') !== '1' || !navigator.permissions?.query) return;
  try {
    const permission = await navigator.permissions.query({name: 'microphone'});
    if (permission.state === 'granted') await startCall();
  } catch {
    // Not every mobile browser supports permission queries.
  }
}

el.talk.addEventListener('click', () => startCall());
el.typeFirst.addEventListener('click', () => startCall({typing: true}));
el.type.addEventListener('click', () => setTyping(true));
el.mute.addEventListener('click', () => setMuted(!state.muted));
el.end.addEventListener('click', endCall);
el.monolith.addEventListener('click', () => {
  if (io.playingTurn) interruptPlayback(true);
});
el.speak.addEventListener('click', async () => {
  if (!io.micOn && !(await useMic())) return;
  setTyping(false);
});
el.composer.addEventListener('submit', event => {
  event.preventDefault();
  const text = el.text.value.trim();
  if (!text || !state.online) return;
  el.text.value = '';
  view.you(text);
  markSeen();
  send({type: 'text_turn', text});
});

// Keep the layout inside the visible area when the on-screen keyboard opens.
if (window.visualViewport) {
  const fit = () => el.app.style.setProperty('--app-h', `${window.visualViewport.height}px`);
  window.visualViewport.addEventListener('resize', fit);
  fit();
}

telemetry('app_loaded');
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js')
    .then(() => telemetry('service_worker_ready'))
    .catch(() => telemetry('service_worker_failed'));
}
refresh();
connect();
