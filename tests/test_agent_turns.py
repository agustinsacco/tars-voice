import asyncio
import unittest

from gateway.agent import AgentError, Decision
from gateway.app import VoiceConnection
from gateway.handoff import HandoffQueue
from tests.test_handoff import ScriptedRelay
from tests.test_turns import FakeWebSocket


class FakeAgent:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []
        self.started = 0

    def start_call(self):
        self.started += 1

    async def warm(self):
        pass

    async def decide(self, text, ledger, *, note=False, now=None):
        self.calls.append((text, note, ledger))
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return decision


class FakeTTS:
    async def synthesize(self, text):
        return b"RIFF" + text.encode()


async def until(condition, message="condition not met"):
    for _ in range(400):
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(message)


def build(decisions, script=None):
    ws = FakeWebSocket()
    agent = FakeAgent(decisions)
    queue = HandoffQueue(ScriptedRelay(script or {}), busy_retry_seconds=0.001)
    ws.app.state.handoffs = queue
    ws.app.state.agent_factory = lambda: agent
    ws.app.state.tts = FakeTTS()
    connection = VoiceConnection(ws)
    queue.subscribe(connection.on_task)
    return ws, agent, queue, connection


def types(ws):
    return [event["type"] for event in ws.events]


class AgentTurnTests(unittest.TestCase):
    def test_local_answer_is_spoken_without_tars(self):
        async def run():
            ws, _, queue, connection = build([Decision("self", "Twelve.")])
            await connection.submit_text("What's 15 percent of 80?")
            await until(lambda: connection.turn_task is None)
            self.assertEqual(types(ws), ["status", "answer", "speakable", "audio", "done"])
            self.assertEqual(ws.events[1]["text"], "Twelve.")
            self.assertEqual(queue.tasks, [])
            self.assertEqual(connection.transcript, ["Sam: What's 15 percent of 80?", "Tars: Twelve."])

        asyncio.run(run())

    def test_handoff_runs_in_background_and_result_is_announced(self):
        async def run():
            ws, agent, queue, connection = build(
                [Decision("tars", "On it.", "Weather in Montreal on Thursday."), Decision("self", "Thursday looks sunny.")],
                {"Weather in Montreal on Thursday.": ["Sunny, high 20."]},
            )
            queue.start()
            await connection.submit_text("What's the weather tomorrow?")
            await until(lambda: len(agent.calls) == 2 and connection.turn_task is None, "result was not announced")
            await queue.stop()
            task = queue.tasks[0]
            self.assertEqual((task.status, task.answer, task.announced), ("done", "Sunny, high 20.", True))
            statuses = [e["task"]["status"] for e in ws.events if e["type"] == "task"]
            self.assertEqual(statuses, ["queued", "working", "done"])
            note, is_note, ledger = agent.calls[1]
            self.assertTrue(is_note)
            self.assertIn("#1", note)
            self.assertIn('-> "Sunny, high 20."', ledger)
            answers = [e for e in ws.events if e["type"] == "answer"]
            self.assertEqual([(a["text"], a["taskId"]) for a in answers], [("On it.", None), ("Thursday looks sunny.", 1)])

        asyncio.run(run())

    def test_model_failure_hands_the_raw_request_to_tars(self):
        async def run():
            ws, _, queue, connection = build([AgentError("down")])
            await connection.submit_text("Is the garage closed?")
            await until(lambda: connection.turn_task is None)
            self.assertEqual(queue.tasks[0].request, "Is the garage closed?")
            self.assertIn("On it", next(e["text"] for e in ws.events if e["type"] == "answer"))

        asyncio.run(run())

    def test_failed_announcement_falls_back_to_the_answer(self):
        async def run():
            ws, _, queue, connection = build(
                [Decision("tars", "On it.", "Weather"), AgentError("down")],
                {"Weather": ["**Sunny** and 20 degrees.\n- Low of 8."]},
            )
            queue.start()
            await connection.submit_text("Weather?")
            await until(lambda: queue.tasks and queue.tasks[0].announced and connection.turn_task is None)
            await queue.stop()
            self.assertEqual([e["text"] for e in ws.events if e["type"] == "answer"][-1], "Sunny and 20 degrees. Low of 8.")

        asyncio.run(run())

    def test_quiet_mode_shows_text_but_sends_no_audio(self):
        async def run():
            ws, _, _, connection = build([Decision("self", "Frank Herbert.")])
            await connection.control({"type": "quiet", "on": True})
            await connection.submit_text("Who wrote Dune?")
            await until(lambda: connection.turn_task is None)
            self.assertEqual(types(ws), ["status", "answer", "done"])

        asyncio.run(run())

    def test_only_confirmed_speech_or_a_tap_interrupts(self):
        async def run():
            _, _, _, connection = build([])
            await connection.control({"type": "speech_start"})
            self.assertEqual((connection.generation, connection.capture_id), (0, 1))
            await connection.control({"type": "interrupt"})
            self.assertEqual(connection.generation, 1)

        asyncio.run(run())

    def test_announcement_waits_while_the_owner_is_talking(self):
        async def run():
            _, agent, queue, connection = build(
                [Decision("tars", "On it.", "Weather"), Decision("self", "Sunny.")],
                {"Weather": ["Sunny."]},
            )
            await connection.submit_text("Weather?")
            await until(lambda: connection.turn_task is None)
            connection.recording = True
            queue.start()
            await until(lambda: queue.tasks[0].finished)
            await asyncio.sleep(0.02)
            self.assertEqual(len(agent.calls), 1)
            connection.recording = False
            connection.maybe_announce()
            await until(lambda: len(agent.calls) == 2 and connection.turn_task is None)
            await queue.stop()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
