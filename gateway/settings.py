"""Voice gateway configuration. Secrets are environment-only."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    public_origin: str = os.getenv("VOICE_PUBLIC_ORIGIN", "")
    access_team_domain: str = os.getenv("VOICE_ACCESS_TEAM_DOMAIN", "")
    access_audience: str = os.getenv("VOICE_ACCESS_AUD", "")
    access_email: str = os.getenv("VOICE_ACCESS_EMAIL", "")
    relay_url: str = os.getenv("VOICE_RELAY_URL", "http://127.0.0.1:8789/v1/turn")
    relay_token: str = os.getenv("VOICE_RELAY_TOKEN", "")
    whisper_url: str = os.getenv("VOICE_WHISPER_URL", "http://127.0.0.1:8790/inference")
    piper_model: Path = Path(os.getenv("VOICE_PIPER_MODEL", str(ROOT / "models/piper/en_US-lessac-medium.onnx")))
    max_audio_seconds: int = int(os.getenv("VOICE_MAX_AUDIO_SECONDS", "30"))
    additional_hosts: str = os.getenv("VOICE_ADDITIONAL_HOSTS", "")

    @property
    def public_host(self) -> str:
        return urlparse(self.public_origin).hostname or ""

    @property
    def allowed_hosts(self) -> set[str]:
        configured = {host.strip().lower() for host in self.additional_hosts.split(",") if host.strip()}
        return {self.public_host, "127.0.0.1", "localhost", "testserver"} | configured

    def validate(self) -> None:
        if not self.access_team_domain.endswith(".cloudflareaccess.com"):
            raise RuntimeError("VOICE_ACCESS_TEAM_DOMAIN is not configured")
        if len(self.access_audience) < 32:
            raise RuntimeError("VOICE_ACCESS_AUD is not configured")
        if "@" not in self.access_email:
            raise RuntimeError("VOICE_ACCESS_EMAIL is not configured")
        if len(self.relay_token) < 24:
            raise RuntimeError("VOICE_RELAY_TOKEN is not configured")
        if not self.public_origin.startswith("https://") or not self.public_host:
            raise RuntimeError("VOICE_PUBLIC_ORIGIN must be a complete HTTPS origin")
        if not self.piper_model.is_file():
            raise RuntimeError("Piper model is missing")
