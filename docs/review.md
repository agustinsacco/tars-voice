# Reference implementation review

## Implemented

- Cloudflare Access JWT verification with exact owner identity
- Exact Host and WebSocket Origin validation
- Loopback gateway, Whisper, and Tars relay topology
- Installable dependency-free PWA
- AudioWorklet PCM downsampling
- Adaptive automatic turn detection
- Server-side WebRTC VAD
- Local interim and final Whisper transcription
- Incremental Tars NDJSON relay
- Sentence buffering and preloaded Piper TTS
- One active client, one active turn, and one queued follow-up
- Local playback interruption with honest cancellation semantics
- Metadata-only structured diagnostics and readiness probes
- Hardened systemd user-service templates

## Automated validation

The model-free suite covers Access JWT rejection cases, authenticated HTTP/WebSockets, VAD behavior, interim transcript delivery, bounded follow-up queueing, sentence sanitization, and a real incremental HTTP relay fixture. Python, JavaScript, the PWA manifest, and systemd units are validated by `make validate`.

GitHub Actions repeats the validation, builds the gateway container, and runs a full-history Gitleaks scan.

## Reference performance

On a Vulkan-capable AMD host using Whisper `large-v3-turbo` and Piper `en_US-lessac-medium`:

- final transcription completed in roughly 0.24–0.29 seconds
- first interim captions appeared around 0.7 seconds after speech onset
- sentence synthesis completed in roughly 0.03–0.12 seconds

The Tars model's time-to-first-answer was substantially larger than speech processing and remained the primary latency target.

## Acceptance boundary

Automated and headless checks do not prove microphone permissions, echo cancellation, mobile browser behavior, speaker playback, or reconnection quality. Each deployment needs the live acceptance checklist in [`validation.md`](validation.md).
