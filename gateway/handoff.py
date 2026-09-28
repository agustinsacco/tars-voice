"""Background queue that relays voice-agent requests to Tars, one at a time.

Tars runs one turn at a time and answers "I'm currently working" instead of
waiting, so the queue is single-flight and retries while Tars is busy. The queue
lives for the whole gateway process, so results survive a dropped connection.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .relay import HttpTarsRelay

SUPERVISOR_ERROR = "Supervisor Error"
BUSY_MARKER = "currently working"
LEDGER_WINDOW_SECONDS = 2 * 60 * 60

Listener = Callable[["Task"], Awaitable[None]]


@dataclass
class Task:
    id: int
    request: str
    asked_at: float = field(default_factory=time.time)
    status: str = "queued"  # queued | working | waiting | done | failed
    answer: str = ""
    finished_at: float | None = None
    announced: bool = False

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")

    def public(self) -> dict:
        """Client-safe view. The answer is owner content and goes only to the owner's socket."""
        seconds = round((self.finished_at or time.time()) - self.asked_at)
        return {
            "id": self.id,
            "status": self.status,
            "request": self.request,
            "answer": self.answer,
            "askedAt": round(self.asked_at * 1000),
            "seconds": seconds,
        }


class HandoffQueue:
    def __init__(
        self,
        relay: HttpTarsRelay,
        session_id: str = "tars-voice",
        busy_retry_seconds: float = 15.0,
        busy_max_attempts: int = 40,
    ):
        self.relay = relay
        self.session_id = session_id
        self.busy_retry_seconds = busy_retry_seconds
        self.busy_max_attempts = busy_max_attempts
        self.tasks: list[Task] = []
        self._pending: asyncio.Queue[Task] = asyncio.Queue()
        self._listeners: set[Listener] = set()
        self._worker: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker

    def subscribe(self, listener: Listener) -> None:
        self._listeners.add(listener)

    def unsubscribe(self, listener: Listener) -> None:
        self._listeners.discard(listener)

    def find_open(self, request: str) -> Task | None:
        wanted = request.strip().lower()
        return next((t for t in self.tasks if not t.finished and t.request.strip().lower() == wanted), None)

    async def submit(self, request: str) -> Task:
        """Queue a request, or return the identical one that is still open."""
        existing = self.find_open(request)
        if existing:
            return existing
        task = Task(len(self.tasks) + 1, request)
        self.tasks.append(task)
        await self._notify(task)
        self._pending.put_nowait(task)
        return task

    def recent(self, window: float = LEDGER_WINDOW_SECONDS) -> list[Task]:
        cutoff = time.time() - window
        return [t for t in self.tasks if t.asked_at >= cutoff or not t.finished or not t.announced]

    def ledger(self, limit: int = 6) -> str:
        """Plain-text task list for the voice agent's prompt."""
        tasks = self.recent()[-limit:]
        if not tasks:
            return "BACKGROUND TASKS\n(none)"
        lines = ["BACKGROUND TASKS"]
        for task in tasks:
            minutes = int(time.time() - task.asked_at) // 60
            line = f'- #{task.id} {task.status} (asked {minutes} min ago): "{task.request}"'
            if task.answer:
                line += f' -> "{task.answer[:800]}"'
            lines.append(line)
        return "\n".join(lines)

    async def _notify(self, task: Task) -> None:
        for listener in list(self._listeners):
            with contextlib.suppress(Exception):  # a broken listener must not stop the queue
                await listener(task)

    async def _run(self) -> None:
        while True:
            task = await self._pending.get()
            await self._execute(task)

    async def _execute(self, task: Task) -> None:
        for attempt in range(self.busy_max_attempts):
            task.status = "working" if attempt == 0 else "waiting"
            await self._notify(task)
            try:
                answer = await self._ask(task.request)
            except Exception:
                task.status, task.answer = "failed", "Tars couldn't be reached."
                break
            if SUPERVISOR_ERROR in answer and BUSY_MARKER in answer:
                await asyncio.sleep(self.busy_retry_seconds)
                continue
            if SUPERVISOR_ERROR in answer:
                task.status, task.answer = "failed", "Tars hit an error on this one."
                break
            task.status, task.answer = "done", answer or "Done."
            break
        else:
            task.status, task.answer = "failed", "Tars stayed busy, so this didn't run."
        task.finished_at = time.time()
        await self._notify(task)

    async def _ask(self, text: str) -> str:
        answers: list[str] = []
        errors = 0
        async for event in self.relay.run(text, self.session_id):
            if event.type == "answer":
                answers.append(event.text)
            elif event.type == "error":
                errors += 1
        if not answers and errors:
            raise RuntimeError("Tars reported an error")
        return "".join(answers).strip()
