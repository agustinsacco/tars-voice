#!/usr/bin/env bash
# Run the full Tars Voice app on this machine for testing, using the host's models.
#
# The host keeps the voice LLM (8791), Whisper (8790) and the Tars relay (8789) on
# loopback, so one SSH connection forwards them to local ports (over Tailscale when
# the host name resolves there). The gateway and Piper run here, with Cloudflare
# Access stubbed for loopback. The relay token is read over SSH into this process's
# environment and never written to disk.
#
#   scripts/dev-local.sh               # background requests go to the real Tars
#   scripts/dev-local.sh --fake-tars   # canned answers; the real Tars is untouched
#
# Then open http://127.0.0.1:18788 and allow the microphone. Ctrl-C stops everything.
#
# Environment: VOICE_DEV_HOST (default stark@stark), VOICE_DEV_REMOTE_APP (default
# .tars/apps/tars-voice), VOICE_DEV_PORT (default 18788; the next three ports carry
# the relay, Whisper and the voice LLM), VOICE_DEV_FAKE_DELAY (seconds, default 5),
# VOICE_OWNER_NAME, VOICE_DISCORD_WEBHOOK_URL (unset: nothing is posted).
set -euo pipefail

cd "$(dirname "$0")/.."
HOST="${VOICE_DEV_HOST:-stark@stark}"
REMOTE_APP="${VOICE_DEV_REMOTE_APP:-.tars/apps/tars-voice}"
PORT="${VOICE_DEV_PORT:-18788}"
RELAY_PORT=$((PORT + 1))
WHISPER_PORT=$((PORT + 2))
LLM_PORT=$((PORT + 3))
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/tars-voice-dev"
PYTHON="${PYTHON:-.venv/bin/python}"
FAKE_TARS=0

case "${1:-}" in
  --fake-tars) FAKE_TARS=1 ;;
  "") ;;
  *) echo "usage: $0 [--fake-tars]" >&2; exit 2 ;;
esac

[ -x "$PYTHON" ] || { echo "missing $PYTHON; run 'make setup' first" >&2; exit 1; }
for p in "$PORT" "$RELAY_PORT" "$WHISPER_PORT" "$LLM_PORT"; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $p is already in use; set VOICE_DEV_PORT to move all four" >&2
    exit 1
  fi
done

pids=()
cleanup() {
  for pid in ${pids[@]+"${pids[@]}"}; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

wait_port() {
  for _ in $(seq 1 75); do
    nc -z 127.0.0.1 "$1" 2>/dev/null && return 0
    sleep .2
  done
  echo "$2 did not come up on port $1" >&2
  exit 1
}

umask 077
mkdir -p "$CACHE/piper"

voice="$CACHE/piper/en_US-lessac-medium.onnx"
if [ ! -s "$voice" ] || [ ! -s "$voice.json" ]; then
  echo "copying the Piper voice from $HOST (63 MB, first run only)"
  scp -q "$HOST:$REMOTE_APP/models/piper/en_US-lessac-medium.onnx" \
    "$HOST:$REMOTE_APP/models/piper/en_US-lessac-medium.onnx.json" "$CACHE/piper/"
fi

# A fresh read-only copy of the memory files the voice agent uses for context.
rm -rf "$CACHE/tars" && mkdir -p "$CACHE/tars"
ssh "$HOST" 'cd ~/.tars && ls workspace/USER.md workspace/MEMORY.md data/memory/facts.json 2>/dev/null | tar -cf - -T -' \
  | tar -xf - -C "$CACHE/tars"

forwards=(-L "$WHISPER_PORT:127.0.0.1:8790" -L "$LLM_PORT:127.0.0.1:8791")
[ "$FAKE_TARS" = 1 ] || forwards+=(-L "$RELAY_PORT:127.0.0.1:8789")
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 "${forwards[@]}" "$HOST" &
pids+=($!)
wait_port "$WHISPER_PORT" "Whisper tunnel"
wait_port "$LLM_PORT" "voice LLM tunnel"
curl -fsS "http://127.0.0.1:$LLM_PORT/health" >/dev/null || { echo "the voice LLM on $HOST is not healthy" >&2; exit 1; }

if [ "$FAKE_TARS" = 1 ]; then
  "$PYTHON" scripts/dev_fake_tars.py "$RELAY_PORT" "${VOICE_DEV_FAKE_DELAY:-5}" &
  pids+=($!)
  VOICE_RELAY_TOKEN="dev-fake-tars-$(printf '0%.0s' $(seq 1 24))"
else
  wait_port "$RELAY_PORT" "Tars relay tunnel"
  VOICE_RELAY_TOKEN="$(ssh "$HOST" "sed -n 's/^VOICE_RELAY_TOKEN=//p' ~/.tars/secrets/tars-voice.env" | tr -d "\"'\r")"
  [ "${#VOICE_RELAY_TOKEN}" -ge 24 ] || { echo "could not read the relay token from $HOST" >&2; exit 1; }
fi

owner="${VOICE_OWNER_NAME:-$( (id -F 2>/dev/null || true) | cut -d' ' -f1)}"
export VOICE_RELAY_TOKEN
export VOICE_DEV_PORT="$PORT"
export VOICE_RELAY_URL="http://127.0.0.1:$RELAY_PORT/v1/turn"
export VOICE_WHISPER_URL="http://127.0.0.1:$WHISPER_PORT/inference"
export VOICE_AGENT_URL="http://127.0.0.1:$LLM_PORT"
export VOICE_PIPER_MODEL="$voice"
export VOICE_TARS_HOME="$CACHE/tars"
export VOICE_OWNER_NAME="${owner:-the owner}"

"$PYTHON" scripts/dev_gateway.py &
gateway=$!
pids+=("$gateway")
wait_port "$PORT" "gateway"

echo
curl -fsS "http://127.0.0.1:$PORT/readyz" || true
echo
if [ "$FAKE_TARS" = 1 ]; then
  echo "Tars: fake (answers after ${VOICE_DEV_FAKE_DELAY:-5}s). The real Tars is not contacted."
else
  echo "Tars: REAL. Background requests become turns in the main Tars session."
fi
echo "Open http://127.0.0.1:$PORT and allow the microphone. Ctrl-C stops everything."
wait "$gateway"
