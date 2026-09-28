# Local development

`scripts/dev-local.sh` runs the whole app on a laptop against the models on the
host, so you can test the browser UI and the voice agent without deploying.

```bash
make setup                        # once
scripts/dev-local.sh --fake-tars  # canned Tars answers; the real Tars is untouched
scripts/dev-local.sh              # background requests go to the real Tars
```

Open `http://127.0.0.1:18788` and allow the microphone. `127.0.0.1` counts as a
secure origin, so the browser allows the mic without HTTPS. Ctrl-C stops
everything.

## What runs where

| Piece | Where | How |
|---|---|---|
| Gateway, Piper voice | laptop | `scripts/dev_gateway.py` on 127.0.0.1:18788 |
| Voice LLM (8791), Whisper (8790) | host | SSH tunnel to 18791 and 18790 |
| Tars relay (8789) | host | SSH tunnel to 18789, or `scripts/dev_fake_tars.py` with `--fake-tars` |
| Voice-agent context | host | fresh read-only copy of `USER.md`, `MEMORY.md`, `facts.json` |

The host's voice services stay on loopback; the tunnel is the only way in, and
it rides on SSH (over Tailscale when the host name resolves there). The Piper
voice and context copy live in `~/.cache/tars-voice-dev/` with owner-only
permissions. The relay token is read over SSH into the script's environment and
never written to disk.

`dev_gateway.py` is the production gateway with two changes, for loopback only:
Cloudflare Access is stubbed to accept every request, and the origin may be
plain HTTP. Never bind it to anything but 127.0.0.1.

## Real Tars

Without `--fake-tars`, every background request is a real Tars turn in its main
session, the same as from the phone. Tars treats the relay as its most recently
used channel afterwards, so scheduled notifications may stay undelivered until
you next message Tars on Discord. Use `--fake-tars` for UI work.

## Options

| Variable | Default | Meaning |
|---|---|---|
| `VOICE_DEV_HOST` | `stark@stark` | SSH destination running the voice services |
| `VOICE_DEV_REMOTE_APP` | `.tars/apps/tars-voice` | App directory on the host, for the Piper voice |
| `VOICE_DEV_PORT` | `18788` | Gateway port; the next three carry relay, Whisper, voice LLM |
| `VOICE_DEV_FAKE_DELAY` | `5` | Seconds the fake Tars takes per answer |
| `VOICE_OWNER_NAME` | your macOS first name | Name the voice agent uses for you |
| `VOICE_DISCORD_WEBHOOK_URL` | unset | Set to post to a Discord channel; unset posts nothing |
