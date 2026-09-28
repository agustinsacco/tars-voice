# Tars Voice protocol

## WebSocket authentication

`GET /ws` requires the exact configured `VOICE_PUBLIC_ORIGIN` and a valid Cloudflare Access JWT injected by the Access proxy. The gateway independently verifies its signature, issuer, audience, expiry, and owner identity. Anonymous or cross-origin handshakes are rejected. The gateway permits one active WebSocket.

## Client to gateway

JSON control frames:

```json
{"type":"speech_start"}
{"type":"speech_end"}
{"type":"text_turn","text":"..."}
{"type":"interrupt"}
{"type":"quiet","on":true}
{"type":"call_start"}
{"type":"end_call"}
{"type":"ping"}
```

Between `speech_start` and `speech_end`, binary frames contain 16 kHz, mono, signed little-endian PCM16. Individual frames are capped at 64 KiB and each utterance at 30 seconds.

- `text_turn` sends typed text through the same path as speech.
- `interrupt` stops the rest of the current spoken reply (tap to stop).
- `quiet` turns speech synthesis off or on; replies still arrive as text.
- `call_start` starts a fresh voice-agent conversation and prefills its prompt.
- `end_call` summarizes the call, posts it to Discord when configured, and replies with `call_summary`. A call that ends by disconnecting is summarized and posted too.

`speech_start` never drops an answer on its own, because noise can start a capture. The client likewise only pauses playback when a capture starts: it drops the rest of the reply on the capture's `transcript`, and resumes on its `error`. The current reply is only interrupted by confirmed speech (a final transcript), `interrupt`, or the client stopping playback locally.

## Gateway to client

```text
ready       connection accepted; `agent` is true when the voice agent is on; `discordUrl` links the voice channel
tasks       snapshot of recent background tasks
task        one background task changed (queued, working, waiting, done, failed)
call_summary        minutes, summary, the call's tasks, and whether it was posted to Discord
listening   audio capture started
status      sanitized progress label
partial_transcript  evolving local transcript while the owner is speaking
transcript          final recognized owner speech
answer              displayable reply text; `taskId` is set when it reports a task result
speakable   text represented by the following synthesized sentence
queued      one follow-up was queued
error       safe user-facing error
 done       authoritative turn completion
```

An `audio` JSON event provides MIME type and byte count; the immediately following binary frame is that WAV sentence. WebSocket ordering is authoritative.

## Voice agent mode

With `VOICE_AGENT_URL` set, each turn goes to the local voice model first. It either answers (`answer`, then speech) or queues a background task for Tars and says so. Tasks run one at a time over the relay. When one finishes, the gateway sends a `task` event and, at the owner's next pause, a short spoken `answer` with its `taskId`. The full result stays in the `task` event.

Without `VOICE_AGENT_URL`, every turn goes straight to Tars as before.

## Privacy

Audio and transcripts are ephemeral. They are not included in gateway logs or browser persistent storage. Static PWA assets may be cached; API and health responses are never cached by the service worker.
