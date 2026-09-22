#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${PYTHON:-.venv/bin/python}"

PYTHONPATH=. "$PYTHON" -m unittest discover -s tests -v
"$PYTHON" -m ruff check gateway tests
"$PYTHON" -m pip_audit -r requirements.txt
PYTHONPATH=. "$PYTHON" -m compileall -q gateway tests
node --check static/app.js
node --check static/audio-worklet.js
node --check static/sw.js
"$PYTHON" -m json.tool static/manifest.webmanifest >/dev/null

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify --user \
    deploy/systemd/tars-voice-gateway.service \
    deploy/systemd/tars-voice-whisper.service
fi

git diff --check
printf 'All Tars Voice validations passed.\n'
