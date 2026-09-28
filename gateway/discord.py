"""Posts voice activity to a Discord channel through a webhook.

A webhook keeps tars-voice independent of Tars: no bot token, no Tars code, and
the posts land in their own channel. Only finished background tasks and call
summaries are posted; small talk stays local.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable

from .handoff import Task

MAX_CONTENT = 2000
USER_AGENT = "DiscordBot (https://github.com/agustinsacco/tars-voice, 1.0)"


def clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_task(task: Task) -> str:
    head = f"🎙️ **{clip(task.request, 300)}**"
    body = task.answer if task.status == "done" else f"_{task.answer or 'Did not finish.'}_"
    return clip(f"{head}\n{body}", MAX_CONTENT)


def format_call(minutes: int, summary: str, tasks: list[Task]) -> str:
    lines = [f"🎙️ **Voice call · {minutes} min**"]
    if summary:
        lines.append(summary)
    marks = {"done": "✓", "failed": "✗"}
    for task in tasks:
        suffix = "" if task.finished else " (still running)"
        lines.append(f"{marks.get(task.status, '…')} {clip(task.request, 200)}{suffix}")
    return clip("\n".join(lines), MAX_CONTENT)


class DiscordWebhook:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout

    async def post(self, content: str) -> bool:
        return await asyncio.to_thread(self._post, content)

    def _post(self, content: str) -> bool:
        body = json.dumps(
            {"content": clip(content, MAX_CONTENT), "username": "Tars Voice", "allowed_mentions": {"parse": []}}
        ).encode()
        for attempt in range(2):
            request = urllib.request.Request(
                self.url,
                body,
                {"Content-Type": "application/json", "User-Agent": USER_AGENT},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return 200 <= response.status < 300
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt == 0:
                    try:
                        wait = float(json.loads(error.read() or b"{}").get("retry_after", 1))
                    except (ValueError, AttributeError):
                        wait = 1.0
                    time.sleep(min(wait, 5.0))
                    continue
                return False
            except (urllib.error.URLError, TimeoutError, OSError):
                return False
        return False


def task_poster(webhook: DiscordWebhook, record: Callable[..., None]) -> Callable[[Task], Awaitable[None]]:
    """Queue listener that posts each finished task once, without blocking the queue."""
    posted: set[int] = set()
    pending: set[asyncio.Task[None]] = set()

    async def post(task: Task) -> None:
        ok = await webhook.post(format_task(task))
        record("discord_posted" if ok else "discord_failed", level="info" if ok else "warning", kind="task", taskId=task.id)

    async def listener(task: Task) -> None:
        if not task.finished or task.id in posted:
            return
        posted.add(task.id)
        job = asyncio.create_task(post(task))
        pending.add(job)
        job.add_done_callback(pending.discard)

    return listener
