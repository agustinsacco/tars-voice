"""Client for the persistent loopback whisper.cpp server."""
from __future__ import annotations

import asyncio
import io
import json
import urllib.request
import uuid
import wave


class WhisperSTT:
    def __init__(self, url: str, timeout: int = 60):
        self.url = url
        self.timeout = timeout
        self.lock = asyncio.Lock()

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if not pcm or len(pcm) % 2:
            raise ValueError("Invalid PCM audio")
        wav = io.BytesIO()
        with wave.open(wav, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm)
        async with self.lock:
            return await asyncio.to_thread(self._request, wav.getvalue())

    def _request(self, wav: bytes) -> str:
        boundary = "----tarsvoice" + uuid.uuid4().hex
        body = bytearray()

        def field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

        field("response_format", "json")
        field("language", "en")
        field("temperature", "0")
        body.extend(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        )
        body.extend(wav)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            self.url,
            bytes(body),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except Exception as error:
            raise RuntimeError("Speech recognition service is unavailable") from error
        text = " ".join(str(payload.get("text", "")).split())
        if not text:
            raise ValueError("No speech was recognized")
        return text[:8000]
