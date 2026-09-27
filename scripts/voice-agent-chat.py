#!/usr/bin/env python3
"""Text prototype of the voice agent: local model first, async handoff to Tars.

Each line goes to the voice LLM (127.0.0.1:8791), which either answers itself or
routes to Tars. Handoffs join a queue that one worker relays to Tars (single-flight,
retried while Tars is busy); answers print when they arrive while you keep typing.
Runs on the host so the relay token never leaves it. Standard library only.

    ssh -t stark@stark ~/.tars/apps/tars-voice/scripts/voice-agent-chat.py

Commands: /tasks lists handoffs, /quit exits.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TARS_HOME = Path.home() / ".tars"
LLM_URL = os.getenv("VOICE_LLM_URL", "http://127.0.0.1:8791")
RELAY_URL = os.getenv("VOICE_RELAY_URL", "http://127.0.0.1:8789/v1/turn")
BUSY_PREFIX = "❌ **Supervisor Error:** I'm currently working"
BUSY_RETRY_SECONDS = 15
BUSY_MAX_RETRIES = 40
HISTORY_TURNS = 8

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"enum": ["self", "tars"]},
        "say": {"type": "string"},
        "tars_request": {"type": "string"},
    },
    "required": ["route", "say", "tars_request"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You are Tars Voice, the fast spoken front-end for Tars, Agustin's personal assistant.
You talk with Agustin by voice. Everything you write is spoken aloud: one or two short
sentences, plain words, no markdown, lists, emoji, or URLs.

Answer yourself when you can: greetings, small talk, acknowledgements, general knowledge
that does not change over time, simple math, clarifying questions, and anything already
in YOUR CONTEXT or TARS TASKS.

Hand off to Tars anything that needs Agustin's personal data not stated in your context
(health, sleep, calendar, email, money, portfolio, home, cameras, cars), live or current
information (weather, news, prices, anything "today"), an action (send, schedule, remind,
run, buy, change, remember), or tools. Facts in your context may be stale; hand off when
Agustin asks about now or today. Never guess or infer personal facts (such as who is
whose relative) and never claim an action is done. Do not hand off again something
already pending in TARS TASKS; tell Agustin it is still in progress instead. A handoff
request must be standalone (resolve "it", "that", "tomorrow" to concrete things and
dates); also say one short sentence telling Agustin you have asked Tars. When a Tars
task is done, answer from its result in your own short spoken words.

If the utterance is only a backchannel (mm-hmm, okay, thanks) or is not addressed to you,
reply with a few words at most.

Reply with JSON only. Set "route" to "tars" to hand off (with a standalone
"tars_request") or "self" to answer yourself (with "tars_request" empty). "say" is what
Agustin hears.

EXAMPLES (utterance -> decision)
- "What's 12 times 7?" -> self; say "Eighty-four."
- "Thanks." -> self; say "Sure."
- "What's on my calendar Friday?" -> tars; request "List Agustin's calendar events for Friday <date>."; say "Let me ask Tars."
- "Remind me to water the plants tomorrow morning." -> tars; request "Remind Agustin to water the plants on <date> in the morning."; say "I'll have Tars set that."
- "Remember that I parked on level three." -> tars; request "Remember: Agustin parked on level three."; say "I'll tell Tars to remember that."
- "What's my brother's name?" (not stated in YOUR CONTEXT) -> tars; request "What is the name of Agustin's brother?"; say "Let me check with Tars."
"""


def read_context(limit: int = 8000) -> str:
    parts = []
    for name in ("USER.md", "MEMORY.md"):
        path = TARS_HOME / "workspace" / name
        if path.is_file():
            parts.append(f"## {name}\n" + path.read_text(errors="replace").replace("\n§\n", "\n"))
    facts = TARS_HOME / "data" / "memory" / "facts.json"
    if facts.is_file():
        try:
            data = json.loads(facts.read_text())
            lines = [f"- {v.get('key')}: {v.get('value')}" for v in data.get("facts", {}).values()]
            parts.append("## facts\n" + "\n".join(lines))
        except (json.JSONDecodeError, AttributeError):
            pass
    return ("\n\n".join(parts) or "(no context available)")[:limit]


def relay_token() -> str:
    for line in (TARS_HOME / "secrets" / "tars-voice.env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "VOICE_RELAY_TOKEN":
            return value.strip().strip("'\"")
    raise SystemExit("VOICE_RELAY_TOKEN not found in ~/.tars/secrets/tars-voice.env")


@dataclass
class Task:
    id: int
    request: str
    asked: float
    status: str = "queued"
    answer: str = ""


class TarsQueue:
    """Single-flight relay to Tars; Tars rejects concurrent turns, so retry while busy."""

    def __init__(self, token: str, print_lock: threading.Lock):
        self.token = token
        self.print_lock = print_lock
        self.tasks: list[Task] = []
        self.pending: queue.Queue[Task] = queue.Queue()
        threading.Thread(target=self.worker, name="tars-relay", daemon=True).start()

    def submit(self, request: str) -> Task:
        task = Task(len(self.tasks) + 1, request, time.time())
        self.tasks.append(task)
        self.pending.put(task)
        return task

    def ledger(self) -> str:
        if not self.tasks:
            return "TARS TASKS\n(none)"
        lines = ["TARS TASKS"]
        for task in self.tasks[-6:]:
            age = f"{int(time.time() - task.asked) // 60} min ago"
            line = f'- #{task.id} {task.status} (asked {age}): "{task.request}"'
            if task.answer:
                line += f' -> "{task.answer[:800]}"'
            lines.append(line)
        return "\n".join(lines)

    def worker(self) -> None:
        while True:
            task = self.pending.get()
            for attempt in range(BUSY_MAX_RETRIES):
                task.status = "running" if attempt == 0 else "waiting for Tars"
                try:
                    answer = self.relay(task.request)
                except Exception as error:  # report and move on to the next task
                    task.status, task.answer = "failed", f"relay error: {error}"
                    break
                if answer.startswith(BUSY_PREFIX):
                    task.status = "waiting for Tars (busy)"
                    time.sleep(BUSY_RETRY_SECONDS)
                    continue
                task.status, task.answer = "done", answer
                break
            else:
                task.status, task.answer = "failed", "Tars stayed busy"
            with self.print_lock:
                print(f"\n[tars #{task.id} {task.status} in {time.time() - task.asked:.0f}s]\n{task.answer}\n> ", end="", flush=True)

    def relay(self, text: str) -> str:
        request = urllib.request.Request(
            RELAY_URL,
            json.dumps({"text": text, "sessionId": "tars-voice-chat"}).encode(),
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/x-ndjson",
            },
            method="POST",
        )
        answer, errors = [], []
        with urllib.request.urlopen(request, timeout=900) as response:
            for raw in response:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if event.get("type") == "answer":
                    answer.append(str(event.get("text", "")))
                elif event.get("type") == "error":
                    errors.append(str(event.get("text", "")) or "error")
        if not answer and errors:
            raise RuntimeError("; ".join(errors))
        return "\n".join(answer).strip()


def complete(messages: list[dict], max_tokens: int = 200) -> tuple[dict, float]:
    body = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "top_p": 0.8,
        "top_k": 20,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"schema": ROUTE_SCHEMA}},
    }
    request = urllib.request.Request(
        f"{LLM_URL}/v1/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.load(response)
    content = data["choices"][0]["message"]["content"]
    seconds = time.monotonic() - started
    return (json.loads(content) if max_tokens > 1 else {}), seconds


def main() -> None:
    print_lock = threading.Lock()
    tars = TarsQueue(relay_token(), print_lock)
    # Stable prefix (instructions + context) first so llama.cpp reuses its KV cache;
    # time and the task ledger ride on the newest user message only.
    system = f"{INSTRUCTIONS}\nYOUR CONTEXT\n{read_context()}"
    history: list[dict] = []

    print("warming up the voice model...", flush=True)
    _, seconds = complete([{"role": "system", "content": system}, {"role": "user", "content": "Hi."}], 1)
    print(f"ready ({seconds:.1f}s). Type to talk; /tasks, /quit.\n")

    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/tasks":
            print(tars.ledger())
            continue
        now = datetime.now().strftime("%A %B %d %Y, %H:%M")
        turn = f"[{now}]\n{tars.ledger()}\n\nAgustin: {text}"
        messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": turn}]
        try:
            reply, seconds = complete(messages)
        except Exception as error:
            print(f"(voice model error: {error})")
            continue
        say = reply.get("say", "").strip()
        handoff = reply.get("route") == "tars" and reply.get("tars_request", "").strip()
        with print_lock:
            print(f"tars-voice ({seconds:.2f}s): {say}")
            if handoff:
                task = tars.submit(handoff)
                print(f'  -> handed to Tars as #{task.id}: "{handoff}"')
        history += [{"role": "user", "content": text}, {"role": "assistant", "content": json.dumps(reply)}]
        history = history[-2 * HISTORY_TURNS:]

    busy = [t for t in tars.tasks if t.status not in ("done", "failed")]
    if busy:
        print(f"{len(busy)} Tars task(s) still running; their answers stay in the Tars session.")


if __name__ == "__main__":
    main()
