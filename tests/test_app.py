import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
MODEL = Path(TEMP.name) / "voice.onnx"
MODEL.touch()

os.environ["VOICE_RELAY_TOKEN"] = "r" * 32
os.environ["VOICE_PUBLIC_ORIGIN"] = "https://voice.example.com"
os.environ["VOICE_ACCESS_TEAM_DOMAIN"] = "example.cloudflareaccess.com"
os.environ["VOICE_ACCESS_AUD"] = "a" * 64
os.environ["VOICE_ACCESS_EMAIL"] = "owner@example.com"
os.environ["VOICE_PIPER_MODEL"] = str(MODEL)

from gateway.tts import PiperTTS


async def fake_tts_load(self):
    self._voice = object()


PiperTTS.load = fake_tts_load

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.access import AccessIdentity
from gateway.app import ACCESS_HEADER, app, has_speech


class FakeAccessVerifier:
    def verify(self, token):
        if token == "valid-access-token":
            return AccessIdentity(email="owner@example.com", subject="owner")
        return None


class AppTests(unittest.TestCase):
    def test_cloudflare_access_protects_http_and_websocket(self):
        with TestClient(app, base_url="https://voice.example.com") as client:
            app.state.access = FakeAccessVerifier()
            self.assertEqual(client.get("/").status_code, 403)
            response = client.get("/", headers={ACCESS_HEADER: "valid-access-token"})
            self.assertEqual(response.status_code, 200)

            with (
                self.assertRaises(WebSocketDisconnect),
                client.websocket_connect("/ws", headers={"origin": "https://voice.example.com"}),
            ):
                pass

            with client.websocket_connect(
                "/ws",
                headers={
                    "origin": "https://voice.example.com",
                    ACCESS_HEADER: "valid-access-token",
                },
            ) as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")

    def test_local_health_and_privacy_safe_diagnostics(self):
        with TestClient(app, base_url="http://127.0.0.1") as client:
            self.assertEqual(client.get("/healthz").json()["status"], "ok")
            self.assertTrue(client.post("/api/diagnostics/client", json={"event": "microphone_ready"}).json()["accepted"])
            self.assertFalse(client.post("/api/diagnostics/client", json={"event": "arbitrary_detail"}).json()["accepted"])
            report = client.get("/api/diagnostics").json()
            self.assertIn("components", report["readiness"])
            self.assertIn("recent", report["runtime"])
            serialized = str(report).lower()
            self.assertNotIn("transcript", serialized)
            self.assertNotIn("access-token", serialized)

    def test_vad_rejects_silence_and_accepts_voiced_frames(self):
        self.assertFalse(has_speech(b"\0" * 32000))
        with patch("gateway.app.webrtcvad.Vad") as vad_type:
            vad_type.return_value.is_speech.side_effect = [True, True, True, False]
            self.assertTrue(has_speech(b"\1\0" * 1920))


if __name__ == "__main__":
    unittest.main()
