"""Privacy-safe structured diagnostics for the voice gateway."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, deque
from datetime import UTC, datetime
from typing import Any

_LOGGER = logging.getLogger("tars.voice")


def configure_logging() -> None:
    if any(getattr(handler, "name", None) == "tars-voice-json" for handler in _LOGGER.handlers):
        return
    handler = logging.StreamHandler()
    handler.name = "tars-voice-json"
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


class Diagnostics:
    """Bounded in-memory event history plus JSON journald records.

    Callers must provide metadata only. Audio, transcript/answer text, tokens,
    cookies, headers, tool arguments, and exception strings are forbidden.
    """

    def __init__(self, recent_limit: int = 100) -> None:
        configure_logging()
        self.started_wall = datetime.now(UTC)
        self.started_monotonic = time.monotonic()
        self._recent: deque[dict[str, Any]] = deque(maxlen=recent_limit)
        self._counters: Counter[str] = Counter()
        self._lock = threading.Lock()

    def record(self, event: str, *, level: str = "info", **fields: Any) -> None:
        safe = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "component": "voice-gateway",
            "event": event[:64],
        }
        for key, value in fields.items():
            if isinstance(value, str):
                safe[key[:40]] = value[:120]
            elif isinstance(value, (int, float, bool)) or value is None:
                safe[key[:40]] = value
        with self._lock:
            self._recent.append(safe)
            self._counters[event] += 1
        getattr(_LOGGER, level if level in {"info", "warning", "error"} else "info")(
            json.dumps(safe, separators=(",", ":"), sort_keys=True)
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            recent = list(self._recent)
        return {
            "startedAt": self.started_wall.isoformat(timespec="seconds"),
            "uptimeSeconds": round(time.monotonic() - self.started_monotonic, 1),
            "counters": counters,
            "recent": recent,
            "privacy": "metadata-only; no user content, credentials, or tool payloads retained",
        }
