import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState

from app import timeutil
from app.booking import BookingError
from app.business import DAYS

router = APIRouter(prefix="/api")
log = logging.getLogger("receptionist")
STATUS_FOR_CODE = {"not_found": 404, "slot_unavailable": 409, "closed": 409, "not_bookable": 409}
DATE, TIME = r"^\d{4}-\d{2}-\d{2}$", r"^([01]\d|2[0-3]):[0-5]\d$"


class AppointmentIn(BaseModel):
    customer_name: str = Field(min_length=1, max_length=120)
    phone_number: str = Field(min_length=7, max_length=20)
    service: str
    date: str = Field(pattern=DATE, description="business-local YYYY-MM-DD")
    time: str = Field(pattern=TIME, description="business-local HH:MM")


class AppointmentPatch(BaseModel):
    date: str = Field(pattern=DATE)
    time: str = Field(pattern=TIME)


class HandoffIn(BaseModel):
    session_id: str
    reason: str = Field(default="requested from the dashboard", max_length=200)


def svc(request: Request):
    return request.app.state.services


def _day(value: str):
    try:
        return timeutil.parse_date(value)
    except ValueError as e:
        raise HTTPException(422, "date must be a real calendar date") from e


def _raise(e: BookingError):
    raise HTTPException(STATUS_FOR_CODE.get(e.code, 422), e.message) from e


# ------------------------------------------------------------------ health
@router.get("/health")
async def health(request: Request):
    s = svc(request)
    b = s.business
    return {"status": "ok", "mock_mode": s.mock_mode, "components": s.components(), "require_confirmation": s.settings.require_confirmation,
            "business": {"name": b.name, "phone": b.phone, "address": b.address, "timezone": b.timezone,
                         "hours": {d: b.hours[d] for d in DAYS}, "services": [{"name": x.name, "duration_min": x.duration_min} for x in b.services]}}


# ------------------------------------------------------------------ calls
@router.post("/calls/session", status_code=201)
async def create_session(request: Request):
    s = svc(request)
    call = await s.calls.create(s)
    return {"session_id": call.id, "stream_url": f"/api/calls/{call.id}/stream", "mock_mode": s.mock_mode, "components": s.components()}


@router.get("/calls")
async def list_calls(request: Request, limit: int = Query(100, ge=1, le=500), status: str | None = Query(None, pattern="^(active|ended|transferred)$")):
    where, params = ("WHERE c.status=?", [status, limit]) if status else ("", [limit])
    return await svc(request).db.raw(
        f"SELECT c.id, c.started_at, c.ended_at, c.status, c.intent, c.handoff_reason, cu.name AS customer_name, cu.phone AS customer_phone, "
        f"(SELECT COUNT(*) FROM messages m WHERE m.session_id=c.id) AS message_count, (SELECT COUNT(*) FROM tool_calls t WHERE t.session_id=c.id) AS tool_call_count, "
        f"(SELECT COUNT(*) FROM appointments a WHERE a.session_id=c.id AND a.status='booked') AS appointments_booked "
        f"FROM call_sessions c LEFT JOIN customers cu ON cu.id=c.customer_id {where} ORDER BY c.started_at DESC LIMIT ?", params)


@router.get("/calls/{call_id}")
async def get_call(call_id: str, request: Request):
    db = svc(request).db
    rows = await db.raw("SELECT c.*, cu.name AS customer_name, cu.phone AS customer_phone FROM call_sessions c LEFT JOIN customers cu ON cu.id=c.customer_id WHERE c.id=?", (call_id,))
    if not rows:
        raise HTTPException(404, "call not found")
    return {**rows[0], "messages": await db.select("messages", order="seq", session_id=call_id), "tool_calls": await db.select("tool_calls", order="created_at, rowid", session_id=call_id),
            "appointments": await db.select("appointments", order="starts_at", session_id=call_id)}


class WsEmitter:
    def __init__(self, ws: WebSocket):
        self.ws = ws

    async def json(self, event):
        if self.ws.client_state == WebSocketState.CONNECTED:
            try:
                await self.ws.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                pass

    async def bytes(self, data):
        if self.ws.client_state == WebSocketState.CONNECTED:
            try:
                await self.ws.send_bytes(data)
            except (RuntimeError, WebSocketDisconnect):
                pass


@router.websocket("/calls/{session_id}/stream")
async def stream(ws: WebSocket, session_id: str):
    """Client sends: binary PCM16 16 kHz mono frames, then {"type":"audio.end"}; or {"type":"text","text":...}; {"type":"interrupt"}; {"type":"hangup"}."""
    s = ws.app.state.services
    await ws.accept()
    call = s.calls.get(session_id)
    if call is None or call.emitter is not None:
        await ws.close(code=4404 if call is None else 4409)
        return
    call.emitter = WsEmitter(ws)
    inbox: asyncio.Queue = asyncio.Queue()

    async def worker():
        while (item := await inbox.get()) is not None:
            kind, payload = item
            try:
                await (call.handle_audio(payload) if kind == "audio" else call.handle_text(payload))
            except Exception:  # noqa: BLE001
                log.exception("call input failed")
                await call.emitter.json({"type": "error", "message": "something went wrong handling that input"})

    task = asyncio.create_task(worker())
    buffer, cap = bytearray(), (s.settings.max_utterance_seconds + 1) * s.settings.sample_rate * 2
    try:
        await call.start()
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                buffer += msg["bytes"][: max(0, cap - len(buffer))]
                continue
            try:
                data = json.loads(msg.get("text") or "")
                kind = data.get("type")
            except (json.JSONDecodeError, AttributeError):
                await call.emitter.json({"type": "error", "message": "expected a JSON object with a type"})
                continue
            if kind == "audio.end":
                await inbox.put(("audio", bytes(buffer)))
                buffer.clear()
            elif kind == "text":
                await inbox.put(("text", str(data.get("text", ""))[:500]))
            elif kind == "interrupt":
                buffer.clear()
                call.interrupt()
            elif kind == "hangup":
                break
            else:
                await call.emitter.json({"type": "error", "message": f"unknown message type {kind!r}"})
    except WebSocketDisconnect:
        pass
    finally:
        await inbox.put(None)                 # finish the turn in progress (it may be mid-booking) rather than cancelling it
        await task
        await call.end("caller hung up")
        call.emitter = None
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):   # the caller may already have closed the socket
            await ws.close()


@router.post("/handoff")
async def handoff(body: HandoffIn, request: Request):
    s = svc(request)
    call = s.calls.get(body.session_id)
    if call is not None:
        await call.request_handoff(body.reason)
        return {"session_id": body.session_id, "status": "transferred"}
    row = await s.db.one("call_sessions", id=body.session_id)
    if row is None:
        raise HTTPException(404, "call not found")
    if row["status"] != "active":
        raise HTTPException(409, f"call is already {row['status']}")
    await s.db.update("call_sessions", {"id": body.session_id}, status="transferred", handoff_requested=1, handoff_reason=body.reason)
    return {"session_id": body.session_id, "status": "transferred"}


# ------------------------------------------------------------------ appointments
APPT_SQL = ("SELECT a.*, c.name AS customer_name, c.phone AS customer_phone FROM appointments a JOIN customers c ON c.id=a.customer_id")


@router.get("/appointments")
async def list_appointments(request: Request, status: str = Query("all", pattern="^(all|booked|cancelled)$"), upcoming: bool = False):
    clauses, params = [], []
    if status != "all":
        clauses.append("a.status=?")
        params.append(status)
    if upcoming:
        clauses.append("a.starts_at >= ?")
        params.append(timeutil.iso(timeutil.utcnow()))
    return await svc(request).db.raw(f"{APPT_SQL} {'WHERE ' + ' AND '.join(clauses) if clauses else ''} ORDER BY a.starts_at", params)


@router.post("/appointments", status_code=201)
async def create_appointment(body: AppointmentIn, request: Request):
    s = svc(request)
    try:
        customer, _ = await s.booking.create_customer(body.customer_name, body.phone_number)
        return await s.booking.book(customer["id"], body.service, _day(body.date), body.time)
    except BookingError as e:
        _raise(e)


@router.put("/appointments/{appointment_id}")
async def reschedule_appointment(appointment_id: str, body: AppointmentPatch, request: Request):
    try:
        return await svc(request).booking.reschedule(appointment_id, _day(body.date), body.time)
    except BookingError as e:
        _raise(e)


@router.delete("/appointments/{appointment_id}", status_code=204)
async def cancel_appointment(appointment_id: str, request: Request):
    try:
        await svc(request).booking.cancel(appointment_id)
    except BookingError as e:
        _raise(e)
    return Response(status_code=204)


# ------------------------------------------------------------------ customers
@router.get("/customers")
async def list_customers(request: Request):
    return await svc(request).db.raw(
        "SELECT c.*, COUNT(a.id) AS appointment_count, SUM(CASE WHEN a.status='booked' AND a.starts_at >= ? THEN 1 ELSE 0 END) AS upcoming_count "
        "FROM customers c LEFT JOIN appointments a ON a.customer_id=c.id GROUP BY c.id ORDER BY c.created_at DESC", (timeutil.iso(timeutil.utcnow()),))
