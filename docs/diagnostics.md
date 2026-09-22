# Diagnostics and post-test review

Tars Voice emits bounded, privacy-safe operational telemetry. It does not log or retain microphone audio, transcripts, answer text, Access assertions, cookies, relay tokens, tool arguments, or tool results.

## Coverage by layer

| Layer | Signals | Source |
|---|---|---|
| Browser/PWA | app load, service-worker registration, WebSocket open/close/error, microphone readiness/denial, conversation-mode toggles, playback failures | allowlisted events sent to `/api/diagnostics/client` |
| Cloudflare Access | identity decision and Access session | Cloudflare Zero Trust Access logs |
| Cloudflare Tunnel | connection/reconnect and ingress errors | `~/.tars/cloudflared.out` |
| Gateway | startup, Access/Origin rejection, connection lifecycle, capture duration/size, VAD result, STT/relay/TTS latency and failure category, queue behavior | structured JSON in the user journal |
| Whisper | model/GPU startup and server errors | `tars-voice-whisper.service` journal |
| Tars relay/supervisor | relay acceptance, turn processing, provider/tool failures | `tars logs` and relay event stream |
| Piper | preload, synthesis latency, output byte count, failure category | gateway structured JSON |
| systemd | process lifecycle, exit code, restart count | user-service journal/status |

Gateway records include short random `connectionId` and `turnId` values for correlation. They are not credentials and are regenerated for each connection/turn.

## Live endpoints

Loopback checks intentionally bypass Cloudflare Access:

```bash
curl -fsS http://127.0.0.1:8788/healthz
curl -fsS http://127.0.0.1:8788/readyz | python3 -m json.tool
curl -fsS http://127.0.0.1:8788/api/diagnostics | python3 -m json.tool
```

- `/healthz` is a shallow gateway liveness check.
- `/readyz` probes the loopback Whisper and relay ports and reports whether Piper is loaded.
- `/api/diagnostics` adds uptime, counters, active-client state, and the latest 100 metadata-only events.
- `/api/diagnostics/client` accepts only a fixed event allowlist; arbitrary browser detail is rejected.

The diagnostics ring is memory-only and clears whenever the gateway restarts. The same safe events are written as one-line JSON to journald for post-test review.

## Post-test collection

Run immediately after a phone or desktop test:

```bash
since='15 minutes ago'

curl -fsS http://127.0.0.1:8788/api/diagnostics | python3 -m json.tool
journalctl --user -u tars-voice-gateway.service --since "$since" --no-pager
journalctl --user -u tars-voice-whisper.service --since "$since" --no-pager
tail -n 200 ~/.tars/cloudflared.out
tars logs
systemctl --user show tars-voice-gateway tars-voice-whisper \
  -p ActiveState -p SubState -p NRestarts -p ExecMainStatus
```

Do not paste unreviewed logs into chat. Inspect locally first and redact unrelated personal data or credentials if a line from another component violates the expected logging policy.

## Expected successful sequence

An automatically detected conversation turn should produce metadata events in this order:

```text
client_event(app_loaded)
client_event(websocket_open)
websocket_connected
client_event(microphone_ready)
capture_started
stt_partial (zero or more)
capture_completed
stt_started
stt_completed
turn_started
relay_first_answer
tts_completed (one or more)
turn_completed
```

The exact ordering of browser events can vary around reconnects. `capture_completed.durationMs` is derived only from PCM byte count; no audio is retained.

## Failure localization

- No `app_loaded`: stale/offline PWA shell or Access/navigation failure.
- `app_loaded` but no `websocket_open`: Access assertion, Origin, tunnel, or WebSocket upgrade failure.
- `microphone_denied`: browser permission or embedded-browser limitation.
- `capture_completed` followed by `vad_rejected`: silence, low input level, or an overly short utterance.
- `stt_failed`: Whisper service, timeout, or empty recognition result.
- `turn_started` without `relay_first_answer`: relay/supervisor/provider delay or failure; inspect `tars logs`.
- `tts_failed`: Piper runtime/model failure.
- `audio_playback_failed`: browser autoplay or output-device issue.
- repeated `websocket_close`/`websocket_error`: Access expiry, network change, tunnel instability, or server rejection.

## Known diagnostic boundary

The current relay does not expose authoritative cancellation or structured tool lifecycle events. Gateway telemetry can time the complete relay turn but cannot yet attribute time to individual Tars tool calls. Tool-level diagnosis therefore remains in `tars logs` until the core relay emits safe structured tool events.
