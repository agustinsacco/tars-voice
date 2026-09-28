#!/usr/bin/env bash
# Download a pinned voice-agent LLM candidate into the tars-voice model directory.
# These GGUFs belong to tars-voice, not to ~/LLM: they deliberately live outside
# ~/models so the local-llm presets and validate.sh are unaffected.
#
#   scripts/voice-llm-download.sh qwen36-a3b-q4
#   scripts/voice-llm-download.sh qwen36-a3b-q3
#   scripts/voice-llm-download.sh qwen3-2507-a3b-q4
set -euo pipefail

APP_HOME="${TARS_VOICE_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
MODEL_DIR="$APP_HOME/models/llm"

case "${1:-}" in
  qwen36-a3b-q4)
    REPO="unsloth/Qwen3.6-35B-A3B-GGUF"
    REVISION="a483e9e6cbd595906af30beda3187c2663a1118c"
    FILE="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"
    SHA256="707a55a8a4397ecde44de0c499d3e68c1ad1d240d1da65826b4949d1043f4450"
    ;;
  qwen36-a3b-q3)
    REPO="unsloth/Qwen3.6-35B-A3B-GGUF"
    REVISION="a483e9e6cbd595906af30beda3187c2663a1118c"
    FILE="Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf"
    SHA256="a832b9689925f1bd335bbe985cdfb06c36bf2cf268f4f8f6eceafa3ceb515617"
    ;;
  qwen3-2507-a3b-q4)
    REPO="unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF"
    REVISION="eea7b2be5805a5f151f8847ede8e5f9a9284bf77"
    FILE="Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf"
    SHA256="535f831bf3034a7fcfdcc8f0277a57cbad3a36c650c6068bf1d0894f25fb4383"
    ;;
  *)
    echo "usage: $0 qwen36-a3b-q4|qwen36-a3b-q3|qwen3-2507-a3b-q4" >&2
    exit 2
    ;;
esac

mkdir -p "$MODEL_DIR"
chmod 0700 "$MODEL_DIR"
dest="$MODEL_DIR/$FILE"

if [ -f "$dest" ] && echo "$SHA256  $dest" | sha256sum --check --status; then
  echo "already present and verified: $dest"
  exit 0
fi

echo "downloading $REPO@$REVISION/$FILE"
wget -c -q --show-progress "https://huggingface.co/$REPO/resolve/$REVISION/$FILE" -O "$dest.partial"
echo "$SHA256  $dest.partial" | sha256sum --check
mv "$dest.partial" "$dest"
echo "verified: $dest"
