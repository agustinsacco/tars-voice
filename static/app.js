const $ = selector => document.querySelector(selector);
const orb = $('#orb');
const status = $('#status');
const hint = $('#hint');
const startButton = $('#start');
const stopButton = $('#stop');
const transcript = $('#transcript');
const answer = $('#answer');
const connection = $('.connection');
const connectionLabel = $('#connection-label');

let ws;
let wsReady = false;
let reconnectTimer;
let context;
let source;
let worklet;
let stream;
let conversationActive = false;
let capturing = false;
let captureStarted = 0;
let voiceCandidateAt = 0;
let lastVoice = 0;
let preRoll = [];
let smoothedLevel = 0;
let noiseFloor = .004;
let phase = 'idle';
let pendingAudio = false;
let audioQueue = [];
let player = null;
let playerUrl = null;
let turnDone = true;

function telemetry(event, code = null) {
  fetch('/api/diagnostics/client', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({event, code}),
    keepalive: true,
  }).catch(() => {});
}

function setConnection(online, label) {
  connection.classList.toggle('online', online);
  connectionLabel.textContent = label;
}

function setMode(mode, label, detail) {
  phase = mode;
  orb.dataset.mode = mode;
  status.textContent = label;
  hint.textContent = detail;
}

function setCaption(element, text, style = '') {
  element.textContent = text;
  element.classList.toggle('placeholder', style === 'placeholder');
  element.classList.toggle('interim', style === 'interim');
}

function connect() {
  clearTimeout(reconnectTimer);
  ws = new WebSocket(`wss://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    telemetry('websocket_open');
    setConnection(true, 'Secure');
  };
  ws.onclose = event => {
    telemetry('websocket_close', event.code);
    wsReady = false;
    startButton.disabled = true;
    capturing = false;
    setConnection(false, 'Reconnecting');
    setMode('error', event.code === 4429 ? 'Already active elsewhere' : 'Connection interrupted', event.code === 4429 ? 'Close the other voice session to continue' : 'Reconnecting automatically…');
    reconnectTimer = setTimeout(connect, event.code === 4429 ? 4000 : 1600);
  };
  ws.onerror = () => {
    telemetry('websocket_error');
    setConnection(false, 'Offline');
  };
  ws.onmessage = event => handleMessage(event.data);
}

async function handleMessage(data) {
  if (data instanceof ArrayBuffer) {
    if (!pendingAudio) return;
    pendingAudio = false;
    audioQueue.push(new Blob([data], {type: 'audio/wav'}));
    playNext();
    return;
  }

  const event = JSON.parse(data);
  if (event.type === 'ready') {
    wsReady = true;
    startButton.disabled = false;
    setConnection(true, 'Secure');
    if (conversationActive) {
      setMode('listening', 'Listening', 'Speak naturally — no button needed');
    } else {
      setMode('idle', 'Ready when you are', 'One tap starts a continuous conversation');
      attemptAutoStart();
    }
  } else if (event.type === 'listening') {
    setMode('listening', 'Listening', 'I’ll respond when you pause');
  } else if (event.type === 'partial_transcript') {
    if (capturing) setCaption(transcript, event.text, 'interim');
  } else if (event.type === 'status') {
    if (!capturing) setMode('thinking', event.safeLabel || 'Thinking', 'You can interrupt at any time');
  } else if (event.type === 'transcript') {
    setCaption(transcript, event.text);
    setCaption(answer, 'Thinking…', 'interim');
    turnDone = false;
    setMode('thinking', 'Thinking', 'You can keep speaking to add a follow-up');
  } else if (event.type === 'answer') {
    if (answer.classList.contains('interim') || answer.classList.contains('placeholder')) setCaption(answer, '');
    answer.textContent += event.text;
  } else if (event.type === 'audio') {
    pendingAudio = true;
  } else if (event.type === 'queued') {
    setMode('thinking', 'Follow-up heard', 'Finishing the current response first');
  } else if (event.type === 'done') {
    turnDone = true;
    if (!player && !audioQueue.length && !capturing) returnToListening();
  } else if (event.type === 'error') {
    setMode('error', event.message || 'Something went wrong', 'Listening will resume automatically');
    setTimeout(() => {
      if (conversationActive && !capturing) returnToListening();
    }, 1200);
  }
}

async function ensureMic() {
  if (context) return;
  stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  context = new AudioContext({latencyHint: 'interactive'});
  await context.audioWorklet.addModule('/audio-worklet.js');
  source = context.createMediaStreamSource(stream);
  worklet = new AudioWorkletNode(context, 'tars-capture');
  source.connect(worklet);
  const silent = context.createGain();
  silent.gain.value = 0;
  worklet.connect(silent).connect(context.destination);
  worklet.port.onmessage = ({data}) => audioFrame(data);
  telemetry('microphone_ready');
}

async function startConversation(userInitiated = true) {
  if (!wsReady) return;
  try {
    await ensureMic();
    await context.resume();
    if (context.state !== 'running') throw new Error('Audio context is suspended');
    conversationActive = true;
    localStorage.setItem('tars_voice_autostart', '1');
    startButton.classList.add('hidden');
    stopButton.classList.remove('hidden');
    setCaption(transcript, 'Listening…', 'interim');
    setMode('listening', 'Listening', 'Speak naturally — no button needed');
    if (userInitiated) telemetry('handsfree_enabled');
  } catch {
    conversationActive = false;
    startButton.classList.remove('hidden');
    stopButton.classList.add('hidden');
    telemetry('microphone_denied');
    setMode('error', 'Microphone access needed', 'Allow microphone access, then tap Start conversation');
  }
}

async function attemptAutoStart() {
  if (localStorage.getItem('tars_voice_autostart') !== '1' || !navigator.permissions?.query) return;
  try {
    const permission = await navigator.permissions.query({name: 'microphone'});
    if (permission.state === 'granted') await startConversation(false);
  } catch {
    // Permission introspection is not supported by every mobile browser.
  }
}

function audioFrame({pcm, rms}) {
  const now = performance.now();
  smoothedLevel = smoothedLevel * .76 + rms * .24;
  orb.style.setProperty('--level', String(Math.min(1, smoothedLevel * 16)));

  if (!capturing) {
    preRoll.push(pcm);
    while (preRoll.length > 150) preRoll.shift();
  }
  if (!conversationActive || !wsReady) return;

  const canStart = phase === 'listening' || phase === 'thinking' || phase === 'speaking';
  if (!canStart && !capturing) return;

  if (!capturing && phase !== 'speaking' && smoothedLevel < .025) {
    noiseFloor = noiseFloor * .995 + smoothedLevel * .005;
  }
  const ambientThreshold = Math.max(.012, Math.min(.04, noiseFloor * 2.8 + .006));
  const threshold = phase === 'speaking' ? Math.max(.052, ambientThreshold * 1.8) : ambientThreshold;
  const voiced = smoothedLevel > threshold;

  if (!capturing) {
    if (voiced) {
      if (!voiceCandidateAt) voiceCandidateAt = now;
      const onset = phase === 'speaking' ? 150 : 85;
      if (now - voiceCandidateAt >= onset) startUtterance();
    } else if (now - voiceCandidateAt > 110) {
      voiceCandidateAt = 0;
    }
    return;
  }

  if (voiced) lastVoice = now;
  if (ws.readyState === WebSocket.OPEN) ws.send(pcm);
  if (now - lastVoice > 620 && now - captureStarted > 360) finishUtterance();
}

function startUtterance() {
  if (capturing || ws?.readyState !== WebSocket.OPEN) return;
  const wasSpeaking = phase === 'speaking';
  const bufferedAudio = wasSpeaking ? preRoll.slice(-60) : preRoll;
  stopPlayback();
  capturing = true;
  captureStarted = performance.now();
  lastVoice = captureStarted;
  voiceCandidateAt = 0;
  turnDone = false;
  setCaption(transcript, 'Listening…', 'interim');
  setCaption(answer, '', '');
  setMode('listening', 'Listening', 'Keep speaking — I’ll detect when you’re done');
  ws.send(JSON.stringify({type: 'speech_start'}));
  for (const chunk of bufferedAudio) ws.send(chunk);
  preRoll = [];
}

function finishUtterance() {
  if (!capturing) return;
  capturing = false;
  voiceCandidateAt = 0;
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type: 'speech_end'}));
  setMode('thinking', 'Understanding', 'Turning your speech into text');
}

function returnToListening() {
  if (!conversationActive) return;
  setMode('listening', 'Listening', 'Speak naturally — no button needed');
}

function stopPlayback() {
  if (player) {
    player.pause();
    player = null;
  }
  if (playerUrl) {
    URL.revokeObjectURL(playerUrl);
    playerUrl = null;
  }
  audioQueue = [];
  pendingAudio = false;
}

function playNext() {
  if (player || !audioQueue.length) {
    if (!player && !audioQueue.length && turnDone) returnToListening();
    return;
  }
  const blob = audioQueue.shift();
  playerUrl = URL.createObjectURL(blob);
  player = new Audio(playerUrl);
  setMode('speaking', 'Speaking', 'Just start talking to interrupt');
  player.onended = () => {
    URL.revokeObjectURL(playerUrl);
    playerUrl = null;
    player = null;
    playNext();
  };
  player.onerror = player.onended;
  player.play().catch(() => {
    telemetry('audio_playback_failed');
    player = null;
    setMode('error', 'Tap Start to enable audio', 'Your browser paused automatic playback');
    startButton.classList.remove('hidden');
  });
}

async function endConversation() {
  if (capturing) finishUtterance();
  conversationActive = false;
  localStorage.removeItem('tars_voice_autostart');
  stopPlayback();
  if (stream) stream.getTracks().forEach(track => track.stop());
  if (context) await context.close().catch(() => {});
  context = null;
  source = null;
  worklet = null;
  stream = null;
  preRoll = [];
  orb.style.setProperty('--level', '0');
  startButton.classList.remove('hidden');
  stopButton.classList.add('hidden');
  setCaption(transcript, 'Start speaking whenever you’re ready.', 'placeholder');
  setCaption(answer, 'I’ll respond here and out loud.', 'placeholder');
  setMode('idle', 'Conversation ended', 'Tap Start conversation whenever you want to resume');
  telemetry('handsfree_disabled');
}

startButton.addEventListener('click', () => startConversation(true));
stopButton.addEventListener('click', endConversation);

telemetry('app_loaded');
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js')
    .then(() => telemetry('service_worker_ready'))
    .catch(() => telemetry('service_worker_failed'));
}
connect();
