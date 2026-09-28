#!/usr/bin/env bash
# Benchmark one downloaded voice LLM candidate: start it as a temporary loopback
# server on 8791, run scripts/voice-llm-bench.py, and always stop it afterwards.
# Refuses to start without ~15 GB of memory headroom next to the main local LLM,
# or while the permanent tars-voice-llm service holds the port.
#
#   scripts/voice-llm-bench-run.sh Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf qwen36-a3b-q4
#   MODES=json scripts/voice-llm-bench-run.sh <file> <label>
set -euo pipefail

file="${1:?usage: $0 <gguf file in models/llm> <label>}"
label="${2:?usage: $0 <gguf file in models/llm> <label>}"
app="${TARS_VOICE_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
bin="${VOICE_LLM_SERVER_BIN:-$app/vendor/llama.cpp/build-vulkan/bin/llama-server}"
model="$app/models/llm/$file"
[ -f "$model" ] || { echo "missing $model" >&2; exit 1; }
if ss -ltn | grep -q '127.0.0.1:8791 '; then
  echo "port 8791 is in use; stop tars-voice-llm first: systemctl --user stop tars-voice-llm" >&2
  exit 1
fi

avail=$(free -g | awk '/^Mem:/{print $7}')
need=$(( $(stat -c %s "$model") / 1000000000 + 4 + 15 ))
echo "available ${avail}G, need ~${need}G (model + KV + 15G headroom)"
[ "$avail" -ge "$need" ] || { echo "not enough headroom; not starting" >&2; exit 1; }

systemctl --user stop tars-voice-llm-bench 2>/dev/null || true
systemctl --user reset-failed tars-voice-llm-bench 2>/dev/null || true
systemd-run --user --unit=tars-voice-llm-bench \
  --description="tars-voice: voice LLM benchmark (temporary)" \
  --setenv=AMD_VULKAN_ICD=RADV \
  "$bin" --model "$model" --host 127.0.0.1 --port 8791 \
  --ctx-size 32768 --parallel 2 --n-gpu-layers 999 --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --jinja --no-webui
trap 'systemctl --user stop tars-voice-llm-bench; echo "stopped tars-voice-llm-bench"' EXIT

for _ in $(seq 1 180); do
  curl -sf http://127.0.0.1:8791/health >/dev/null 2>&1 && break
  systemctl --user is-active -q tars-voice-llm-bench \
    || { journalctl --user -u tars-voice-llm-bench -n 30 --no-pager -o cat; exit 1; }
  sleep 2
done
curl -sf http://127.0.0.1:8791/health >/dev/null || { echo "server did not become healthy" >&2; exit 1; }
echo "loaded; free: $(free -g | awk '/^Mem:/{print $7}')G available"

mkdir -p "$app/tmp"
for mode in ${MODES:-tools json}; do
  for chars in 8000 24000; do
    echo "--- $mode, context ${chars} chars ---"
    "$app/scripts/voice-llm-bench.py" --label "$label" --mode "$mode" --context-chars "$chars" \
      --rounds "$([ "$chars" = 8000 ] && echo 2 || echo 1)" \
      --out "$app/tmp/voice-llm-bench-$label-$mode-$chars.json"
  done
done
echo "gtt used: $(awk '{printf "%.1f GB", $1/1e9}' /sys/class/drm/card*/device/mem_info_gtt_used 2>/dev/null)"
