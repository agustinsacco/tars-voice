// Microphone capture, turn detection, and reply playback.
// The detection tuning is unchanged from the original client: an adaptive noise
// floor, 85 ms onset (150 ms while Tars is speaking), and 620 ms of silence to end.

const BATCH_BYTES = 1280; // 40 ms of 16 kHz PCM16 per WebSocket frame, not one per 2.7 ms render quantum
const PRE_ROLL_FRAMES = 150; // about 400 ms of audio from just before speech started

function concat(chunks) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(new Uint8Array(chunk), offset);
    offset += chunk.byteLength;
  }
  return out.buffer;
}

export class VoiceIO {
  // handlers: level(value), speechStart(preRoll), frame(buffer), speechEnd(), playing(turnId|null), blocked()
  constructor(handlers) {
    this.on = handlers;
    this.context = null;
    this.stream = null;
    this.muted = false;
    this.enabled = false; // a call is active and the socket is ready
    this.capturing = false;
    this.playingTurn = null; // turn whose audio is audible right now
    this.playerTurn = null; // turn of the loaded clip, even while held
    this.holding = false;
    this.queue = [];
    this.player = null;
    this.playerUrl = null;
    this.level = 0;
    this.noiseFloor = .004;
    this.preRoll = [];
    this.batch = [];
    this.batchBytes = 0;
    this.candidateAt = 0;
    this.lastVoice = 0;
    this.captureStarted = 0;
  }

  get micOn() {
    return Boolean(this.stream);
  }

  async unlock() {
    if (!this.context) this.context = new AudioContext({latencyHint: 'interactive'});
    await this.context.resume();
  }

  async startMic() {
    if (this.stream) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true},
    });
    await this.unlock();
    await this.context.audioWorklet.addModule('/audio-worklet.js');
    const source = this.context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(this.context, 'tars-capture');
    const silent = this.context.createGain();
    silent.gain.value = 0;
    source.connect(worklet);
    worklet.connect(silent).connect(this.context.destination);
    worklet.port.onmessage = ({data}) => this.frame(data);
    this.stream = stream;
    this.nodes = [source, worklet, silent];
  }

  stopMic() {
    if (this.capturing) this.finish();
    this.stream?.getTracks().forEach(track => track.stop());
    this.nodes?.forEach(node => node.disconnect());
    this.stream = null;
    this.nodes = null;
    this.preRoll = [];
    this.level = 0;
    this.on.level(0);
  }

  setMuted(muted) {
    this.muted = muted;
    if (muted && this.capturing) this.finish();
  }

  frame({pcm, rms}) {
    const now = performance.now();
    this.level = this.level * .76 + rms * .24;
    this.on.level(this.muted ? 0 : this.level);
    if (!this.capturing) {
      this.preRoll.push(pcm);
      while (this.preRoll.length > PRE_ROLL_FRAMES) this.preRoll.shift();
    }
    if (!this.enabled || this.muted) return;

    const speaking = Boolean(this.playingTurn);
    if (!this.capturing && !speaking && this.level < .025) {
      this.noiseFloor = this.noiseFloor * .995 + this.level * .005;
    }
    const ambient = Math.max(.012, Math.min(.04, this.noiseFloor * 2.8 + .006));
    const threshold = speaking ? Math.max(.052, ambient * 1.8) : ambient;
    const voiced = this.level > threshold;

    if (!this.capturing) {
      if (voiced) {
        if (!this.candidateAt) this.candidateAt = now;
        if (now - this.candidateAt >= (speaking ? 150 : 85)) this.start(speaking);
      } else if (now - this.candidateAt > 110) {
        this.candidateAt = 0;
      }
      return;
    }

    if (voiced) this.lastVoice = now;
    this.batch.push(pcm);
    this.batchBytes += pcm.byteLength;
    if (this.batchBytes >= BATCH_BYTES) this.flush();
    if (now - this.lastVoice > 620 && now - this.captureStarted > 360) this.finish();
  }

  start(wasSpeaking) {
    const preRoll = wasSpeaking ? this.preRoll.slice(-60) : this.preRoll;
    this.capturing = true;
    this.captureStarted = this.lastVoice = performance.now();
    this.candidateAt = 0;
    this.preRoll = [];
    this.on.speechStart(preRoll.length ? concat(preRoll) : null);
  }

  flush() {
    if (!this.batch.length) return;
    this.on.frame(concat(this.batch));
    this.batch = [];
    this.batchBytes = 0;
  }

  finish() {
    if (!this.capturing) return;
    this.flush();
    this.capturing = false;
    this.candidateAt = 0;
    this.on.speechEnd();
  }

  enqueue(turnId, blob) {
    this.queue.push({turnId, blob});
    this.playNext();
  }

  // Replies are held from the start of every capture until it is confirmed as speech
  // (stop) or turns out to be noise (release), so Tars never talks over you and noise
  // never costs you a reply.
  hold() {
    this.holding = true;
    this.player?.pause();
    this.setPlaying(null);
    return this.playerTurn || this.queue[0]?.turnId || null;
  }

  release() {
    if (!this.holding) return;
    this.holding = false;
    if (!this.player) {
      this.playNext();
      return;
    }
    this.setPlaying(this.playerTurn);
    this.player.play().catch(() => this.player?.onended?.());
  }

  stopPlayback() {
    const turnId = this.playerTurn || this.queue[0]?.turnId || null;
    this.player?.pause();
    if (this.playerUrl) URL.revokeObjectURL(this.playerUrl);
    this.player = this.playerUrl = this.playerTurn = null;
    this.queue = [];
    this.holding = false;
    this.setPlaying(null);
    return turnId;
  }

  setPlaying(turnId) {
    if (this.playingTurn === turnId) return;
    this.playingTurn = turnId;
    this.on.playing(turnId);
  }

  playNext() {
    if (this.player || this.holding) return;
    const next = this.queue.shift();
    if (!next) {
      this.setPlaying(null);
      return;
    }
    this.playerUrl = URL.createObjectURL(next.blob);
    this.player = new Audio(this.playerUrl);
    this.playerTurn = next.turnId;
    this.setPlaying(next.turnId);
    const done = () => {
      if (this.playerUrl) URL.revokeObjectURL(this.playerUrl);
      this.player = this.playerUrl = this.playerTurn = null;
      this.playNext();
    };
    this.player.onended = done;
    this.player.onerror = done;
    this.player.play().catch(() => {
      URL.revokeObjectURL(this.playerUrl);
      this.player = this.playerUrl = this.playerTurn = null;
      this.queue.unshift(next);
      this.setPlaying(null);
      this.on.blocked();
    });
  }

  // One soft tick when a background result lands.
  tick() {
    const ctx = this.context;
    if (!ctx || ctx.state !== 'running') return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const t = ctx.currentTime;
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, t);
    osc.frequency.exponentialRampToValueAtTime(1320, t + .06);
    gain.gain.setValueAtTime(.0001, t);
    gain.gain.exponentialRampToValueAtTime(.08, t + .01);
    gain.gain.exponentialRampToValueAtTime(.0001, t + .14);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + .16);
  }
}
