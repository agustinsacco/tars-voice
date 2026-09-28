"""Small helpers for safe client events and sentence-level synthesis."""
from __future__ import annotations

import re

_SENTENCE = re.compile(r"(.+?(?:[.!?](?=\s|$)|\n+))", re.DOTALL)
_MARKUP = re.compile(r"[`*_>#]+")


class SentenceBuffer:
    def __init__(self) -> None:
        self._buffer = ""

    def push(self, text: str) -> list[str]:
        self._buffer += text
        output: list[str] = []
        while match := _SENTENCE.match(self._buffer):
            sentence = match.group(1).strip()
            self._buffer = self._buffer[match.end():]
            if sentence:
                output.append(sentence)
        return output

    def flush(self) -> str:
        text, self._buffer = self._buffer.strip(), ""
        return text


def speakable_text(text: str) -> str:
    text = re.sub(r"https?://\S+", "a link", text)
    text = _MARKUP.sub("", text)
    return " ".join(text.split())[:4000]


def first_sentences(text: str, limit: int = 220) -> str:
    """A short spoken version of a long answer: whole sentences up to `limit` characters."""
    lines = [_MARKUP.sub("", re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)).strip() for line in text.splitlines()]
    prose = " ".join(line if re.search(r"[.!?:]$", line) else f"{line}." for line in lines if line)
    spoken = ""
    for sentence in SentenceBuffer().push(speakable_text(prose) + "\n"):
        if spoken and len(spoken) + len(sentence) > limit:
            break
        spoken = f"{spoken} {sentence}".strip()
    return spoken[: limit + 80]


def safe_status(event_type: str) -> str:
    return {
        "accepted": "Request accepted",
        "thinking": "Thinking",
        "status": "Working",
        "error": "The request could not be completed",
    }.get(event_type, "Working")
