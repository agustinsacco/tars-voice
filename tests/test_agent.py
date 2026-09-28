import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from gateway.agent import AgentError, Decision, VoiceAgent, parse_decision, read_context


class FakeModel(BaseHTTPRequestHandler):
    replies: ClassVar[list[str]] = []
    requests: ClassVar[list[dict]] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeModel.requests.append(body)
        content = FakeModel.replies.pop(0) if FakeModel.replies else "{}"
        payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def reply(route, say, request=""):
    return json.dumps({"route": route, "say": say, "tars_request": request})


class ParseTests(unittest.TestCase):
    def test_handoff_needs_a_request(self):
        self.assertEqual(parse_decision(reply("tars", "On it.", "")), Decision("self", "On it."))
        self.assertEqual(parse_decision(reply("tars", "", "Weather tomorrow")).say, "On it. I'll tell you when it's back.")

    def test_invalid_replies_raise(self):
        for content in ("not json", "[]", json.dumps({"route": "maybe", "say": "x"})):
            with self.assertRaises(AgentError):
                parse_decision(content)


class ContextTests(unittest.TestCase):
    def test_reads_memory_files_and_facts(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            (root / "workspace").mkdir()
            (root / "workspace" / "USER.md").write_text("Lives in Montreal.")
            (root / "data" / "memory").mkdir(parents=True)
            (root / "data" / "memory" / "facts.json").write_text(json.dumps({"facts": {"a": {"key": "dog", "value": "Luna"}}}))
            context = read_context(root)
            self.assertIn("Lives in Montreal.", context)
            self.assertIn("- dog: Luna", context)
            self.assertEqual(read_context(root / "missing"), "(no context available)")


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.home = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.home.cleanup()

    def setUp(self):
        FakeModel.replies, FakeModel.requests = [], []
        self.agent = VoiceAgent(f"http://127.0.0.1:{self.server.server_port}", "Sam", Path(self.home.name))

    def test_decide_sends_route_schema_and_keeps_history(self):
        FakeModel.replies = [reply("self", "Twelve."), reply("tars", "On it.", "Weather in Montreal on Thursday.")]
        now = datetime(2026, 9, 23, 9, 41)
        first = asyncio.run(self.agent.decide("What's 15 percent of 80?", "BACKGROUND TASKS\n(none)", now=now))
        second = asyncio.run(self.agent.decide("Weather tomorrow?", "BACKGROUND TASKS\n(none)", now=now))
        self.assertEqual(first, Decision("self", "Twelve."))
        self.assertEqual(second.request, "Weather in Montreal on Thursday.")
        body = FakeModel.requests[1]
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["response_format"]["json_schema"]["schema"]["properties"]["route"]["enum"], ["self", "tars"])
        self.assertIn("You are Tars, Sam's personal assistant", body["messages"][0]["content"])
        self.assertEqual(body["messages"][1], {"role": "user", "content": "Sam: What's 15 percent of 80?"})
        self.assertIn("[Wednesday September 23 2026, 09:41]", body["messages"][-1]["content"])
        self.assertTrue(body["messages"][-1]["content"].endswith("Sam: Weather tomorrow?"))
        self.assertEqual(len(self.agent.history), 4)

    def test_system_notes_are_not_attributed_to_the_owner(self):
        FakeModel.replies = [reply("self", "Tomorrow is sunny.")]
        asyncio.run(self.agent.decide("(Background task #1 just finished.)", "ledger", note=True))
        self.assertTrue(FakeModel.requests[0]["messages"][-1]["content"].endswith("\n(Background task #1 just finished.)"))

    def test_history_is_bounded(self):
        self.agent.history_turns = 2
        FakeModel.replies = [reply("self", "Ok.") for _ in range(5)]
        for _ in range(5):
            asyncio.run(self.agent.decide("hi", "ledger"))
        self.assertEqual(len(self.agent.history), 4)

    def test_summarize_uses_plain_schema(self):
        FakeModel.replies = [json.dumps({"summary": "Checked the weather."})]
        summary = asyncio.run(self.agent.summarize(["Sam: weather?"], ["done: Weather -> Sunny"]))
        self.assertEqual(summary, "Checked the weather.")
        self.assertIn("summary", FakeModel.requests[0]["response_format"]["json_schema"]["schema"]["properties"])
        self.assertEqual(asyncio.run(self.agent.summarize([], [])), "")

    def test_unreachable_model_raises(self):
        agent = VoiceAgent("http://127.0.0.1:9", "Sam", Path(self.home.name), timeout=1)
        with self.assertRaises(AgentError):
            asyncio.run(agent.decide("hi", "ledger"))
        asyncio.run(agent.warm())  # warm-up failures are swallowed


if __name__ == "__main__":
    unittest.main()
