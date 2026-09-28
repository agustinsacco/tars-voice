"""Local voice agent: answers conversational turns and routes the rest to Tars.

The agent runs on a small loopback llama.cpp server. It must commit to a route
before it speaks (route-first JSON), which stops it from claiming background
work it never started. Tars itself is unchanged: routed requests go through the
existing relay as ordinary turns.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}
DEFAULT_HANDOFF_SAY = "On it. I'll tell you when it's back."

INSTRUCTIONS = """You are Tars, {owner}'s personal assistant, talking with {owner} by voice.
Everything in "say" is spoken aloud: one or two short sentences, plain words, no markdown,
lists, emoji, or URLs.

You are the quick, conversational side of Tars. Slower work runs in the background with
the full Tars, which has {owner}'s personal data, live information, and tools, and can
take actions.

Answer yourself (route "self") when you can: greetings, small talk, acknowledgements,
general knowledge that does not change over time, simple math, clarifying questions, and
anything already in YOUR CONTEXT or BACKGROUND TASKS.

Start background work (route "tars") for anything that needs {owner}'s personal data not
stated in your context (health, sleep, calendar, email, money, portfolio, home, cameras,
cars), live or current information (weather, news, prices, anything about today), an
action (send, schedule, remind, run, buy, change, remember), or tools. Facts in your
context may be stale; use the background for questions about now or today. Never guess
personal facts (such as who is whose relative) and never claim an action is done before
its task is done. Do not start work that is already queued or working in BACKGROUND
TASKS; say it is still in progress instead. A background request must be standalone:
resolve "it", "that", and "tomorrow" to concrete things and dates. When you start one,
say one short sentence such as "On it." Never mention asking or handing off to anyone:
to {owner} you are one assistant.

When a background task is done, answer from its result in your own short spoken words.

If the utterance is only a backchannel (mm-hmm, okay, thanks) or is not addressed to you,
reply with a few words at most.

Reply with JSON only. Set "route" to "tars" to start background work (with a standalone
"tars_request") or "self" to answer yourself (with "tars_request" empty). "say" is what
{owner} hears.

EXAMPLES (utterance -> decision)
- "What's 12 times 7?" -> self; say "Eighty-four."
- "Thanks." -> self; say "Sure."
- "What's on my calendar Friday?" -> tars; request "List {owner}'s calendar events for Friday <date>."; say "On it."
- "Remind me to water the plants tomorrow morning." -> tars; request "Remind {owner} to water the plants on <date> in the morning."; say "On it, I'll set that up."
- "Remember that I parked on level three." -> tars; request "Remember: {owner} parked on level three."; say "Got it."
- "What's my brother's name?" (not stated in YOUR CONTEXT) -> tars; request "What is the name of {owner}'s brother?"; say "Let me check."
"""


class AgentError(RuntimeError):
    """The local model was unreachable or returned an unusable reply."""


@dataclass(frozen=True)
class Decision:
    route: str
    say: str
    request: str = ""


def read_context(tars_home: Path, limit: int = 8000) -> str:
    """Read Tars' curated memory files. Read-only; never writes to Tars."""
    parts: list[str] = []
    for name in ("USER.md", "MEMORY.md"):
        path = tars_home / "workspace" / name
        if path.is_file():
            parts.append(f"## {name}\n" + path.read_text(errors="replace").replace("\n§\n", "\n"))
    facts = tars_home / "data" / "memory" / "facts.json"
    if facts.is_file():
        try:
            data = json.loads(facts.read_text())
            lines = [f"- {v.get('key')}: {v.get('value')}" for v in data.get("facts", {}).values()]
            parts.append("## facts\n" + "\n".join(lines))
        except (json.JSONDecodeError, AttributeError, OSError):
            pass
    return ("\n\n".join(parts) or "(no context available)")[:limit]


def parse_decision(content: str) -> Decision:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise AgentError("voice model returned invalid JSON") from error
    if not isinstance(data, dict) or data.get("route") not in ("self", "tars"):
        raise AgentError("voice model returned no route")
    say = " ".join(str(data.get("say", "")).split())
    request = " ".join(str(data.get("tars_request", "")).split())
    if data["route"] == "tars" and request:
        return Decision("tars", say or DEFAULT_HANDOFF_SAY, request[:2000])
    return Decision("self", say)


class VoiceAgent:
    def __init__(self, url: str, owner: str, tars_home: Path, timeout: float = 30.0, history_turns: int = 8):
        self.url = url.rstrip("/")
        self.owner = owner
        self.tars_home = tars_home
        self.timeout = timeout
        self.history_turns = history_turns
        self.system = ""
        self.history: list[dict[str, str]] = []
        self._lock = asyncio.Lock()

    def start_call(self) -> None:
        """Fresh conversation. Instructions and context form a stable, cacheable prefix."""
        context = read_context(self.tars_home)
        self.system = INSTRUCTIONS.format(owner=self.owner) + f"\nYOUR CONTEXT\n{context}"
        self.history = []

    async def warm(self) -> None:
        """Prefill the prompt prefix so the first real turn is fast."""
        if not self.system:
            self.start_call()
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": "Hi."}]
        with contextlib.suppress(AgentError):
            await asyncio.to_thread(self._complete, messages, 1, None)

    async def decide(self, text: str, ledger: str, *, note: bool = False, now: datetime | None = None) -> Decision:
        """Answer or route one utterance. `note` marks a system note, not owner speech."""
        async with self._lock:
            if not self.system:
                self.start_call()
            line = text if note else f"{self.owner}: {text}"
            stamp = (now or datetime.now()).strftime("%A %B %d %Y, %H:%M")
            messages = [
                {"role": "system", "content": self.system},
                *self.history,
                {"role": "user", "content": f"[{stamp}]\n{ledger}\n\n{line}"},
            ]
            decision = parse_decision(await asyncio.to_thread(self._complete, messages, 200, ROUTE_SCHEMA))
            reply = {"route": decision.route, "say": decision.say, "tars_request": decision.request}
            self.history += [{"role": "user", "content": line}, {"role": "assistant", "content": json.dumps(reply)}]
            self.history = self.history[-2 * self.history_turns:]
            return decision

    async def summarize(self, transcript: list[str], tasks: list[str]) -> str:
        """One or two sentences describing a finished call, for the Discord log."""
        if not transcript and not tasks:
            return ""
        prompt = (
            f"Summarize this voice conversation between {self.owner} and Tars in one or two plain "
            "sentences for a chat log. Say what was asked and what got done. No markdown."
        )
        body = "\n".join(transcript[-60:]) + ("\n\nBackground tasks:\n" + "\n".join(tasks) if tasks else "")
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": body[-12000:]}]
        content = await asyncio.to_thread(self._complete, messages, 160, SUMMARY_SCHEMA)
        try:
            return " ".join(str(json.loads(content).get("summary", "")).split())
        except (json.JSONDecodeError, AttributeError) as error:
            raise AgentError("voice model returned an invalid summary") from error

    def _complete(self, messages: list[dict[str, str]], max_tokens: int, schema: dict | None) -> str:
        body: dict = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "top_p": 0.8,
            "top_k": 20,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"schema": schema}}
        request = urllib.request.Request(
            f"{self.url}/v1/chat/completions",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
            return str(data["choices"][0]["message"]["content"])
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, ValueError) as error:
            raise AgentError("voice model is unavailable") from error
