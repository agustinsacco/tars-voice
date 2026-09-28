"""Test environment. Settings read the environment at import, so set it before any test imports the gateway."""
import os
import tempfile
from pathlib import Path

_TEMP = tempfile.TemporaryDirectory()
_MODEL = Path(_TEMP.name) / "voice.onnx"
_MODEL.touch()

os.environ.setdefault("VOICE_RELAY_TOKEN", "r" * 32)
os.environ.setdefault("VOICE_PUBLIC_ORIGIN", "https://voice.example.com")
os.environ.setdefault("VOICE_ACCESS_TEAM_DOMAIN", "example.cloudflareaccess.com")
os.environ.setdefault("VOICE_ACCESS_AUD", "a" * 64)
os.environ.setdefault("VOICE_ACCESS_EMAIL", "owner@example.com")
os.environ.setdefault("VOICE_PIPER_MODEL", str(_MODEL))
