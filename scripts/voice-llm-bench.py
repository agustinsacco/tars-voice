#!/usr/bin/env python3
"""Voice-shaped benchmark for voice-agent LLM candidates.

Measures what a spoken turn feels like, not raw throughput: time to first token,
time to the first speakable sentence, time to a complete Tars handoff call, and
whether the local/handoff decision is right. Standard library only; run it on the
host against a loopback llama-server.

    scripts/voice-llm-bench.py --url http://127.0.0.1:8791 --label qwen36-a3b-q4
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.request
from datetime import datetime
from pathlib import Path

TARS_HOME = Path.home() / ".tars"
SENTENCE_END = re.compile(r"[.!?](\s|$)")
SAY_FIELD = re.compile(r'"say"\s*:\s*"((?:[^"\\]|\\.)*)')
# Spoken claims of a handoff or action that only Tars can perform.
FAKE_HANDOFF = re.compile(
    r"\b(asked tars|ask(ing)? tars|i'll check|checking|i've set|reminder set|i've sent|"
    r"i've asked|i'll keep that in mind|noted|i'll remember)\b",
    re.IGNORECASE,
)

# Route-first structured output: the model commits to self/tars before it speaks,
# so it cannot say "I asked Tars" without actually handing off.
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

EXAMPLES = """EXAMPLES (utterance -> decision)
- "What's 12 times 7?" -> self; say "Eighty-four."
- "Who wrote Dune?" -> self; say "Frank Herbert."
- "Thanks." -> self; say "Sure."
- "What's on my calendar Friday?" -> tars; request "List Agustin's calendar events for Friday September 25 2026."; say "Let me ask Tars."
- "Remind me to water the plants tomorrow morning." -> tars; request "Remind Agustin to water the plants on September 24 2026 in the morning."; say "I'll have Tars set that."
- "Remember that I parked on level three." -> tars; request "Remember: Agustin parked on level three."; say "I'll tell Tars to remember that."
- "How did I sleep?" -> tars; request "Report Agustin's sleep from last night."; say "Asking Tars now."
- "What's my brother's name?" (not stated in YOUR CONTEXT) -> tars; request "What is the name of Agustin's brother?"; say "Let me check with Tars."
"""

ASK_TARS = {
    "type": "function",
    "function": {
        "name": "ask_tars",
        "description": (
            "Hand a request to Tars, the main assistant, which has tools, live data, "
            "personal data, and can take actions. Returns immediately; Tars answers later."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "Standalone request Tars can act on without this conversation.",
                }
            },
            "required": ["request"],
        },
    },
}

TASKS = """TARS TASKS
- done: "What is the weather in Toronto today?" -> "18 C and light rain, clearing by evening."
- pending (asked 2 minutes ago): "Run car-sentry on the front camera and list the cars in front of the house."
"""

# (utterance, expected) - expected is "local", "handoff", or "any" (not scored).
# The first nine are the owner's real utterances from the 2026-09-23 session.
CASES = [
    ("Hey, how you doing?", "local"),
    ("What was my sleep score last night?", "handoff"),
    ("It's very good.", "local"),
    ("Mm-hmm.", "local"),
    ("I'll ask it what's on your calendar.", "any"),
    ("Well, it's on my calendar tomorrow.", "any"),
    ("Is it an indicator that it's working on it?", "local"),
    ("What's on my calendar tomorrow?", "handoff"),
    ("Can you run CarCentry and tell me what cars are in the front?", "local"),  # already pending
    ("What's the capital of Portugal?", "local"),
    ("What's fifteen percent of two hundred forty?", "local"),
    ("Tell me a quick joke.", "local"),
    ("Explain how a heat pump works, briefly.", "local"),
    ("What did Tars say about the weather?", "local"),
    ("Remind me to call my mom at six tonight.", "handoff"),
    ("Send an email to John saying I'll be ten minutes late.", "handoff"),
    ("How is my portfolio doing today?", "handoff"),
    ("What's the weather going to be tomorrow?", "handoff"),
    ("What's my sister's birthday?", "handoff"),  # names are in context, the relation is not
    ("What's my dentist's name?", "handoff"),
    ("Remember that I prefer window seats.", "handoff"),
]


def read_context(limit: int) -> str:
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
    text = "\n\n".join(parts) or "(no context available)"
    return text[:limit]


def system_prompt(context: str, run_id: str, mode: str) -> str:
    if mode == "json":
        handoff = (
            'Reply with JSON only. Set "route" to "tars" to hand off (with a standalone '
            '"tars_request") or "self" to answer yourself (with "tars_request" empty). '
            '"say" is what Agustin hears.'
        )
    else:
        handoff = "To hand off, call the ask_tars tool; saying you asked Tars is not enough."
    now = datetime(2026, 9, 23, 9, 30).strftime("%A %B %d %Y, %H:%M")
    # run_id leads the prompt so no run reuses another run's cached prefix; the cold
    # request then measures a genuinely cold prefill.
    return f"""[bench run {run_id}]
You are Tars Voice, the fast spoken front-end for Tars, Agustin's personal assistant.
You talk with Agustin by voice. Everything you write is spoken aloud: one or two short
sentences, plain words, no markdown, lists, emoji, or URLs.

Answer yourself when you can: greetings, small talk, acknowledgements, general knowledge
that does not change over time, simple math, clarifying questions, and anything already
in YOUR CONTEXT or TARS TASKS below.

Hand off to Tars anything that needs Agustin's personal data not stated in your context
(health, sleep, calendar, email, money, portfolio, home, cameras, cars), live or current
information (weather, news, prices, anything "today"), an action (send, schedule, remind,
run, buy, change, remember), or tools. Facts in your context may be stale; hand off when
Agustin asks about now or today. Never guess or infer personal facts (such as who is
whose relative) and never claim an action is done. Do not hand off again something
already pending in TARS TASKS; tell Agustin it is still in progress instead. A handoff
request must be standalone (resolve "it", "that", "tomorrow" to concrete things and
dates); also say one short sentence telling Agustin you have asked Tars.

If the utterance is only a backchannel (mm-hmm, okay, thanks) or is not addressed to you,
reply with a few words at most.

{handoff}

{EXAMPLES}
Current time: {now}.

YOUR CONTEXT
{context}

{TASKS}"""


def spoken(content: str, mode: str) -> str:
    if mode != "json":
        return content
    match = SAY_FIELD.search(content)
    return match.group(1) if match else ""


def run_case(url: str, system: str, utterance: str, args: argparse.Namespace) -> dict:
    body = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": utterance}],
        "stream": True,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "top_p": 0.8,
        "top_k": 20,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
        "timings_per_token": False,
    }
    if args.mode == "json":
        body["response_format"] = {"type": "json_schema", "json_schema": {"schema": ROUTE_SCHEMA}}
    else:
        body["tools"] = [ASK_TARS]
    request = urllib.request.Request(
        f"{url}/v1/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    first_token = first_sentence = tool_done = None
    content, tool_name, tool_args, timings = "", "", "", {}
    with urllib.request.urlopen(request, timeout=180) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            timings = chunk.get("timings") or timings
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                piece = delta.get("content") or ""
                calls = delta.get("tool_calls") or []
                if (piece or calls) and first_token is None:
                    first_token = time.monotonic() - started
                if piece:
                    content += piece
                    if first_sentence is None and SENTENCE_END.search(spoken(content, args.mode)):
                        first_sentence = time.monotonic() - started
                for call in calls:
                    fn = call.get("function") or {}
                    tool_name += fn.get("name") or ""
                    tool_args += fn.get("arguments") or ""
                if choice.get("finish_reason") == "tool_calls":
                    tool_done = time.monotonic() - started
    total = time.monotonic() - started
    said = spoken(content, args.mode)
    if first_sentence is None and said.strip():
        first_sentence = total
    handoff_request = None
    if args.mode == "json":
        try:
            parsed = json.loads(content)
            said = parsed.get("say", "")
            if parsed.get("route") == "tars":
                handoff_request = parsed.get("tars_request") or "<empty request>"
                tool_done = total
        except (json.JSONDecodeError, AttributeError):
            handoff_request = f"<invalid json: {content[:80]}>"
            tool_done = total
    elif tool_name:
        tool_done = tool_done or total
        try:
            handoff_request = json.loads(tool_args).get("request")
        except (json.JSONDecodeError, AttributeError):
            handoff_request = f"<invalid arguments: {tool_args[:80]}>"
    decision = "handoff" if handoff_request is not None else "local"
    return {
        "utterance": utterance,
        "decision": decision,
        "handoffRequest": handoff_request,
        "said": said.strip(),
        "fakeHandoff": decision == "local" and bool(FAKE_HANDOFF.search(said)),
        "ttft": first_token,
        "firstSentence": first_sentence,
        "handoffReady": tool_done,
        "total": total,
        "promptTokens": timings.get("prompt_n"),
        "cachedTokens": timings.get("cache_n"),
        "promptPerSecond": timings.get("prompt_per_second"),
        "decodePerSecond": timings.get("predicted_per_second"),
    }


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8791")
    parser.add_argument("--label", required=True)
    parser.add_argument("--context-chars", type=int, default=8000)
    parser.add_argument("--max-tokens", type=int, default=160)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--mode", choices=("tools", "json"), default="json")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    run_id = f"{args.label}-{args.mode}-{args.context_chars}-{time.time_ns()}"
    system = system_prompt(read_context(args.context_chars), run_id, args.mode)
    # Cold: first request after load, full prompt prefill.
    cold = run_case(args.url, system, "Hi there.", args)
    results = []
    for round_index in range(args.rounds):
        for utterance, expected in CASES:
            result = run_case(args.url, system, utterance, args)
            result.update(expected=expected, round=round_index + 1)
            # "I already asked Tars" is right when the task is pending (expected local).
            result["fakeHandoff"] = result["fakeHandoff"] and expected != "local"
            results.append(result)

    scored = [r for r in results if r["expected"] != "any"]
    correct = [r for r in scored if r["decision"] == r["expected"]]
    silent_handoffs = [r for r in results if r["decision"] == "handoff" and not r["said"]]
    summary = {
        "label": args.label,
        "mode": args.mode,
        "contextChars": args.context_chars,
        "coldPromptTokens": cold["promptTokens"],
        "coldTtft": round(cold["ttft"] or 0, 3),
        "coldPromptPerSecond": cold["promptPerSecond"],
        "decisionAccuracy": f"{len(correct)}/{len(scored)}",
        "medianTtft": median(r["ttft"] for r in results),
        "medianFirstSentence": median(r["firstSentence"] for r in results),
        "medianHandoffReady": median(r["handoffReady"] for r in results),
        "medianDecodePerSecond": median(r["decodePerSecond"] for r in results),
        "medianPromptPerSecond": median(r["promptPerSecond"] for r in results),
        "handoffsWithoutSpokenAck": len(silent_handoffs),
        "fakeHandoffs": sum(r["fakeHandoff"] for r in results),
    }
    print(json.dumps(summary, indent=1))
    for r in results:
        if r["round"] != 1:
            continue
        mark = "  " if r["expected"] in ("any", r["decision"]) else "XX"
        mark = "FK" if r["fakeHandoff"] else mark
        timing = r["handoffReady"] if r["decision"] == "handoff" else r["firstSentence"]
        detail = r["handoffRequest"] if r["decision"] == "handoff" else r["said"]
        print(f"{mark} {r['decision']:<7} {timing or 0:5.2f}s  {r['utterance'][:44]:<44} | {(detail or '')[:90]}")
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "cold": cold, "results": results}, indent=1))


if __name__ == "__main__":
    main()
