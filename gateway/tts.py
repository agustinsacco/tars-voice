"""Persistent local Piper synthesizer."""
from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path

from piper.voice import PiperVoice


class PiperTTS:
    def __init__(self, model: Path):
        self.model = model
        self._voice: PiperVoice | None = None
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._voice is not None

    async def load(self) -> None:
        if self._voice is None:
            self._voice = await asyncio.to_thread(PiperVoice.load, self.model)

    async def synthesize(self, text: str) -> bytes:
        async with self._lock:
            await self.load()
            return await asyncio.to_thread(self._synthesize, text)

    def _synthesize(self, text: str) -> bytes:
        if self._voice is None:
            raise RuntimeError("Piper is not loaded")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            self._voice.synthesize_wav(text, wav_file)
        return output.getvalue()
