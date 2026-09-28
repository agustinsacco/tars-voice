---
name: tars-voice
description: Operates and validates the private Cloudflare Access-protected Tars Voice PWA, its local speech models, its own voice-agent LLM server (port 8791, separate from the local-llm server on 8086), the authenticated gateway, and the narrow relay into Tars.
---

# Tars Voice

Canonical copy: `skills/tars-voice/` in the tars-voice repo (deployed at
`~/.tars/apps/tars-voice`). `~/.tars/skills/tars-voice` and
`~/.claude/skills/tars-voice` are symlinks to it. Edit the canonical copy only.

## Safety boundaries

- Browser ingress is the configured `VOICE_PUBLIC_ORIGIN` through the existing Cloudflare Tunnel and owner-only Cloudflare Access. Do not remove or bypass Access without explicit authorization.
- The gateway independently validates Cloudflare's signed Access JWT: signature, issuer, audience, expiry, subject, and configured owner email. There is no second application-pairing layer.
- Keep gateway, relay, Whisper, voice LLM, and TTS listeners loopback-only. Never expose ports 8788, 8789, 8790, or 8791 directly.
- The browser receives no tool credentials. Only Tars invokes tools and governs consequential actions.
- `VOICE_RELAY_TOKEN`, Access assertions, and browser identity cookies are secrets. Never print or send them in chat.
- Do not restart the Tars supervisor. Ask the owner to run `tars restart` if a core update requires it.
- Tars stays voice-agnostic: do not add voice-specific code or config to Tars. Voice features belong in this app.
- Do not restart cloudflared except for an explicitly authorized tunnel change. Use a script file with anchored process patterns so the command does not match itself.
- Never claim phone/desktop acceptance from server-side tests alone.

## Architecture

```text
PWA → Cloudflare Access + Tunnel TLS → gateway 127.0.0.1:8788
                                      ├→ Whisper 127.0.0.1:8790
                                      ├→ persistent Piper
                                      ├→ voice LLM 127.0.0.1:8791   (running, not wired in yet)
                                      └→ Tars relay 127.0.0.1:8789 → supervisor
```

Tars owns reasoning, durable memory, tools, and confirmation policy. The gateway owns Access assertion verification, ephemeral audio, adaptive automatic turn detection, local interim/final Whisper STT, TTS sentence playback, local interruption, and one bounded follow-up. The PWA needs one initial browser gesture, then listens continuously until the owner ends the conversation.

Planned direction (not live yet): a lightweight local voice agent answers
conversational turns itself and hands tool/live-data/action requests to Tars
asynchronously over the existing relay, then reports results back. Tars is
unchanged by this.

## Voice LLM (owned by tars-voice)

tars-voice runs its **own** small llama.cpp server for the voice agent, next to
the owner's main local LLM (`llama-native`, port 8086, see the `local-llm`
skill). They are separate on purpose; tars-voice is accountable for its server.

| Item | Value |
|---|---|
| Status | running permanently (`tars-voice-llm.service`, enabled); not wired into the gateway yet. Text prototype: `scripts/voice-agent-chat.py` |
| Binary | vendored llama.cpp Vulkan build in `vendor/llama.cpp/` (see its `VERSION`); independent of `~/LLM` rebuilds |
| Port | `127.0.0.1:8791` (loopback only) |
| Models | `~/.tars/apps/tars-voice/models/llm/*.gguf`, pinned and sha256-verified by `scripts/voice-llm-download.sh` |
| Chosen | Qwen3.6-35B-A3B UD-Q4_K_XL (22.4 GB), route-first JSON output; runner-up Qwen3-30B-A3B-Instruct-2507 UD-Q4_K_XL (17.7 GB) |
| Memory budget | one model loaded at a time, ~23 GB GTT at 32k context, 2 slots |
| Units | `tars-voice-llm.service` (permanent); `tars-voice-llm-bench` is a temporary unit started and always stopped by `scripts/voice-llm-bench-run.sh` - stop the permanent one first |
| Results | `docs/voice-llm.md`; raw runs in `tmp/voice-llm-bench-*.json` |

Rules for every agent on this host:

- Never load a voice model into `llama-native` / port 8086, and never switch or edit `~/LLM` presets on behalf of voice. The voice GGUFs live outside `~/models` so `~/LLM/validate.sh` does not see them.
- Do not stop, restart, or delete the voice LLM or its model files unless the owner asks, or you need the memory for a large `~/LLM` preset. In that case stop it with `systemctl --user stop 'tars-voice-llm*'` and tell the owner.
- Before starting the voice LLM, check headroom (`free -g`, and GTT use in `/sys/class/drm/card*/device/mem_info_gtt_used`). Keep at least ~15 GB free after loading.
- The voice LLM and Whisper share the iGPU with `llama-native`; heavy 8086 jobs slow voice turns. That is expected, not a fault.

```bash
systemctl --user list-units 'tars-voice-llm*' --all --no-pager
curl -fsS http://127.0.0.1:8791/health
```

## Paths

- App: `~/.tars/apps/tars-voice`
- Architecture: `docs/architecture.md`
- Protocol: `docs/protocol.md`
- Runbook: `docs/runbook.md`
- Validation: `docs/review.md`
- Voice LLM: `docs/voice-llm.md`, models in `models/llm/` (under the app directory)
- Live cloudflared config: `~/.cloudflared/config.yml`
- Access environment: `~/.tars/secrets/tars-voice-access.env` (never display values)
- Relay environment: `~/.tars/secrets/tars-voice.env` (never display)

## Health checks

```bash
systemctl --user status tars-voice-whisper tars-voice-gateway tars-voice-llm
ss -ltnp | grep -E ':(8788|8789|8790|8791)\b'
curl -fsS http://127.0.0.1:8788/healthz
curl -sS -o /dev/null -w '%{http_code}\n' "$VOICE_PUBLIC_ORIGIN/"
```

All voice listeners must be bound to `127.0.0.1`. Without a Cloudflare Access session, the external URL should redirect to Access. Local loopback health checks intentionally bypass Access.

## Validation

```bash
cd ~/.tars/apps/tars-voice
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=. .venv/bin/python -m py_compile gateway/*.py
node --check static/app.js
node --check static/audio-worklet.js
node --check static/sw.js
```

Relay `done` is authoritative. Barge-in stops local audio immediately but does not claim cancellation of an in-flight Tars/tool operation; the current relay has no authoritative cancel endpoint.
