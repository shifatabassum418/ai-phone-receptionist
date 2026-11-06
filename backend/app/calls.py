"""One live call: persists every message, tool call and state change, and streams events to the dashboard."""
import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from app import timeutil
from app.agent import LLM_FAILURE, AgentResult
from app.state import SessionState

if TYPE_CHECKING:
    from app.services import Services

log = logging.getLogger("receptionist")
UNCLEAR = "Sorry, I didn't catch that. Could you say it again?"
TOO_LONG = "That was a bit long. Could you say it in a shorter way?"
HEARING_TROUBLE = "I'm having trouble hearing you. Let me connect you with a human receptionist."
HUMAN_NOTICE = "A human receptionist has been requested. The AI will not reply any further."


class Emitter(Protocol):
    async def json(self, event: dict[str, Any]) -> None: ...
    async def bytes(self, data: bytes) -> None: ...


class CallSession:
    def __init__(self, svc: "Services", state: SessionState, registry: "CallRegistry"):
        self.svc, self.state, self.registry = svc, state, registry
        self.emitter: Emitter | None = None
        self.ended = False
        self._seq = 0
        self._interrupted = False
        self._stt_errors = 0
        self._unclear = 0
        self._notice_sent = False
        self._lock = asyncio.Lock()          # one turn at a time

    @property
    def id(self) -> str:
        return self.state.session_id

    # ------------------------------------------------------------------ output
    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emitter:
            await self.emitter.json(event)

    async def _log(self, role: str, text: str, **extra: Any) -> int:
        self._seq += 1
        now = timeutil.iso(timeutil.utcnow())
        await self.svc.db.insert("messages", session_id=self.id, seq=self._seq, role=role, content=text, created_at=now)
        await self._emit({"type": "message", "seq": self._seq, "role": role, "text": text, "at": now, **extra})
        return self._seq

    async def _say(self, text: str) -> None:
        clip, voice = None, "browser"
        try:
            clip = await asyncio.wait_for(self.svc.tts.synthesize(text), self.svc.settings.provider_timeout_seconds)
            voice = "server" if clip else "browser"
        except Exception as e:  # noqa: BLE001 - the caller still gets the text and the browser voice
            log.warning("tts failed: %s", e)
        await self._log("agent", text, voice=voice)
        if clip and not self._interrupted and self.emitter:
            await self._emit({"type": "audio", "format": clip.format, "bytes": len(clip.data)})
            await self.emitter.bytes(clip.data)

    async def _publish_state(self) -> None:
        s = self.state
        await self.svc.db.update("call_sessions", {"id": self.id}, state=s.public(), intent=s.intent, customer_id=s.customer_id,
                                 handoff_requested=int(s.handoff_requested), handoff_reason=s.handoff_reason)
        appointment = None
        if s.appointment_id and (appt := await self.svc.booking.get(s.appointment_id)):
            appointment = {"id": appt["id"], "service": appt["service"], "starts_at": appt["starts_at"], "status": appt["status"],
                           "when": timeutil.spoken(timeutil.parse_iso(appt["starts_at"]), self.svc.business.tz)}
        await self._emit({"type": "session.state", "state": s.public(), "appointment": appointment})

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        greeting = f"Thank you for calling {self.svc.business.name}. How can I help you today?"
        await self._emit({"type": "session.ready", "session_id": self.id, "components": self.svc.components(), "mock_mode": self.svc.mock_mode,
                          "sample_rate": self.svc.settings.sample_rate, "max_utterance_seconds": self.svc.settings.max_utterance_seconds})
        await self._say(greeting)
        self.state.conversation_history.append({"role": "agent", "text": greeting})
        await self._publish_state()

    async def end(self, reason: str) -> None:
        if self.ended:
            return
        self.ended = True
        status = "transferred" if self.state.handoff_requested else "ended"
        await self.svc.db.update("call_sessions", {"id": self.id}, status=status, ended_at=timeutil.iso(timeutil.utcnow()))
        await self._emit({"type": "call.ended", "reason": reason, "status": status})
        self.registry.drop(self.id)

    def interrupt(self) -> None:
        self._interrupted = True

    # ------------------------------------------------------------------ hand-off
    async def _mark_handoff(self, reason: str) -> None:
        await self.svc.db.update("call_sessions", {"id": self.id}, status="transferred", handoff_requested=1, handoff_reason=reason)
        await self._emit({"type": "handoff", "message": "Human handoff requested.", "reason": reason})

    async def request_handoff(self, reason: str) -> None:
        """Handoff triggered from outside the conversation (dashboard button or POST /api/handoff)."""
        async with self._lock:
            if self.state.handoff_requested:
                return
            self.state.handoff_requested, self.state.handoff_reason, self.state.pending = True, reason, None
            await self._mark_handoff(reason)
            await self._say("I'm connecting you with a human receptionist now.")
            await self._publish_state()

    # ------------------------------------------------------------------ input
    async def handle_text(self, text: str) -> None:
        text = text.strip()
        if not text or self.ended:
            return
        async with self._lock:
            self._interrupted = False
            turn = await self._log("caller", text)
            await self._emit({"type": "agent.status", "state": "thinking"})
            try:
                result = await self.svc.agent.turn(self.state, text)
            except Exception:  # noqa: BLE001
                log.exception("agent turn crashed")
                self.state.handoff_requested, self.state.handoff_reason = True, "internal error"
                result = AgentResult(LLM_FAILURE, [], "internal error")
            for rec in result.records:
                await self.svc.db.insert("tool_calls", id=uuid.uuid4().hex, session_id=self.id, turn=turn, name=rec.name, arguments=rec.arguments,
                                         result=rec.result, status=rec.status, duration_ms=rec.duration_ms, created_at=timeutil.iso(timeutil.utcnow()))
                await self._emit({"type": "tool.call", "turn": turn, "name": rec.name, "arguments": rec.arguments, "result": rec.result,
                                  "status": rec.status, "duration_ms": rec.duration_ms})
                log.info("tool %s %s %dms", rec.name, rec.status, rec.duration_ms)
            if result.reply:
                await self._say(result.reply)
            elif not self._notice_sent:
                self._notice_sent = True
                await self._log("system", HUMAN_NOTICE)
            if result.handoff_reason:
                await self._mark_handoff(result.handoff_reason)
            await self._publish_state()

    async def handle_audio(self, pcm: bytes) -> None:
        if self.ended or self.state.handoff_requested:
            return
        s = self.svc.settings
        if len(pcm) / 2 / s.sample_rate > s.max_utterance_seconds:
            async with self._lock:
                await self._say(TOO_LONG)
            return
        try:
            text = await asyncio.wait_for(self.svc.stt.transcribe(pcm, s.sample_rate), s.provider_timeout_seconds)
        except Exception as e:  # noqa: BLE001
            log.warning("stt failed: %s", e)
            self._stt_errors += 1
            await self._trouble(HEARING_TROUBLE if self._stt_errors >= 2 else UNCLEAR, "speech recognition unavailable", self._stt_errors >= 2)
            return
        if not text:
            self._unclear += 1
            await self._trouble(HEARING_TROUBLE if self._unclear >= 3 else UNCLEAR, "caller could not be understood", self._unclear >= 3)
            return
        self._stt_errors = self._unclear = 0
        await self.handle_text(text)

    async def _trouble(self, message: str, reason: str, handoff: bool) -> None:
        async with self._lock:
            await self._say(message)
            if handoff:
                self.state.handoff_requested, self.state.handoff_reason = True, reason
                await self._mark_handoff(reason)
            await self._publish_state()


class CallRegistry:
    def __init__(self) -> None:
        self._calls: dict[str, CallSession] = {}

    async def create(self, svc: "Services") -> CallSession:
        state = SessionState(session_id=uuid.uuid4().hex)
        await svc.db.insert("call_sessions", id=state.session_id, started_at=timeutil.iso(timeutil.utcnow()), status="active", components=svc.components(),
                            state=state.public())
        call = CallSession(svc, state, self)
        self._calls[state.session_id] = call
        return call

    def get(self, session_id: str) -> CallSession | None:
        return self._calls.get(session_id)

    def drop(self, session_id: str) -> None:
        self._calls.pop(session_id, None)
