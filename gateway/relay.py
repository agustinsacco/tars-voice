"""Incremental authenticated adapter for the loopback Tars NDJSON relay."""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class RelayEvent:
    type: str
    text: str = ""


class HttpTarsRelay:
    def __init__(self, url: str, token: str, timeout: int = 180):
        if len(token) < 24:
            raise RuntimeError("Voice relay token is not configured")
        self.url = url
        self.token = token
        self.timeout = timeout

    async def run(self, text: str, session_id: str) -> AsyncIterator[RelayEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[RelayEvent | Exception | None] = asyncio.Queue()
        stopped = threading.Event()

        def emit(value: RelayEvent | Exception | None) -> None:
            if not stopped.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, value)

        def request_thread() -> None:
            body = json.dumps({"text": text, "sessionId": session_id}).encode()
            request = urllib.request.Request(
                self.url,
                body,
                {
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/x-ndjson",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    for raw_line in response:
                        if stopped.is_set():
                            break
                        try:
                            payload = json.loads(raw_line)
                            event_type = str(payload.get("type", "error"))
                            text_value = str(payload.get("text", ""))
                            emit(RelayEvent(event_type, text_value))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            emit(RuntimeError("Tars returned an invalid event"))
                            break
            except urllib.error.HTTPError as error:
                emit(RuntimeError(f"Tars relay rejected the request ({error.code})"))
            except Exception:
                emit(RuntimeError("Tars relay is unavailable"))
            finally:
                emit(None)

        thread = threading.Thread(target=request_thread, name="tars-voice-relay", daemon=True)
        thread.start()
        try:
            while True:
                value = await queue.get()
                if value is None:
                    break
                if isinstance(value, Exception):
                    raise value
                yield value
        finally:
            stopped.set()
