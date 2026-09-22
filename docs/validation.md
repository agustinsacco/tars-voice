# Validation

## Local validation

Run the complete model-free suite:

```bash
make validate
```

It performs:

- Access JWT acceptance and rejection tests
- Authenticated HTTP and WebSocket contract tests
- Local health and diagnostics tests
- VAD behavior tests
- Interim transcription event tests
- Follow-up queue bounds
- Sentence buffering and speech-text sanitization
- Real incremental HTTP NDJSON relay adapter test
- Python bytecode compilation
- JavaScript syntax validation
- PWA manifest validation
- systemd unit verification when available
- whitespace/error checks with `git diff --check`

## Container build

```bash
docker build -t tars-voice:local .
```

The image contains the gateway and PWA, not Whisper or Piper model files. Supply configuration and the Piper model at runtime. A loopback or isolated network must provide Whisper and the Tars relay.

## CI

GitHub Actions runs the local validation, builds the container image, and scans the complete repository history with Gitleaks on every push and pull request.

## Live acceptance

A release is not fully accepted until real devices pass:

1. Cloudflare Access login
2. Desktop microphone and playback
3. Phone microphone and playback
4. Automatic speech start/end detection
5. Interim and final captions
6. Multi-turn conversation
7. Local barge-in behavior
8. Network reconnection
9. A clean no-tool Tars turn
10. A consequential request that still requires normal Tars confirmation

Record only pass/fail and latency metadata. Do not commit recordings, transcripts, identity assertions, or raw runtime logs.
