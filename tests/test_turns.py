import asyncio
import unittest
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import patch

from gateway.app import VoiceConnection


class NoopDiagnostics:
    def record(self, *args, **kwargs):
        pass


class FakeWebSocket:
    def __init__(self):
        settings = SimpleNamespace(max_audio_seconds=30)
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                relay=None,
                stt=None,
                tts=None,
                diagnostics=NoopDiagnostics(),
            )
        )
        self.events = []

    async def send_json(self, payload):
        self.events.append(payload)

    async def send_bytes(self, payload):
        pass


class FakeSTT:
    async def transcribe_pcm(self, pcm):
        return "live partial words"


class TurnTests(unittest.TestCase):
    def test_partial_transcript_is_emitted_while_recording(self):
        async def run():
            ws = FakeWebSocket()
            connection = VoiceConnection(ws)
            connection.stt = FakeSTT()
            connection.recording = True
            connection.generation = 1
            pcm = b"\1\0" * 9600
            with patch("gateway.app.has_speech", return_value=True):
                await connection.transcribe_partial(pcm, 1)
            self.assertEqual(ws.events[-1], {"type": "partial_transcript", "text": "live partial words"})

        asyncio.run(run())

    def test_only_one_follow_up_is_queued(self):
        async def run():
            ws = FakeWebSocket()
            connection = VoiceConnection(ws)
            connection.turn_task = asyncio.create_task(asyncio.sleep(10))
            await connection.submit_text("first follow-up")
            await connection.submit_text("second follow-up")
            self.assertEqual(connection.pending_text, "first follow-up")
            self.assertEqual(ws.events[0]["type"], "queued")
            self.assertEqual(ws.events[1]["type"], "error")
            connection.turn_task.cancel()
            with suppress(asyncio.CancelledError):
                await connection.turn_task
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
