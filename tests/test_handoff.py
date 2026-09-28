import asyncio
import unittest

from gateway.handoff import HandoffQueue
from gateway.relay import RelayEvent

BUSY = "❌ **Supervisor Error:** I'm currently working on a task. Please retry in a moment."


class ScriptedRelay:
    """Answers each request from a script; records the order requests arrive in."""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def run(self, text, session_id):
        self.calls.append((text, session_id))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            outcome = self.script[text].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            yield RelayEvent("accepted")
            if outcome is not None:
                yield RelayEvent("answer", outcome)
            else:
                yield RelayEvent("error")
            yield RelayEvent("done")
        finally:
            self.active -= 1


async def settle(queue, count):
    for _ in range(400):
        if sum(t.finished for t in queue.tasks) >= count:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("queue did not settle")


class HandoffTests(unittest.TestCase):
    def run_queue(self, script, requests, **options):
        async def run():
            relay = ScriptedRelay(script)
            queue = HandoffQueue(relay, busy_retry_seconds=0.001, **options)
            seen = []

            async def listener(task):
                seen.append((task.id, task.status))

            queue.subscribe(listener)
            queue.start()
            for request in requests:
                await queue.submit(request)
            await settle(queue, len(queue.tasks))
            await queue.stop()
            return relay, queue, seen

        return asyncio.run(run())

    def test_single_flight_in_order_with_busy_retry(self):
        relay, queue, seen = self.run_queue(
            {"weather": [BUSY, "Sunny, high 20."], "dune": ["Frank Herbert."]},
            ["weather", "dune"],
        )
        self.assertEqual(relay.max_active, 1)
        self.assertEqual([c[0] for c in relay.calls], ["weather", "weather", "dune"])
        self.assertEqual({c[1] for c in relay.calls}, {"tars-voice"})
        self.assertEqual([(t.status, t.answer) for t in queue.tasks], [("done", "Sunny, high 20."), ("done", "Frank Herbert.")])
        weather = [status for task_id, status in seen if task_id == 1]
        self.assertEqual(weather, ["queued", "working", "waiting", "done"])

    def test_failures_are_reported_not_raised(self):
        _, queue, _ = self.run_queue(
            {"a": [ConnectionError()], "b": [None], "c": ["❌ **Supervisor Error:** model overloaded"]},
            ["a", "b", "c"],
        )
        self.assertEqual([t.status for t in queue.tasks], ["failed", "failed", "failed"])
        self.assertTrue(all(t.finished_at for t in queue.tasks))
        self.assertNotIn("overloaded", queue.tasks[2].answer)

    def test_gives_up_when_tars_stays_busy(self):
        _, queue, _ = self.run_queue({"a": [BUSY, BUSY]}, ["a"], busy_max_attempts=2)
        self.assertEqual(queue.tasks[0].status, "failed")

    def test_identical_open_request_is_not_queued_twice(self):
        relay, queue, _ = self.run_queue({"Weather": ["Sunny."]}, ["Weather", " weather "])
        self.assertEqual(len(queue.tasks), 1)
        self.assertEqual(len(relay.calls), 1)

    def test_ledger_lists_status_and_answer(self):
        _, queue, _ = self.run_queue({"weather": ["Sunny."]}, ["weather"])
        ledger = queue.ledger()
        self.assertTrue(ledger.startswith("BACKGROUND TASKS\n- #1 done"))
        self.assertIn('"weather" -> "Sunny."', ledger)
        public = queue.tasks[0].public()
        self.assertEqual((public["id"], public["status"], public["answer"]), (1, "done", "Sunny."))

    def test_empty_ledger(self):
        self.assertEqual(HandoffQueue(ScriptedRelay({})).ledger(), "BACKGROUND TASKS\n(none)")


if __name__ == "__main__":
    unittest.main()
