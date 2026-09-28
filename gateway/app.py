from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import webrtcvad
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .access import CloudflareAccessVerifier
from .agent import DEFAULT_HANDOFF_SAY, AgentError, Decision, VoiceAgent
from .handoff import HandoffQueue, Task
from .observability import Diagnostics
from .protocol import SentenceBuffer, first_sentences, safe_status, speakable_text
from .relay import HttpTarsRelay
from .settings import ROOT, Settings
from .stt import WhisperSTT
from .tts import PiperTTS

STATIC = ROOT / "static"
ACTIVE_CLIENT = asyncio.Lock()
ACCESS_HEADER = "cf-access-jwt-assertion"
CLIENT_EVENTS = {
    "app_loaded",
    "websocket_open",
    "websocket_close",
    "websocket_error",
    "microphone_ready",
    "microphone_denied",
    "handsfree_enabled",
    "handsfree_disabled",
    "audio_playback_failed",
    "service_worker_ready",
    "service_worker_failed",
}


class ClientDiagnostic(BaseModel):
    event: str = Field(min_length=1, max_length=64)
    code: int | None = Field(default=None, ge=0, le=65535)


async def tcp_probe(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.0)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, TimeoutError):
        return False


async def readiness(app: FastAPI) -> dict[str, Any]:
    settings: Settings = app.state.settings
    probes = [tcp_probe(settings.whisper_url), tcp_probe(settings.relay_url)]
    if settings.agent_url:
        probes.append(tcp_probe(settings.agent_url))
    results = await asyncio.gather(*probes)
    components = {
        "gateway": True,
        "tts": app.state.tts.loaded,
        "whisper": results[0],
        "relay": results[1],
    }
    if settings.agent_url:
        components["agent"] = results[2]
    return {"ready": all(components.values()), "components": components, "activeClient": ACTIVE_CLIENT.locked()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    settings.validate()
    app.state.settings = settings
    app.state.diagnostics = Diagnostics()
    app.state.diagnostics.record("gateway_starting")
    app.state.access = CloudflareAccessVerifier(
        settings.access_team_domain,
        settings.access_audience,
        settings.access_email,
    )
    app.state.relay = HttpTarsRelay(settings.relay_url, settings.relay_token)
    app.state.stt = WhisperSTT(settings.whisper_url)
    app.state.tts = PiperTTS(settings.piper_model)
    app.state.handoffs = None
    app.state.agent_factory = None
    if settings.agent_url:
        # Tars may run tools for minutes without sending a byte, so handoffs get a longer timeout.
        handoff_relay = HttpTarsRelay(settings.relay_url, settings.relay_token, timeout=settings.handoff_timeout)
        app.state.handoffs = HandoffQueue(handoff_relay)
        app.state.handoffs.start()
        app.state.agent_factory = lambda: VoiceAgent(settings.agent_url, settings.owner_name, settings.tars_home)
    started = time.monotonic()
    await app.state.tts.load()
    app.state.diagnostics.record("tts_loaded", latencyMs=round((time.monotonic() - started) * 1000))
    app.state.diagnostics.record("gateway_ready", agent=bool(settings.agent_url))
    try:
        yield
    finally:
        if app.state.handoffs:
            await app.state.handoffs.stop()
        app.state.diagnostics.record("gateway_stopping")


app = FastAPI(title="Tars Voice", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    settings: Settings = request.app.state.settings
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    if host not in settings.allowed_hosts:
        request.app.state.diagnostics.record("http_rejected", level="warning", reason="invalid_host")
        return JSONResponse({"error": "invalid host"}, status_code=400)
    if host == settings.public_host:
        identity = await asyncio.to_thread(request.app.state.access.verify, request.headers.get(ACCESS_HEADER))
        if identity is None:
            request.app.state.diagnostics.record("http_rejected", level="warning", reason="invalid_access", path=request.url.path)
            return JSONResponse({"error": "Cloudflare Access authentication required"}, status_code=403)
    response = await call_next(request)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; connect-src 'self' wss:; media-src 'self' blob:; style-src 'self'; script-src 'self'; worker-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
        }
    )
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "production"}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    state = await readiness(request.app)
    return JSONResponse(state, status_code=200 if state["ready"] else 503)


@app.get("/api/diagnostics")
async def diagnostics(request: Request) -> dict[str, Any]:
    return {
        "readiness": await readiness(request.app),
        "runtime": request.app.state.diagnostics.snapshot(),
    }


@app.post("/api/diagnostics/client")
async def client_diagnostic(request: Request, data: ClientDiagnostic) -> dict[str, bool]:
    if data.event not in CLIENT_EVENTS:
        return {"accepted": False}
    request.app.state.diagnostics.record("client_event", clientEvent=data.event, code=data.code)
    return {"accepted": True}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/app.js")
@app.get("/app.css")
@app.get("/audio-worklet.js")
@app.get("/manifest.webmanifest")
@app.get("/sw.js")
@app.get("/icon-192.png")
@app.get("/icon-512.png")
async def static_asset(request: Request):
    return FileResponse(STATIC / request.url.path.removeprefix("/"))


def has_speech(pcm: bytes) -> bool:
    vad = webrtcvad.Vad(2)
    frame_bytes = 960  # 30 ms of 16 kHz, mono, signed 16-bit PCM
    frames = [pcm[i:i + frame_bytes] for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes)]
    return sum(vad.is_speech(frame, 16000) for frame in frames) >= 3


class VoiceConnection:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.settings: Settings = ws.app.state.settings
        self.relay: HttpTarsRelay = ws.app.state.relay
        self.stt: WhisperSTT = ws.app.state.stt
        self.tts: PiperTTS = ws.app.state.tts
        self.diag: Diagnostics = ws.app.state.diagnostics
        self.session_id = secrets.token_hex(16)
        self.log_id = self.session_id[:8]
        self.send_lock = asyncio.Lock()
        self.recording = False
        self.audio = bytearray()
        self.transcribe_task: asyncio.Task[None] | None = None
        self.partial_task: asyncio.Task[None] | None = None
        self.next_partial_bytes = 28_800  # first interim result after about 0.9 seconds
        self.turn_task: asyncio.Task[None] | None = None
        self.pending_text: str | None = None
        self.generation = 0  # bumped on confirmed speech or a tap-to-stop; drops older audio
        self.capture_id = 0  # bumped on every capture; drops stale partial transcripts
        self.closed = False
        self.quiet = False
        self.handoffs: HandoffQueue | None = ws.app.state.handoffs
        factory = ws.app.state.agent_factory
        self.agent: VoiceAgent | None = factory() if factory and self.handoffs else None
        self.transcript: list[str] = []
        self.call_task: asyncio.Task[None] | None = None

    async def send(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.ws.send_json(payload)

    async def send_audio(self, turn_id: str, audio: bytes) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.ws.send_json({"type": "audio", "turnId": turn_id, "mime": "audio/wav", "bytes": len(audio)})
            await self.ws.send_bytes(audio)

    async def receive(self) -> None:
        self.diag.record("websocket_connected", connectionId=self.log_id)
        await self.send({"type": "ready", "mode": "single-session", "agent": self.agent is not None})
        if self.agent and self.handoffs:
            self.handoffs.subscribe(self.on_task)
            await self.send({"type": "tasks", "tasks": [task.public() for task in self.handoffs.recent()]})
            self.call_task = asyncio.create_task(self.start_call())
        try:
            while True:
                incoming = await self.ws.receive()
                if incoming["type"] == "websocket.disconnect":
                    break
                if incoming.get("bytes") is not None:
                    await self.audio_frame(incoming["bytes"])
                    continue
                try:
                    message = json.loads(incoming.get("text") or "{}")
                except json.JSONDecodeError:
                    await self.send({"type": "error", "message": "Invalid control message."})
                    continue
                await self.control(message)
        except WebSocketDisconnect:
            pass
        finally:
            self.closed = True
            if self.handoffs:
                self.handoffs.unsubscribe(self.on_task)
            self.diag.record("websocket_disconnected", connectionId=self.log_id)
            for task in (self.transcribe_task, self.partial_task, self.turn_task, self.call_task):
                if task and not task.done():
                    task.cancel()

    async def control(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "speech_start":
            # Noise can start a capture too, so this must not drop the current answer.
            self.recording = True
            self.audio.clear()
            self.next_partial_bytes = 28_800
            self.capture_id += 1
            self.diag.record("capture_started", connectionId=self.log_id)
            await self.send({"type": "listening"})
        elif kind == "speech_end":
            if not self.recording:
                return
            self.recording = False
            pcm = bytes(self.audio)
            self.audio.clear()
            self.diag.record(
                "capture_completed",
                connectionId=self.log_id,
                audioBytes=len(pcm),
                durationMs=round(len(pcm) / 32),
            )
            if self.transcribe_task and not self.transcribe_task.done():
                self.diag.record("capture_rejected", level="warning", connectionId=self.log_id, reason="transcription_busy")
                await self.send({"type": "error", "message": "Still transcribing the previous utterance."})
                return
            self.transcribe_task = asyncio.create_task(self.transcribe(pcm))
        elif kind == "text_turn" and isinstance(message.get("text"), str):
            await self.submit_text(" ".join(message["text"].split())[:8000])
        elif kind == "interrupt":
            self.generation += 1
        elif kind == "quiet":
            self.quiet = bool(message.get("on"))
        elif kind == "call_start":
            self.call_task = asyncio.create_task(self.start_call())
        elif kind == "ping":
            await self.send({"type": "pong"})
        else:
            await self.send({"type": "error", "message": "Unsupported control message."})

    async def audio_frame(self, frame: bytes) -> None:
        if not self.recording:
            return
        maximum = self.settings.max_audio_seconds * 16000 * 2
        if len(frame) > 65536 or len(self.audio) + len(frame) > maximum:
            self.recording = False
            self.audio.clear()
            self.diag.record("capture_rejected", level="warning", connectionId=self.log_id, reason="audio_limit")
            await self.send({"type": "error", "message": "The utterance was too long."})
            return
        self.audio.extend(frame)
        if (
            len(self.audio) >= self.next_partial_bytes
            and (self.partial_task is None or self.partial_task.done())
        ):
            self.next_partial_bytes = len(self.audio) + 38_400  # then about every 1.2 seconds
            self.partial_task = asyncio.create_task(
                self.transcribe_partial(bytes(self.audio), self.capture_id)
            )

    async def transcribe_partial(self, pcm: bytes, capture_id: int) -> None:
        if len(pcm) < 19_200 or not has_speech(pcm):
            return
        started = time.monotonic()
        try:
            text = await self.stt.transcribe_pcm(pcm)
        except (ValueError, RuntimeError):
            return
        if self.recording and capture_id == self.capture_id:
            self.diag.record(
                "stt_partial",
                connectionId=self.log_id,
                latencyMs=round((time.monotonic() - started) * 1000),
                characters=len(text),
            )
            await self.send({"type": "partial_transcript", "text": text})

    async def transcribe(self, pcm: bytes) -> None:
        partial = self.partial_task
        if partial and not partial.done():
            try:
                await partial
            except asyncio.CancelledError:
                return
        if len(pcm) < 6400 or not has_speech(pcm):
            self.diag.record("vad_rejected", connectionId=self.log_id, audioBytes=len(pcm))
            await self.send({"type": "error", "message": "No clear speech was detected."})
            return
        await self.send({"type": "status", "safeLabel": "Transcribing"})
        started = time.monotonic()
        self.diag.record("stt_started", connectionId=self.log_id, audioBytes=len(pcm))
        try:
            text = await self.stt.transcribe_pcm(pcm)
        except ValueError:
            self.diag.record("stt_failed", level="warning", connectionId=self.log_id, reason="empty_result", latencyMs=round((time.monotonic() - started) * 1000))
            await self.send({"type": "error", "message": "No speech was recognized."})
            return
        except Exception:
            self.diag.record("stt_failed", level="error", connectionId=self.log_id, reason="service_error", latencyMs=round((time.monotonic() - started) * 1000))
            await self.send({"type": "error", "message": "Speech recognition is unavailable."})
            return
        self.diag.record("stt_completed", connectionId=self.log_id, latencyMs=round((time.monotonic() - started) * 1000), characters=len(text))
        self.generation += 1  # confirmed speech interrupts whatever was still being said
        await self.send({"type": "transcript", "text": text})
        await self.submit_text(text)

    async def submit_text(self, text: str) -> None:
        if not text:
            return
        if self.turn_task and not self.turn_task.done():
            if self.pending_text is None:
                self.pending_text = text
                self.diag.record("turn_queued", connectionId=self.log_id)
                await self.send({"type": "queued", "safeLabel": "Follow-up queued"})
            else:
                self.diag.record("turn_rejected", level="warning", connectionId=self.log_id, reason="queue_full")
                await self.send({"type": "error", "message": "One follow-up is already queued."})
            return
        self.turn_task = asyncio.create_task(self.run_turn(text))

    async def run_turn(self, text: str, result: Task | None = None) -> None:
        try:
            if self.agent and self.handoffs:
                await self.run_agent_turn(text, result)
            else:
                await self.run_relay_turn(text)
        finally:
            current = asyncio.current_task()
            if self.turn_task is current:
                self.turn_task = None
            pending, self.pending_text = self.pending_text, None
            if pending and not self.closed:
                self.turn_task = asyncio.create_task(self.run_turn(pending))
            else:
                self.maybe_announce()

    async def start_call(self) -> None:
        if not self.agent:
            return
        self.agent.start_call()
        self.transcript = []
        await self.agent.warm()
        self.maybe_announce()

    async def on_task(self, task: Task) -> None:
        await self.send({"type": "task", "task": task.public()})
        if task.finished:
            self.diag.record("handoff_finished", connectionId=self.log_id, taskId=task.id, status=task.status, seconds=task.public()["seconds"])
            self.maybe_announce()

    def maybe_announce(self) -> None:
        """Tell the owner about a finished background task at the next pause, never over them."""
        busy = (self.turn_task and not self.turn_task.done()) or (self.transcribe_task and not self.transcribe_task.done())
        if self.closed or not self.agent or not self.handoffs or self.recording or busy:
            return
        task = next((t for t in self.handoffs.tasks if t.finished and not t.announced), None)
        if task is None:
            return
        task.announced = True
        note = f"(Background task #{task.id} just finished. Tell {self.settings.owner_name} the result now.)"
        self.turn_task = asyncio.create_task(self.run_turn(note, result=task))

    async def run_agent_turn(self, text: str, result: Task | None) -> None:
        assert self.agent and self.handoffs
        turn_id = secrets.token_hex(12)
        log_turn_id = turn_id[:8]
        generation = self.generation
        started = time.monotonic()
        self.diag.record("turn_started", connectionId=self.log_id, turnId=log_turn_id, inputCharacters=len(text), kind="result" if result else "agent")
        if result is None:
            self.transcript.append(f"{self.settings.owner_name}: {text}")
            await self.send({"type": "status", "turnId": turn_id, "safeLabel": "Thinking"})
        try:
            try:
                decision = await self.agent.decide(text, self.handoffs.ledger(), note=result is not None)
                self.diag.record("agent_decided", connectionId=self.log_id, turnId=log_turn_id, route=decision.route, latencyMs=round((time.monotonic() - started) * 1000))
            except AgentError:
                self.diag.record("agent_failed", level="warning", connectionId=self.log_id, turnId=log_turn_id)
                decision = Decision("self", first_sentences(result.answer)) if result else Decision("tars", DEFAULT_HANDOFF_SAY, text)
            if decision.route == "tars" and result is None:
                task = await self.handoffs.submit(decision.request)
                self.diag.record("handoff_submitted", connectionId=self.log_id, turnId=log_turn_id, taskId=task.id)
            if decision.say:
                self.transcript.append(f"Tars: {decision.say}")
                await self.send({"type": "answer", "turnId": turn_id, "text": decision.say, "taskId": result.id if result else None})
                sentences = SentenceBuffer()
                for sentence in [*sentences.push(decision.say), sentences.flush()]:
                    await self.speak(turn_id, generation, sentence)
            self.diag.record("turn_completed", connectionId=self.log_id, turnId=log_turn_id, latencyMs=round((time.monotonic() - started) * 1000), answerCharacters=len(decision.say))
        except asyncio.CancelledError:
            self.diag.record("turn_cancelled", level="warning", connectionId=self.log_id, turnId=log_turn_id)
            raise
        finally:
            if not self.closed:
                await self.send({"type": "done", "turnId": turn_id})

    async def run_relay_turn(self, text: str) -> None:
        turn_id = secrets.token_hex(12)
        log_turn_id = turn_id[:8]
        turn_generation = self.generation
        sentences = SentenceBuffer()
        answer_chars = 0
        completed = False
        first_answer_seen = False
        started = time.monotonic()
        self.diag.record("turn_started", connectionId=self.log_id, turnId=log_turn_id, inputCharacters=len(text))
        try:
            async for event in self.relay.run(text, self.session_id):
                if event.type in {"accepted", "thinking", "status"}:
                    await self.send({"type": "status", "turnId": turn_id, "safeLabel": safe_status(event.type)})
                elif event.type == "answer":
                    if not first_answer_seen:
                        first_answer_seen = True
                        self.diag.record("relay_first_answer", connectionId=self.log_id, turnId=log_turn_id, latencyMs=round((time.monotonic() - started) * 1000))
                    answer_chars += len(event.text)
                    await self.send({"type": "answer", "turnId": turn_id, "text": event.text})
                    for sentence in sentences.push(event.text):
                        await self.speak(turn_id, turn_generation, sentence)
                elif event.type == "error":
                    self.diag.record("relay_reported_error", level="warning", connectionId=self.log_id, turnId=log_turn_id)
                    await self.send({"type": "error", "turnId": turn_id, "message": "Tars could not complete that request."})
                elif event.type == "done":
                    remainder = sentences.flush()
                    if remainder:
                        await self.speak(turn_id, turn_generation, remainder)
                    completed = True
                    self.diag.record("turn_completed", connectionId=self.log_id, turnId=log_turn_id, latencyMs=round((time.monotonic() - started) * 1000), answerCharacters=answer_chars)
                    await self.send({"type": "done", "turnId": turn_id})
        except asyncio.CancelledError:
            self.diag.record("turn_cancelled", level="warning", connectionId=self.log_id, turnId=log_turn_id)
            raise
        except Exception:
            self.diag.record("relay_failed", level="error", connectionId=self.log_id, turnId=log_turn_id, latencyMs=round((time.monotonic() - started) * 1000))
            await self.send({"type": "error", "turnId": turn_id, "message": "Tars is unavailable."})
        finally:
            if not completed and not self.closed:
                await self.send({"type": "done", "turnId": turn_id})

    async def speak(self, turn_id: str, generation: int, sentence: str) -> None:
        text = speakable_text(sentence)
        if not text or self.quiet or generation != self.generation:
            return
        started = time.monotonic()
        try:
            audio = await self.tts.synthesize(text)
        except Exception:
            self.diag.record("tts_failed", level="error", connectionId=self.log_id, turnId=turn_id[:8])
            await self.send({"type": "error", "turnId": turn_id, "message": "Speech synthesis is unavailable."})
            return
        self.diag.record("tts_completed", connectionId=self.log_id, turnId=turn_id[:8], latencyMs=round((time.monotonic() - started) * 1000), inputCharacters=len(text), audioBytes=len(audio))
        if generation == self.generation:
            await self.send({"type": "speakable", "turnId": turn_id, "text": text})
            await self.send_audio(turn_id, audio)


@app.websocket("/ws")
async def voice_socket(ws: WebSocket) -> None:
    settings: Settings = ws.app.state.settings
    origin = ws.headers.get("origin")
    if origin != settings.public_origin:
        ws.app.state.diagnostics.record("websocket_rejected", level="warning", reason="invalid_origin")
        await ws.close(code=4401)
        return
    identity = await asyncio.to_thread(ws.app.state.access.verify, ws.headers.get(ACCESS_HEADER))
    if identity is None:
        ws.app.state.diagnostics.record("websocket_rejected", level="warning", reason="invalid_access")
        await ws.close(code=4401)
        return
    if ACTIVE_CLIENT.locked():
        ws.app.state.diagnostics.record("websocket_rejected", level="warning", reason="client_busy")
        await ws.close(code=4429)
        return
    async with ACTIVE_CLIENT:
        await ws.accept()
        await VoiceConnection(ws).receive()
