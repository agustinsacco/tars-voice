# Tars Voice protocol

## WebSocket authentication

`GET /ws` requires the exact configured `VOICE_PUBLIC_ORIGIN` and a valid Cloudflare Access JWT injected by the Access proxy. The gateway independently verifies its signature, issuer, audience, expiry, and owner identity. Anonymous or cross-origin handshakes are rejected. The gateway permits one active WebSocket.

## Client to gateway

JSON control frames:

```json
{"type":"speech_start"}
{"type":"speech_end"}
{"type":"ping"}
```

Between `speech_start` and `speech_end`, binary frames contain 16 kHz, mono, signed little-endian PCM16. Individual frames are capped at 64 KiB and each utterance at 30 seconds.

`text_turn` exists for authenticated diagnostics and accessibility fallback; the production UI does not expose it by default.

## Gateway to client

```text
ready       connection accepted
listening   audio capture started
status      sanitized progress label
partial_transcript  evolving local transcript while the owner is speaking
transcript          final recognized owner speech
answer              displayable Tars answer text
speakable   text represented by the following synthesized sentence
queued      one follow-up was queued
error       safe user-facing error
 done       authoritative turn completion
```

An `audio` JSON event provides MIME type and byte count; the immediately following binary frame is that WAV sentence. WebSocket ordering is authoritative.

## Privacy

Audio and transcripts are ephemeral. They are not included in gateway logs or browser persistent storage. Static PWA assets may be cached; API and health responses are never cached by the service worker.
