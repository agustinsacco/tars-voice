# Tars relay contract

The gateway expects a narrow authenticated HTTP adapter between voice and Tars.

## Request

```http
POST /v1/turn
Authorization: Bearer <VOICE_RELAY_TOKEN>
Content-Type: application/json
Accept: application/x-ndjson
```

```json
{"text":"recognized owner speech","sessionId":"random per-WebSocket session identifier"}
```

The relay must validate the bearer token, apply a request-size limit, and submit the text through the ordinary Tars channel pipeline. It must not create a privileged voice-only path around memory, tools, confirmations, or policy.

## Response stream

Each line is one JSON object:

```json
{"type":"accepted"}
{"type":"thinking"}
{"type":"answer","text":"Incremental response text."}
{"type":"done"}
```

Supported event types:

- `accepted`, `thinking`, `status`: progress indicators; raw status content is not forwarded to the browser
- `answer`: incremental user-visible answer text
- `error`: safe failure signal
- `done`: authoritative completion

The gateway sentence-buffers `answer` chunks for TTS. `done` is the only authoritative turn boundary.

## Cancellation

The current contract does not define cancellation. Browser barge-in stops local playback and suppresses stale synthesized output, but the relay operation may continue. A future cancel operation must carry an unambiguous turn identifier and receive authoritative acknowledgment before the UI claims cancellation.
