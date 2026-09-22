# Security architecture

## Trust boundaries

### Browser

The PWA handles microphone PCM, interim/final captions, response text, and synthesized WAV data. It never receives the Tars relay token or tool credentials.

### Cloudflare edge

Cloudflare Access performs identity authentication. The Tunnel is transport, not the sole authorization boundary.

### Voice gateway

The gateway independently verifies Access assertions and exact WebSocket Origin. It converts only recognized speech into the narrow Tars relay request shape.

### Tars

Tars remains authoritative for reasoning, memory, tools, permission checks, and consequential-action confirmation. Voice interruption does not bypass those controls.

## Network exposure

The recommended deployment binds these services to loopback:

| Port | Component |
|---:|---|
| 8788 | Voice gateway |
| 8789 | Tars relay |
| 8790 | whisper.cpp server |

Only the Cloudflare Tunnel connects external browser traffic to the gateway.

## Data handling

- Browser microphone frames are ephemeral.
- The gateway buffers only the current bounded utterance.
- The gateway does not write recordings or transcripts.
- Whisper may use a private runtime directory for temporary conversion files.
- Structured diagnostics contain timing, byte counts, character counts, and short random correlation IDs—not conversation content.

## Secrets

Never commit:

- `VOICE_RELAY_TOKEN`
- Cloudflare credentials or Access assertions
- Browser cookies
- Environment files containing real identity or infrastructure values
- Recordings, transcripts, or runtime logs

The committed `.env.example` contains placeholders only.

## Known limitations

- Local playback interruption is not authoritative cancellation of a running Tars/tool operation.
- A compromised authenticated browser session can submit voice turns until Cloudflare Access revokes it.
- The single-user connection lock is process-local and is not a distributed concurrency control.
