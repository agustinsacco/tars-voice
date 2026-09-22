# Operations runbook

## Status

```bash
systemctl --user status tars-voice-whisper tars-voice-gateway
ss -ltnp | grep -E ':(8788|8789|8790)\b'
curl -fsS http://127.0.0.1:8788/healthz
curl -fsS http://127.0.0.1:8788/readyz | python3 -m json.tool
```

Expected backend listeners are loopback-only:

- gateway: 8788
- Tars relay: 8789
- Whisper: 8790

An unauthenticated request to the external hostname should receive a Cloudflare Access redirect rather than application content.

## Browser access

Open the configured `VOICE_PUBLIC_ORIGIN`, complete Cloudflare Access authentication, and select **Start conversation**. There is no separate application pairing layer.

## Service control

```bash
systemctl --user restart tars-voice-whisper tars-voice-gateway
systemctl --user stop tars-voice-gateway tars-voice-whisper
systemctl --user start tars-voice-whisper tars-voice-gateway
```

These commands do not control the Tars supervisor or Cloudflare Tunnel.

## Diagnostics

```bash
curl -fsS http://127.0.0.1:8788/api/diagnostics | python3 -m json.tool
journalctl --user -u tars-voice-gateway -u tars-voice-whisper --since today
```

See [`diagnostics.md`](diagnostics.md) for event interpretation. Do not enable logging of assertions, credentials, audio, transcripts, answer content, or tool payloads.

## Update procedure

```bash
cd ~/.local/share/tars-voice
git pull --ff-only
make validate
systemctl --user restart tars-voice-gateway
curl -fsS http://127.0.0.1:8788/readyz | python3 -m json.tool
```

Restart Whisper only when its binary, model, or service configuration changed.

## Rollback

```bash
cd ~/.local/share/tars-voice
git log --oneline -10
git checkout <known-good-commit>
make validate
systemctl --user restart tars-voice-gateway
```

For full shutdown:

```bash
systemctl --user disable --now tars-voice-gateway tars-voice-whisper
```

Removing the Cloudflare Access application, Tunnel ingress, or DNS record is a separate infrastructure operation.
