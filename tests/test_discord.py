import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from gateway.agent import AgentError, Decision
from gateway.discord import DiscordWebhook, format_call, format_task, task_poster
from gateway.handoff import Task
from tests.test_agent_turns import build, until


class FakeDiscord:
    def __init__(self, ok=True):
        self.ok = ok
        self.posts = []

    async def post(self, content):
        self.posts.append(content)
        return self.ok


class Hook(BaseHTTPRequestHandler):
    statuses: ClassVar[list[int]] = []
    bodies: ClassVar[list[dict]] = []

    def do_POST(self):
        Hook.bodies.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
        status = Hook.statuses.pop(0) if Hook.statuses else 204
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        if status == 429:
            self.wfile.write(b'{"retry_after": 0.01}')

    def log_message(self, *_):
        pass


class FormatTests(unittest.TestCase):
    def test_task_and_call_messages(self):
        done = Task(1, "Weather in Montreal on Thursday", status="done", answer="Sunny, high 20.")
        failed = Task(2, "Close the garage", status="failed", answer="Tars couldn't be reached.")
        running = Task(3, "Summarize my email", status="working")
        self.assertEqual(format_task(done), "🎙️ **Weather in Montreal on Thursday**\nSunny, high 20.")
        self.assertIn("_Tars couldn't be reached._", format_task(failed))
        call = format_call(6, "Checked the weather.", [done, failed, running])
        self.assertEqual(call.splitlines(), [
            "🎙️ **Voice call · 6 min**",
            "Checked the weather.",
            "✓ Weather in Montreal on Thursday",
            "✗ Close the garage",
            "… Summarize my email (still running)",
        ])
        self.assertLessEqual(len(format_task(Task(4, "x", status="done", answer="y" * 5000))), 2000)


class WebhookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/hook"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_posts_without_mentions_and_retries_rate_limits(self):
        Hook.statuses, Hook.bodies = [429, 204], []
        self.assertTrue(asyncio.run(DiscordWebhook(self.url).post("hello @everyone")))
        self.assertEqual(len(Hook.bodies), 2)
        self.assertEqual(Hook.bodies[0]["allowed_mentions"], {"parse": []})
        Hook.statuses = [500]
        self.assertFalse(asyncio.run(DiscordWebhook(self.url).post("x")))
        self.assertFalse(asyncio.run(DiscordWebhook("http://127.0.0.1:9/x", timeout=1).post("x")))

    def test_task_poster_posts_each_finished_task_once(self):
        async def run():
            fake, events = FakeDiscord(), []
            listener = task_poster(fake, lambda event, **_: events.append(event))
            task = Task(1, "Weather", status="working")
            await listener(task)
            task.status, task.answer = "done", "Sunny."
            await listener(task)
            await listener(task)
            await until(lambda: events)
            return fake.posts, events

        posts, events = asyncio.run(run())
        self.assertEqual(posts, ["🎙️ **Weather**\nSunny."])
        self.assertEqual(events, ["discord_posted"])


class CallSummaryTests(unittest.TestCase):
    def test_end_call_summarizes_posts_and_resets(self):
        async def run():
            ws, agent, queue, connection = build(
                [Decision("tars", "On it.", "Weather"), Decision("self", "Sunny.")], {"Weather": ["Sunny."]}
            )
            connection.discord = FakeDiscord()
            queue.start()
            await connection.submit_text("Weather tomorrow?")
            await until(lambda: len(agent.calls) == 2 and connection.turn_task is None)
            await connection.control({"type": "end_call"})
            await queue.stop()
            summary = ws.events[-1]
            self.assertEqual(summary["type"], "call_summary")
            self.assertEqual((summary["minutes"], summary["summary"], summary["posted"]), (1, "Checked Thursday's weather.", True))
            self.assertEqual([t["request"] for t in summary["tasks"]], ["Weather"])
            self.assertIn("Sam: Weather tomorrow?", agent.summarized[0][0])
            self.assertIn("✓ Weather", connection.discord.posts[0])
            self.assertEqual((connection.transcript, connection.call_task_ids), ([], []))

        asyncio.run(run())

    def test_empty_call_posts_nothing_and_summary_failure_still_posts(self):
        async def run():
            ws, agent, _, connection = build([Decision("self", "Hi.")])
            connection.discord = FakeDiscord()
            await connection.control({"type": "end_call"})
            self.assertEqual(connection.discord.posts, [])
            self.assertFalse(ws.events[-1]["posted"])
            agent.summary = AgentError("down")
            await connection.submit_text("Hello")
            await until(lambda: connection.turn_task is None)
            await connection.control({"type": "end_call"})
            self.assertEqual(connection.discord.posts, ["🎙️ **Voice call · 1 min**"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
