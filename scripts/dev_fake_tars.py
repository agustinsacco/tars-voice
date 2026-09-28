#!/usr/bin/env python3
"""Stand-in for the Tars relay, for testing without touching the real Tars.

Speaks the same NDJSON contract as /v1/turn and answers after a delay, like a
slow tool run. Weather and calendar requests get canned multi-line answers; the
rest are echoed.

    scripts/dev_fake_tars.py 18789 5    # port, seconds per answer
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0


def answer(text: str) -> str:
    lowered = text.lower()
    if "weather" in lowered:
        return "**Thursday:**\n\n- **Sunny and clear**\n- High around **20°C**\n- Low around 8°C\n- Little chance of rain"
    if "calendar" in lowered:
        return "You have a standup at 10:00 and a dentist appointment at 15:30."
    return f"(fake Tars) Done: {text}"


class Relay(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.end_headers()
        self.write({"type": "accepted"})
        time.sleep(DELAY)
        self.write({"type": "answer", "text": answer(str(body.get("text", "")))})
        self.write({"type": "done"})

    def write(self, event: dict) -> None:
        self.wfile.write((json.dumps(event) + "\n").encode())
        self.wfile.flush()

    def log_message(self, *_args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Relay).serve_forever()
