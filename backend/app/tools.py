"""The agent's tools. Read-only tools run immediately. book/reschedule/cancel are 'gated': they are validated first, read back to the
caller, and only run after the caller says yes. Their spoken outcome is composed here from the tool result, never by the model."""
import re
from dataclasses import dataclass
from typing import Any

from app import timeutil
from app.booking import Booking, BookingError, normalize_phone
from app.business import Business
from app.db import Database
from app.notify import Notifier
from app.state import SessionState


class ToolError(Exception):
    """A problem the model can fix (bad argument, missing detail, unavailable time). Returned to the model, not an outage."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ToolContext:
    state: SessionState
    booking: Booking
    business: Business
    db: Database
    notifier: Notifier


def _date(v: str) -> str:
    try:
        timeutil.parse_date(v)
    except ValueError as e:
        raise ToolError("invalid_date", "dates must be YYYY-MM-DD") from e
    return v


def _time(v: str) -> str:
    try:
        timeutil.parse_hhmm(v)
    except ValueError as e:
        raise ToolError("invalid_time", "times must be 24-hour HH:MM, for example 15:30") from e
    return v


def _phone(v: str) -> str:
    try:
        return normalize_phone(v)
    except BookingError as e:
        raise ToolError(e.code, e.message) from e


NORMALISERS = {"date": _date, "requested_date": _date, "new_date": _date, "time": _time, "requested_time": _time, "new_time": _time,
               "preferred_time": _time, "phone_number": _phone}
JSON_TYPES = {"string": str, "boolean": bool}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    props, clean = schema.get("properties", {}), {}
    for key in schema.get("required", []):
        if args.get(key) in (None, ""):
            raise ToolError("missing_argument", f"missing required argument: {key}")
    for key, spec in props.items():
        val = args.get(key)
        if val is None or val == "":
            continue
        if not isinstance(val, JSON_TYPES.get(spec.get("type", "string"), str)):
            raise ToolError("invalid_argument", f"argument {key} must be a {spec.get('type', 'string')}")
        if "enum" in spec and val not in spec["enum"]:
            raise ToolError("invalid_argument", f"argument {key} must be one of {spec['enum']}")
        if isinstance(val, str) and len(val) > 200:
            raise ToolError("invalid_argument", f"argument {key} is too long")
        clean[key] = NORMALISERS[key](val) if key in NORMALISERS else val
    return clean


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    gated = False

    async def validate(self, ctx: ToolContext, args: dict) -> dict:
        return validate_args(self.parameters, args)

    async def prepare(self, ctx: ToolContext, args: dict) -> tuple[dict, str]:
        """Gated tools only: check everything that can be checked now and return (final args, read-back summary)."""
        raise NotImplementedError

    async def run(self, ctx: ToolContext, args: dict) -> dict:
        raise NotImplementedError

    def spoken_result(self, ctx: ToolContext, result: dict) -> str:
        raise NotImplementedError


def _when(ctx: ToolContext, appt: dict) -> str:
    return timeutil.spoken(timeutil.parse_iso(appt["starts_at"]), ctx.business.tz)


def _appt_summary(ctx: ToolContext, appt: dict) -> dict:
    return {"appointment_id": appt["id"], "service": appt["service"], "when": _when(ctx, appt), "status": appt["status"]}


# ------------------------------------------------------------------ read-only tools
class UpdateSession(Tool):
    name = "update_session"
    description = "Record details the caller has just given you. Call it every time the caller provides or changes any of these."
    parameters = _obj({
        "intent": {"type": "string", "enum": ["book", "reschedule", "cancel", "inquiry", "other"]},
        "customer_name": {"type": "string"}, "phone_number": {"type": "string"}, "service": {"type": "string"},
        "appointment_id": {"type": "string"},
        "requested_date": {"type": "string", "description": "YYYY-MM-DD"}, "requested_time": {"type": "string", "description": "24-hour HH:MM"}})

    async def run(self, ctx, args):
        s = ctx.state
        before = (s.service, s.requested_date, s.requested_time)
        for key, value in args.items():
            setattr(s, key, value)
        if (s.service, s.requested_date, s.requested_time) != before:
            s.confirmed = False                   # the details changed, so any earlier "yes" no longer applies
        return {"state": s.public()}


class GetBusinessInformation(Tool):
    name = "get_business_information"
    description = "Business name, address, phone, opening hours, services with durations, and FAQ answers. Answer questions ONLY from this."
    parameters = _obj({"topic": {"type": "string", "description": "optional keyword to filter the FAQ, e.g. parking"}})

    async def run(self, ctx, args):
        b, topic = ctx.business, (args.get("topic") or "").lower()
        words = {w for w in re.findall(r"[a-z]+", topic) if len(w) > 3}
        faq = [f for f in b.faq if f["topic"].lower() in topic or any(w in f["topic"].lower() or w in f["answer"].lower() for w in words)]
        return {"name": b.name, "phone": b.phone, "address": b.address, "hours": b.hours_text(), "timezone": b.timezone,
                "services": [{"name": s.name, "duration_min": s.duration_min} for s in b.services], "faq": faq or list(b.faq)}


class CheckAvailability(Tool):
    name = "check_availability"
    description = "List the open appointment times for a date. The only valid source of availability."
    parameters = _obj({"date": {"type": "string", "description": "YYYY-MM-DD"}, "service": {"type": "string"},
                       "preferred_time": {"type": "string", "description": "24-hour HH:MM the caller asked for"}}, ["date"])

    async def run(self, ctx, args):
        s, b = ctx.state, ctx.business
        name = args.get("service") or s.service or (b.services[0].name if len(b.services) == 1 else None)
        if name is None:
            raise ToolError("service_required", "ask which service: " + ", ".join(x.name for x in b.services))
        try:
            service = ctx.booking.require_service(name)
        except BookingError as e:
            raise ToolError(e.code, e.message) from e
        day = timeutil.parse_date(args["date"])
        s.service, s.requested_date = service.name, args["date"]
        if args.get("preferred_time"):
            s.requested_time = args["preferred_time"]
        if b.window(day) is None:
            return {"date": args["date"], "weekday": f"{day:%A}", "service": service.name, "open_day": False, "slots": [], "message": f"closed on {day:%A}s"}
        local = [x.astimezone(b.tz) for x in await ctx.booking.slots(day, service)]
        slots = [f"{x:%H:%M}" for x in local]
        pref = args.get("preferred_time")
        nearest = []
        if pref and slots:
            h, m = map(int, pref.split(":"))
            nearest = sorted(sorted(slots, key=lambda t: abs(int(t[:2]) * 60 + int(t[3:]) - (h * 60 + m)))[:3])
        return {"date": args["date"], "weekday": f"{day:%A}", "service": service.name, "duration_min": service.duration_min, "open_day": True,
                "slots": slots[:14], "total_open": len(slots), "preferred_time": pref,
                "preferred_time_available": (pref in slots) if pref else None, "nearest_to_preferred": nearest}


class GetCustomer(Tool):
    name = "get_customer"
    description = "Look up the caller by phone number. Returns their upcoming appointments (with ids) if they exist."
    parameters = _obj({"phone_number": {"type": "string"}}, ["phone_number"])

    async def run(self, ctx, args):
        s = ctx.state
        s.phone_number = args["phone_number"]
        customer = await ctx.booking.find_customer(args["phone_number"])
        if customer is None:
            return {"found": False}
        s.customer_id, s.customer_name = customer["id"], customer["name"]
        upcoming = await ctx.booking.upcoming_for(customer["id"])
        return {"found": True, "name": customer["name"], "upcoming_appointments": [_appt_summary(ctx, a) for a in upcoming]}


class CreateCustomer(Tool):
    name = "create_customer"
    description = "Register a new caller. Use only after get_customer found nobody, and after the caller has told you their name."
    parameters = _obj({"name": {"type": "string"}, "phone_number": {"type": "string"}}, ["name", "phone_number"])

    async def run(self, ctx, args):
        s = ctx.state
        customer, created = await ctx.booking.create_customer(args["name"], args["phone_number"])
        s.customer_id, s.customer_name, s.phone_number = customer["id"], customer["name"], customer["phone"]
        return {"created": created, "customer_id": customer["id"], "name": customer["name"]}


class SendConfirmation(Tool):
    name = "send_confirmation"
    description = "Text the caller the details of one of their appointments. Normally called by the system right after a change."
    parameters = _obj({"appointment_id": {"type": "string"}, "kind": {"type": "string", "enum": ["booked", "rescheduled", "cancelled"]}}, ["appointment_id"])

    async def run(self, ctx, args):
        appt = await ctx.booking.get(args["appointment_id"])
        if appt is None or appt["customer_id"] != ctx.state.customer_id:
            raise ToolError("not_found", "no appointment with that id for this caller")
        kind = args.get("kind") or ("cancelled" if appt["status"] == "cancelled" else "booked")
        text = f"{ctx.business.name}: your {appt['service']} is {kind} for {_when(ctx, appt)}." if kind != "cancelled" else \
            f"{ctx.business.name}: your {appt['service']} on {_when(ctx, appt)} has been cancelled."
        result = await ctx.notifier.send(appt["customer_phone"], text)
        return {"delivered": result.delivered, "note": result.note, "to": "***" + appt["customer_phone"][-4:], "text": text}


class TransferToHuman(Tool):
    name = "transfer_to_human"
    description = ("Hand the call to a human receptionist. Use when the caller asks for a person, is frustrated, has a sensitive or "
                   "complex situation, or you cannot resolve the request.")
    parameters = _obj({"reason": {"type": "string"}}, ["reason"])

    async def run(self, ctx, args):
        ctx.state.handoff_requested, ctx.state.handoff_reason = True, args["reason"]
        return {"transferred": True}


# ------------------------------------------------------------------ gated tools
class _AppointmentTool(Tool):
    gated = True

    def appointment_id(self, ctx, args) -> str:
        appt_id = args.get("appointment_id") or ctx.state.appointment_id
        if not appt_id:
            raise ToolError("appointment_required", "call get_customer and ask which appointment the caller means")
        return appt_id

    @staticmethod
    def require_customer(ctx):
        if not ctx.state.customer_id:
            raise ToolError("not_identified", "ask for the caller's phone number, call get_customer, and if not found ask the name and call create_customer")
        return ctx.state.customer_id


class BookAppointment(_AppointmentTool):
    name = "book_appointment"
    description = ("Book an appointment. Arguments default to the session state. The system will read the details back and ask the caller "
                   "to confirm; it then completes the booking and announces the result itself.")
    parameters = _obj({"service": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "24-hour HH:MM"}})

    async def prepare(self, ctx, args):
        s, customer_id = ctx.state, self.require_customer(ctx)
        service_name, date_s, time_s = args.get("service") or s.service, args.get("date") or s.requested_date, args.get("time") or s.requested_time
        missing = [k for k, v in (("service", service_name), ("date", date_s), ("time", time_s)) if not v]
        if missing:
            raise ToolError("missing_details", "still need: " + ", ".join(missing))
        try:
            service = ctx.booking.require_service(service_name)
            start = await ctx.booking.check_slot(service, timeutil.parse_date(date_s), time_s)
        except BookingError as e:
            raise ToolError(e.code, e.message) from e
        return ({"customer_id": customer_id, "service": service.name, "date": date_s, "time": time_s},
                f"book a {service.name} on {timeutil.spoken(start, ctx.business.tz)} for {s.customer_name}")

    async def run(self, ctx, args):
        appt = await ctx.booking.book(args["customer_id"], args["service"], timeutil.parse_date(args["date"]), args["time"], ctx.state.session_id)
        ctx.state.appointment_id, ctx.state.confirmed = appt["id"], True
        return {"appointment_id": appt["id"], **_appt_summary(ctx, appt)}

    def spoken_result(self, ctx, result):
        return f"You're all set. Your {result['service']} is booked for {result['when']}."


class RescheduleAppointment(_AppointmentTool):
    name = "reschedule_appointment"
    description = ("Move an existing appointment to a new date and time. The system reads the change back, asks the caller to confirm, "
                   "then completes it and announces the result itself.")
    parameters = _obj({"appointment_id": {"type": "string"}, "new_date": {"type": "string", "description": "YYYY-MM-DD"},
                       "new_time": {"type": "string", "description": "24-hour HH:MM"}}, ["new_date", "new_time"])

    async def prepare(self, ctx, args):
        customer_id, appt_id = self.require_customer(ctx), self.appointment_id(ctx, args)
        try:
            appt = await ctx.booking.owned(appt_id, customer_id)
            service = ctx.booking.require_service(appt["service"])
            start = await ctx.booking.check_slot(service, timeutil.parse_date(args["new_date"]), args["new_time"], appt["calendar_event_id"])
        except BookingError as e:
            raise ToolError(e.code, e.message) from e
        return ({"customer_id": customer_id, "appointment_id": appt_id, "new_date": args["new_date"], "new_time": args["new_time"]},
                f"move your {appt['service']} from {_when(ctx, appt)} to {timeutil.spoken(start, ctx.business.tz)}")

    async def run(self, ctx, args):
        appt = await ctx.booking.reschedule(args["appointment_id"], timeutil.parse_date(args["new_date"]), args["new_time"], args["customer_id"])
        ctx.state.appointment_id, ctx.state.confirmed = appt["id"], True
        return _appt_summary(ctx, appt)

    def spoken_result(self, ctx, result):
        return f"Done. Your {result['service']} is now on {result['when']}."


class CancelAppointment(_AppointmentTool):
    name = "cancel_appointment"
    description = "Cancel an existing appointment. The system reads it back, asks the caller to confirm, then completes it and announces the result itself."
    parameters = _obj({"appointment_id": {"type": "string"}})

    async def prepare(self, ctx, args):
        customer_id, appt_id = self.require_customer(ctx), self.appointment_id(ctx, args)
        try:
            appt = await ctx.booking.owned(appt_id, customer_id)
        except BookingError as e:
            raise ToolError(e.code, e.message) from e
        return {"customer_id": customer_id, "appointment_id": appt_id}, f"cancel your {appt['service']} on {_when(ctx, appt)}"

    async def run(self, ctx, args):
        appt = await ctx.booking.cancel(args["appointment_id"], args["customer_id"])
        ctx.state.appointment_id, ctx.state.confirmed = None, True
        return _appt_summary(ctx, appt)

    def spoken_result(self, ctx, result):
        return f"Okay, your {result['service']} on {result['when']} has been cancelled."


TOOLS: list[Tool] = [UpdateSession(), GetBusinessInformation(), CheckAvailability(), GetCustomer(), CreateCustomer(), BookAppointment(),
                     RescheduleAppointment(), CancelAppointment(), SendConfirmation(), TransferToHuman()]
