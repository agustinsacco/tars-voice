# Voice LLM

tars-voice runs its own small local model as a fast voice agent. It answers
conversational turns itself and hands tool, live-data, personal-data, and action
requests to Tars asynchronously over the existing relay. Tars is unchanged and
stays voice-agnostic. This document records how the model was chosen and how it
runs. The PWA does not use it yet; `scripts/voice-agent-chat.py` is a text
prototype of the flow.

## Hosting

- `tars-voice-llm.service` runs `llama-server` on `127.0.0.1:8791`, 32k context,
  2 slots, q8_0 KV cache. The owner's main local LLM (port 8086) is separate and
  untouched.
- The binary is a vendored copy of a llama.cpp Vulkan build in
  `vendor/llama.cpp/build-vulkan/bin`, so rebuilds of the main LLM tree cannot
  break voice. Record the source commit in `vendor/llama.cpp/VERSION`.
- Models are pinned and sha256-verified by `scripts/voice-llm-download.sh`, in
  `models/llm/` under the app, outside `~/models`.
- Both servers share the Strix Halo iGPU. The voice LLM added ~23 GB of GTT; about
  34 GB was still available with Qwen3.8-Flash-Next loaded on 8086.

## Install

```bash
scripts/voice-llm-download.sh qwen36-a3b-q4
mkdir -p vendor/llama.cpp/build-vulkan
cp -a /path/to/llama.cpp/build_vulkan/bin vendor/llama.cpp/build-vulkan/
git -C /path/to/llama.cpp rev-parse HEAD > vendor/llama.cpp/VERSION
cp deploy/systemd/tars-voice-llm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tars-voice-llm
curl -fsS http://127.0.0.1:8791/health
```

Check `free -g` first. Keep at least ~15 GB available after the model loads.

## Text prototype

`scripts/voice-agent-chat.py` runs on the host, so the relay token never leaves
it. Each line goes to the voice LLM, which answers itself or routes to Tars.
Handoffs join a single-flight queue that relays to Tars over `/v1/turn` and
retries while Tars reports it is busy. Answers print when they arrive while you
keep typing. `/tasks` lists handoffs.

```bash
ssh -t <host> ~/.tars/apps/tars-voice/scripts/voice-agent-chat.py
```

Handoffs land in the main Tars session like any relay turn. Local small talk
does not.

## Benchmark (2026-09-23, Strix Halo host, llama.cpp upstream 2026-09-13 Vulkan)

`scripts/voice-llm-bench-run.sh` starts a candidate, runs
`scripts/voice-llm-bench.py`, and stops it. The harness uses a voice-agent prompt
with read-only Tars context, 21 utterances (the owner's real turns plus synthetic
probes), thinking disabled, and temperature 0.3. 8086 was idle during the runs.

Route-first JSON mode, ~2.8k-token prompt, two rounds:

| Model | Routing correct | First token | First sentence | Handoff ready | Decode | Cold prefill (2.8k / 8.8k tokens) |
|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B UD-Q4_K_XL | 38/38 | 0.16 s | 0.54 s | 1.0 s | 55 tok/s | 3.5 s / 11.0 s |
| Qwen3-30B-A3B-Instruct-2507 UD-Q4_K_XL | 34/38 | 0.12 s | 0.42 s | 0.74 s | 75 tok/s | 3.1 s / 11.5 s |

Findings:

- **Handoffs need route-first structured output.** In tool-call mode both models
  often said "Let me ask Tars" without calling the tool (2507: 12 of 40 turns).
  2507 also stated guessed personal facts, such as which relative is the
  owner's sister. With a JSON schema whose first field is `route`
  (`self`/`tars`), then `say`, then `tars_request`, neither model faked a
  handoff or stated guessed personal facts.
- **Qwen3.6 has better conversational judgement.** It told the owner a task was
  already pending instead of dispatching it again. It recognised "I'll ask it
  what's on your calendar" as speech aimed at someone else. It explained what
  a pending status meant. 2507 dispatched each of these to Tars.
- The ~0.1 s first-sentence gap is small next to STT and TTS time. **Choice:
  Qwen3.6-35B-A3B UD-Q4_K_XL.**

## Implementation notes

- Keep the prompt prefix stable: instructions, examples, then Tars context. Put
  volatile parts (time, handoff ledger) last so each turn only prefills the tail.
  Prewarm the prefix when the PWA connects; a cold 2.8k-token prefill takes ~3.5 s.
- Stream `say` to TTS while the JSON streams. Dispatch `tars_request` when the
  object completes.
- Deduplicate handoffs against the pending ledger in the gateway as well.
- Facts in Tars memory can be stale. The prompt must send "now/today" questions to
  Tars.
