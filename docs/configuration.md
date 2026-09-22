# Configuration

All runtime configuration is supplied through environment variables.

| Variable | Required | Description |
|---|---:|---|
| `VOICE_PUBLIC_ORIGIN` | yes | Exact external HTTPS origin, without trailing slash |
| `VOICE_ACCESS_TEAM_DOMAIN` | yes | Cloudflare Access team domain ending in `.cloudflareaccess.com` |
| `VOICE_ACCESS_AUD` | yes | Access application audience identifier |
| `VOICE_ACCESS_EMAIL` | yes | Exact permitted Access email identity |
| `VOICE_ADDITIONAL_HOSTS` | no | Comma-separated extra development hostnames |
| `VOICE_RELAY_URL` | no | Tars relay URL; defaults to `http://127.0.0.1:8789/v1/turn` |
| `VOICE_RELAY_TOKEN` | yes | High-entropy relay bearer token, minimum 24 characters |
| `VOICE_WHISPER_URL` | no | whisper.cpp inference URL; defaults to `http://127.0.0.1:8790/inference` |
| `VOICE_PIPER_MODEL` | yes | Absolute path to the Piper ONNX model |
| `VOICE_MAX_AUDIO_SECONDS` | no | Maximum utterance length; defaults to 30 seconds |

## Cloudflare assertion checks

The gateway accepts external HTTP and WebSocket traffic only when the assertion passes all of these checks:

1. RS256 signature against the team's current JWKS
2. Exact issuer
3. Exact application audience
4. Valid issue and expiry times
5. Present subject and email claims
6. Exact configured email identity

Local loopback health and diagnostic requests intentionally bypass Access. Do not publish the loopback port directly.

## Model tuning

The reference deployment uses:

- Whisper `large-v3-turbo`, English, temperature zero
- Piper `en_US-lessac-medium`
- WebRTC VAD mode 2
- First interim transcription near 0.9 seconds, then approximately every 1.2 seconds

These values prioritize responsiveness. Repeated interim Whisper snapshots consume additional inference capacity and are serialized to avoid final-transcript contention.
