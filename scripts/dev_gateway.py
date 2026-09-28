#!/usr/bin/env python3
"""Run the real gateway on 127.0.0.1 for local testing. Development only.

Cloudflare Access is replaced by a stub that accepts every request, so this binds
to loopback and nothing else. Everything else is the production code path: the
voice agent, handoff queue, Whisper client, relay, and a real Piper voice.
Started by scripts/dev-local.sh, which sets the environment.
"""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = int(os.environ.get("VOICE_DEV_PORT", "18788"))
os.environ["VOICE_PUBLIC_ORIGIN"] = f"http://127.0.0.1:{PORT}"
os.environ.setdefault("VOICE_ACCESS_TEAM_DOMAIN", "dev.cloudflareaccess.com")
os.environ.setdefault("VOICE_ACCESS_AUD", "d" * 64)
os.environ.setdefault("VOICE_ACCESS_EMAIL", "dev@localhost")

# Settings read the environment at import, so the gateway is imported after it is set.
import uvicorn  # noqa: E402

from gateway import app as gateway_app  # noqa: E402
from gateway import settings  # noqa: E402
from gateway.access import AccessIdentity  # noqa: E402

_validate = settings.Settings.validate


def dev_validate(self: settings.Settings) -> None:
    """Every production check except the HTTPS origin, which loopback cannot have."""
    _validate(dataclasses.replace(self, public_origin="https://dev.invalid"))


class LoopbackAccess:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def verify(self, _token: str | None) -> AccessIdentity:
        return AccessIdentity(email="dev@localhost", subject="dev")


settings.Settings.validate = dev_validate
gateway_app.CloudflareAccessVerifier = LoopbackAccess

if __name__ == "__main__":
    uvicorn.run(gateway_app.app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False)
