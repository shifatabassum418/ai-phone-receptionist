"""Appointment business logic shared by the agent tools and the REST API."""
import asyncio
import re
import uuid
from datetime import date, timedelta

from app import timeutil
from app.business import Business, Service
from app.calendars import CalendarProvider, SlotTaken
from app.db import Database


class BookingError(Exception):
    """A business-rule failure (not an outage). The message is safe to show or speak."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15:
        raise BookingError("invalid_phone", "that phone number does not look right")
    return ("+" if raw.strip().startswith("+") else "") + digits


class Booking:
    def __init__(self, db: Database, calendar: CalendarProvider, business: Business):
        self.db, self.calendar, self.business = db, calendar, business
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ customers
    async def find_customer(self, phone: str) -> dict | None:
        return await self.db.one("customers", phone=normalize_phone(phone))

    async def create_customer(self, name: str, phone: str) -> tuple[dict, bool]:
        existing = await self.find_customer(phone)
        if existing:
            return existing, False
        row = {"id": uuid.uuid4().hex, "name": name.strip(), "phone": normalize_phone(phone), "created_at": timeutil.iso(timeutil.utcnow())}
        await self.db.insert("customers", **row)
        return row, True

    # ------------------------------------------------------------ availability
    def require_service(self, name: str | None) -> Service:
        service = self.business.service(name)
        if service is None:
            raise BookingError("unknown_service", "available services are: " + ", ".join(s.name for s in self.business.services))
        return service

    async def slots(self, day: date, service: Service, exclude_event_id: str | None = None):
        window = self.business.window(day)
        if window is None:
            return []
        free = await self.calendar.get_availability(window[0], window[1], service.duration_min, self.business.slot_minutes, exclude_event_id)
        earliest = timeutil.utcnow() + timedelta(minutes=self.business.min_notice_minutes)
        return [s for s in free if s >= earliest]

    async def check_slot(self, service: Service, day: date, hhmm: str, exclude_event_id: str | None = None):
        if self.business.window(day) is None:
            raise BookingError("closed", f"we are closed on {day:%A}s")
        start = timeutil.local_to_utc(day, hhmm, self.business.tz)
        if start not in await self.slots(day, service, exclude_event_id):
            raise BookingError("slot_unavailable", f"{timeutil.spoken(start, self.business.tz)} is not available")
        return start

    # ------------------------------------------------------------ appointments
    async def get(self, appointment_id: str) -> dict | None:
        rows = await self.db.raw("SELECT a.*, c.name AS customer_name, c.phone AS customer_phone FROM appointments a "
                                 "JOIN customers c ON c.id=a.customer_id WHERE a.id=?", (appointment_id,))
        return rows[0] if rows else None

    async def owned(self, appointment_id: str, customer_id: str | None) -> dict:
        appt = await self.get(appointment_id)
        if appt is None or (customer_id and appt["customer_id"] != customer_id):
            raise BookingError("not_found", "no appointment with that id for this caller")
        if appt["status"] != "booked":
            raise BookingError("not_bookable", f"that appointment is already {appt['status']}")
        return appt

    async def book(self, customer_id: str, service_name: str, day: date, hhmm: str, session_id: str | None = None) -> dict:
        service = self.require_service(service_name)
        async with self._lock:
            start = await self.check_slot(service, day, hhmm)
            end = start + timedelta(minutes=service.duration_min)
            customer = await self.db.one("customers", id=customer_id)
            try:
                event_id = await self.calendar.create_event(f"{service.name}: {customer['name']}", start, end, f"phone {customer['phone']}")
            except SlotTaken as e:
                raise BookingError("slot_unavailable", "that time was just taken") from e
            now = timeutil.iso(timeutil.utcnow())
            appt_id = uuid.uuid4().hex
            try:
                await self.db.insert("appointments", id=appt_id, customer_id=customer_id, service=service.name, starts_at=timeutil.iso(start),
                                     ends_at=timeutil.iso(end), status="booked", calendar_event_id=event_id, session_id=session_id,
                                     created_at=now, updated_at=now)
            except Exception:
                await self.calendar.delete_event(event_id)      # do not leave an orphan event behind
                raise
        return await self.get(appt_id)

    async def reschedule(self, appointment_id: str, day: date, hhmm: str, customer_id: str | None = None) -> dict:
        appt = await self.owned(appointment_id, customer_id)
        service = self.require_service(appt["service"])
        async with self._lock:
            start = await self.check_slot(service, day, hhmm, exclude_event_id=appt["calendar_event_id"])
            end = start + timedelta(minutes=service.duration_min)
            try:
                await self.calendar.update_event(appt["calendar_event_id"], start, end)
            except SlotTaken as e:
                raise BookingError("slot_unavailable", "that time was just taken") from e
            await self.db.update("appointments", {"id": appointment_id}, starts_at=timeutil.iso(start), ends_at=timeutil.iso(end),
                                 updated_at=timeutil.iso(timeutil.utcnow()))
        return await self.get(appointment_id)

    async def cancel(self, appointment_id: str, customer_id: str | None = None) -> dict:
        appt = await self.owned(appointment_id, customer_id)
        async with self._lock:
            await self.calendar.delete_event(appt["calendar_event_id"])   # if this fails the appointment stays booked
            await self.db.update("appointments", {"id": appointment_id}, status="cancelled", updated_at=timeutil.iso(timeutil.utcnow()))
        return await self.get(appointment_id)

    async def upcoming_for(self, customer_id: str) -> list[dict]:
        return await self.db.raw("SELECT * FROM appointments WHERE customer_id=? AND status='booked' AND starts_at >= ? ORDER BY starts_at",
                                 (customer_id, timeutil.iso(timeutil.utcnow())))
