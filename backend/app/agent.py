"""The receptionist agent: one caller utterance in, one spoken reply out.

Safety design (not left to the model):
  * Availability only comes from check_availability; bookings are re-validated against the calendar when made.
  * book / reschedule / cancel are read back and need the caller's "yes"; the spoken outcome is composed from the tool result.
  * A tool outage ends in the fixed hand-off message. Reply text that claims a change nothing confirmed is blocked.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from app import timeutil
from app.booking import Booking, BookingError
from app.business import Business
from app.config import Settings
from app.db import Database
from app.llm import LLMMessage, LLMProvider, ToolSchema
from app.notify import Notifier
from app.state import SessionState
from app.tools import TOOLS, ToolContext, ToolError

log = logging.getLogger("receptionist")
TOOL_FAILURE = "I’m having trouble accessing the appointment system. Let me connect you with a human receptionist."
LLM_FAILURE = "I'm having trouble right now. Let me connect you with a human receptionist."
HANDOFF_REPLY = "Of course. I'll connect you with a human receptionist now."
FRUSTRATED_REPLY = "I'm sorry about that. Let me connect you with a human receptionist."
UNRESOLVED_REPLY = "I'm sorry, I'm having trouble with this request. Let me connect you with a human receptionist."
UNVERIFIED_REPLY = "I can't confirm that yet. Let me check the details with you again."

HUMAN_RE = re.compile(r"\b(human|real person|live person|operator|representative|(speak|talk|transfer|connect)\b[^.?!]{0,25}\b(person|someone|somebody|agent|receptionist|staff))\b", re.I)
FRUSTRATED_RE = re.compile(r"\b(useless|not helping|waste of (my )?time|ridiculous|stupid|terrible|frustrat\w+|(you('re| are) )?not listening|you don'?t understand)\b", re.I)
# Backstop: wording that tells the caller THEIR appointment was changed. Only tool results may say this, so it is blocked in model text.
CLAIM_RE = re.compile(r"(\byour (appointment|booking|visit|consultation|check-?up)\b[^.?!]{0,60}\b(is|has been|was|are)\b[^.?!]{0,20}\b(booked|scheduled|rescheduled|cancel+ed|confirmed)\b"
                      r"|\byou(?:'re| are| have been)\b[^.?!]{0,15}\b(booked|scheduled|all set)\b"
                      r"|\bI(?:'ve| have)\s+(booked|scheduled|rescheduled|cancel+ed|confirmed)\b"
                      r"|\b(has|have) been (booked|scheduled|rescheduled|cancel+ed)\b)", re.I)
INVALID_CODES = {"missing_argument", "invalid_argument", "invalid_date", "invalid_time", "invalid_phone", "unknown_tool"}
AFFIRM = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "confirm", "right", "please", "go", "ahead", "do", "it"}
NEGATE = {"no", "nope", "don't", "dont", "not", "cancel", "stop", "wait", "never", "mind"}
CONFIRMATION_KIND = {"book_appointment": "booked", "reschedule_appointment": "rescheduled", "cancel_appointment": "cancelled"}

PROMPT = """You are the AI phone receptionist for {name}. You talk with callers by voice: reply in one or two short, natural sentences with no lists, markdown or emojis.
BUSINESS HOURS: {hours}
Rules:
1. Never say a time is available unless check_availability just returned it. Never invent times, prices or policies. Answer business questions only from get_business_information; if it does not contain the answer, say you are not sure and offer a human receptionist.
2. When the caller gives any detail (intent, name, phone number, service, date, time) call update_session immediately. Combine details across turns using SESSION STATE: if the date is already known and the caller now gives only a time, keep the date.
3. Work out relative dates ("tomorrow", "next Friday") from TODAY. Tool arguments use YYYY-MM-DD dates and 24-hour HH:MM times. For a time like "around 4" choose the sensible time within business hours (16:00).
4. To book: get the phone number and call get_customer; if the caller is not found, ask their name and call create_customer. Also get the service, date and time, then call check_availability. If the preferred time is open call book_appointment; otherwise offer the nearest open times from the tool result.
5. To reschedule or cancel: identify the caller with get_customer, which lists their upcoming appointments with ids, then call reschedule_appointment or cancel_appointment.
6. book_appointment, reschedule_appointment and cancel_appointment do not finish immediately: the system reads the details back, asks the caller to confirm, completes the action and announces the result itself. Never tell the caller that an appointment is booked, changed or cancelled.
7. Call transfer_to_human when the caller asks for a person, is frustrated, has a sensitive or complex situation, or you cannot resolve the request.
8. Ask for one missing detail at a time.
TODAY: {today} ({weekday}), local time {time}, timezone {tz}
SERVICES: {services}
SESSION STATE: {state}"""


@dataclass
class ToolRecord:
    name: str
    arguments: dict
    result: dict | None
    status: str          # ok | error | failed | pending_confirmation | blocked
    duration_ms: int = 0


@dataclass
class AgentResult:
    reply: str | None
    records: list[ToolRecord] = field(default_factory=list)
    handoff_reason: str | None = None


def classify_yes_no(text: str) -> str | None:
    tokens = re.findall(r"[a-z']+|\d+", text.lower())
    if not tokens or len(tokens) > 3 or any(t.isdigit() for t in tokens):
        return None                       # longer or numeric replies ("yes but make it 4") are new requests
    if any(t in NEGATE for t in tokens):
        return "no"
    return "yes" if all(t in AFFIRM for t in tokens) else None


def clean_spoken(text: str | None) -> str:
    t = re.sub(r"https?://\S+", "", text or "")
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>]+", "", t)).strip()


class Receptionist:
    def __init__(self, llm: LLMProvider, booking: Booking, business: Business, db: Database, notifier: Notifier, settings: Settings):
        self.llm, self.booking, self.business, self.db, self.notifier, self.s = llm, booking, business, db, notifier, settings
        self.tools = {t.name: t for t in TOOLS}

    # ------------------------------------------------------------------ entry point
    async def turn(self, state: SessionState, text: str) -> AgentResult:
        if state.handoff_requested:
            return AgentResult(None)                     # a human owns the call now
        ctx, records = ToolContext(state, self.booking, self.business, self.db, self.notifier), []
        result = await self._decide(ctx, text, records)
        if result.reply:
            state.conversation_history += [{"role": "caller", "text": text}, {"role": "agent", "text": result.reply}]
        return result

    async def _decide(self, ctx: ToolContext, text: str, records: list[ToolRecord]) -> AgentResult:
        state = ctx.state
        low = text.lower()
        for kw in self.business.escalation_keywords:
            if kw in low:
                return self._handoff(state, records, self.business.escalation_message, f"sensitive situation ({kw})")
        if HUMAN_RE.search(text):
            return self._handoff(state, records, HANDOFF_REPLY, "caller asked for a human")
        if FRUSTRATED_RE.search(text):
            return self._handoff(state, records, FRUSTRATED_REPLY, "caller is frustrated")
        if state.pending:
            pending, state.pending = state.pending, None           # a read-back is answered once
            answer = classify_yes_no(text)
            if answer == "no":
                return AgentResult("Okay, I won't do that. What would you like to change?", records)
            if answer == "yes":
                return await self._execute(ctx, pending["tool"], pending["args"], records)
        return await self._llm_loop(ctx, text, records)

    # ------------------------------------------------------------------ model loop
    def _prompt(self, state: SessionState) -> str:
        now = timeutil.utcnow().astimezone(self.business.tz)
        return PROMPT.format(name=self.business.name, hours=self.business.hours_text(), today=now.date().isoformat(), weekday=f"{now:%A}",
                             time=f"{now:%H:%M}", tz=self.business.timezone, services=json.dumps([s.name for s in self.business.services]),
                             state=json.dumps(state.public()))

    async def _llm_loop(self, ctx: ToolContext, text: str, records: list[ToolRecord]) -> AgentResult:
        state = ctx.state
        history = [LLMMessage("user" if m["role"] == "caller" else "assistant", m["text"]) for m in state.conversation_history[-self.s.history_messages:]]
        messages = [LLMMessage("system", self._prompt(state)), *history, LLMMessage("user", text)]
        schemas = [ToolSchema(t.name, t.description, t.parameters) for t in self.tools.values()]
        for _ in range(self.s.max_tool_steps):
            try:
                resp = await asyncio.wait_for(self.llm.chat(messages, schemas), self.s.provider_timeout_seconds)
            except Exception:  # noqa: BLE001 - any model failure must end in a human, not a crash
                log.exception("language model call failed")
                return self._handoff(state, records, LLM_FAILURE, "language model unavailable")
            if not resp.tool_calls:
                return self._finish(clean_spoken(resp.text), records)
            calls = resp.tool_calls[:4]
            messages.append(LLMMessage("assistant", resp.text, calls))
            for call in calls:
                payload, final = await self._call_tool(ctx, call.name, call.arguments, records)
                if final:
                    return final
                messages.append(LLMMessage("tool", json.dumps(payload, default=str), tool_call_id=call.id, name=call.name))
        return self._handoff(state, records, UNRESOLVED_REPLY, "agent could not resolve the request")

    def _finish(self, reply: str, records: list[ToolRecord]) -> AgentResult:
        if not reply:
            return AgentResult("Sorry, could you say that again?", records)
        lookup_ran = any(r.name == "get_customer" and r.status == "ok" for r in records)   # relaying a lookup result is legitimate
        if CLAIM_RE.search(reply) and not lookup_ran:
            records.append(ToolRecord("guardrail", {"blocked_reply": reply}, {"reason": "reply claimed an appointment change that no tool confirmed"}, "blocked"))
            return AgentResult(UNVERIFIED_REPLY, records)
        return AgentResult(reply, records)

    # ------------------------------------------------------------------ tools
    async def _call_tool(self, ctx: ToolContext, name: str, raw_args: dict, records: list[ToolRecord]) -> tuple[dict | None, AgentResult | None]:
        """Returns (payload for the model, None) to keep going, or (None, final result) when the turn ends here."""
        state, t0 = ctx.state, time.perf_counter()
        ms = lambda: int((time.perf_counter() - t0) * 1000)  # noqa: E731
        tool = self.tools.get(name)
        try:
            if tool is None:
                raise ToolError("unknown_tool", f"there is no tool called {name}")
            args = await tool.validate(ctx, raw_args)
            if tool.gated:
                final_args, summary = await tool.prepare(ctx, args)
                if not self.s.require_confirmation:
                    return None, await self._execute(ctx, name, final_args, records)
                state.pending = {"tool": name, "args": final_args, "summary": summary}
                records.append(ToolRecord(name, final_args, {"awaiting_confirmation": summary}, "pending_confirmation", ms()))
                return None, AgentResult(f"Shall I {summary}? Please say yes or no.", records)
            result = await tool.run(ctx, args)
        except (ToolError, BookingError) as e:
            payload = {"error": e.code, "message": e.message}
            records.append(ToolRecord(name, raw_args, payload, "error", ms()))
            if e.code in INVALID_CODES:
                state.failures += 1
                if state.failures >= 3:
                    return None, self._handoff(state, records, TOOL_FAILURE, "repeated tool errors")
            return payload, None
        except Exception as e:  # noqa: BLE001 - calendar/database outage
            return None, self._tool_failure(state, records, name, raw_args, e, ms())
        records.append(ToolRecord(name, args, result, "ok", ms()))
        if name == "transfer_to_human":
            return None, AgentResult(HANDOFF_REPLY, records, handoff_reason=state.handoff_reason)
        return result, None

    async def _execute(self, ctx: ToolContext, name: str, args: dict, records: list[ToolRecord]) -> AgentResult:
        """Run a confirmed gated tool and announce the outcome from its real result."""
        tool, t0 = self.tools[name], time.perf_counter()
        ms = lambda: int((time.perf_counter() - t0) * 1000)  # noqa: E731
        try:
            result = await tool.run(ctx, args)
        except BookingError as e:                                  # e.g. someone took the slot after the read-back
            records.append(ToolRecord(name, args, {"error": e.code, "message": e.message}, "error", ms()))
            return AgentResult(f"Sorry, {e.message}. Would you like to try a different time?", records)
        except Exception as e:  # noqa: BLE001
            return self._tool_failure(ctx.state, records, name, args, e, ms())
        records.append(ToolRecord(name, args, result, "ok", ms()))
        reply = tool.spoken_result(ctx, result)
        sent = await self._call_tool(ctx, "send_confirmation", {"appointment_id": result["appointment_id"], "kind": CONFIRMATION_KIND[name]}, records)
        if sent[0] and sent[0].get("delivered"):
            reply += " I've also sent you a confirmation text."
        if sent[1]:                                                # confirmation failed hard: the booking itself still stands
            return AgentResult(reply, [r for r in records if r.status != "failed"] + [r for r in records if r.status == "failed"])
        return AgentResult(reply, records)

    # ------------------------------------------------------------------ hand-off
    def _tool_failure(self, state: SessionState, records: list[ToolRecord], name: str, args: dict, exc: Exception, duration_ms: int) -> AgentResult:
        records.append(ToolRecord(name, args, {"error": type(exc).__name__, "message": str(exc)}, "failed", duration_ms))
        return self._handoff(state, records, TOOL_FAILURE, f"tool failure: {name}")

    @staticmethod
    def _handoff(state: SessionState, records: list[ToolRecord], reply: str, reason: str) -> AgentResult:
        state.handoff_requested, state.handoff_reason, state.pending = True, reason, None
        if not any(r.name == "transfer_to_human" for r in records):
            records.append(ToolRecord("transfer_to_human", {"reason": reason}, {"transferred": True}, "ok"))
        return AgentResult(reply, records, handoff_reason=reason)
