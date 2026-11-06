"""Calendar abstraction. getAvailability / createEvent / updateEvent / deleteEvent map to the four async methods below.

To use Google Calendar, implement CalendarProvider (see README) and pass it to build_services(); nothing else changes.
"""
import uuid
from datetime import datetime, timedelta
from typing import Protocol

from app.db import Database
from app.timeutil import iso, parse_iso


class CalendarError(Exception):
    """The calendar could not be reached or failed. The agent treats this as a tool failure and hands off to a human."""


class SlotTaken(Exception):
    """The requested time overlaps an existing event."""


class CalendarProvider(Protocol):
    name: str
    is_mock: bool

    async def get_availability(self, window_start: datetime, window_end: datetime, duration_min: int, step_min: int,
                               exclude_event_id: str | None = None) -> list[datetime]: ...
    async def create_event(self, title: str, start: datetime, end: datetime, description: str = "") -> str: ...
    async def update_event(self, event_id: str, start: datetime, end: datetime) -> None: ...
    async def delete_event(self, event_id: str) -> None: ...


def free_slots(busy: list[tuple[datetime, datetime]], window_start: datetime, window_end: datetime, duration_min: int, step_min: int) -> list[datetime]:
    """Start times on a step grid inside the window where [start, start+duration) overlaps no busy interval. Reusable by any provider."""
    out, dur, step, t = [], timedelta(minutes=duration_min), timedelta(minutes=step_min), window_start
    while t + dur <= window_end:
        if not any(b0 < t + dur and b1 > t for b0, b1 in busy):
            out.append(t)
        t += step
    return out


class LocalCalendar:
    """Development calendar stored in the `calendar_events` table. Real data, but local to this app, so the UI labels it as a mock calendar."""

    name, is_mock = "local", True

    def __init__(self, db: Database):
        self.db = db

    async def get_availability(self, window_start, window_end, duration_min, step_min, exclude_event_id=None):
        rows = await self.db.raw("SELECT starts_at, ends_at FROM calendar_events WHERE starts_at < ? AND ends_at > ? AND id != ?",
                                 (iso(window_end), iso(window_start), exclude_event_id or ""))
        return free_slots([(parse_iso(r["starts_at"]), parse_iso(r["ends_at"])) for r in rows], window_start, window_end, duration_min, step_min)

    async def create_event(self, title, start, end, description=""):
        event_id = uuid.uuid4().hex
        # one atomic statement: the row is inserted only if nothing overlaps, so concurrent bookings cannot double-book
        n = await self.db.execute(
            "INSERT INTO calendar_events(id, title, description, starts_at, ends_at) SELECT ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM calendar_events WHERE starts_at < ? AND ends_at > ?)",
            (event_id, title, description, iso(start), iso(end), iso(end), iso(start)))
        if n == 0:
            raise SlotTaken("time overlaps an existing event")
        return event_id

    async def update_event(self, event_id, start, end):
        n = await self.db.execute(
            "UPDATE calendar_events SET starts_at=?, ends_at=? WHERE id=? AND NOT EXISTS "
            "(SELECT 1 FROM calendar_events o WHERE o.id != ? AND o.starts_at < ? AND o.ends_at > ?)",
            (iso(start), iso(end), event_id, event_id, iso(end), iso(start)))
        if n == 0:
            if await self.db.one("calendar_events", id=event_id) is None:
                raise CalendarError("calendar event not found")
            raise SlotTaken("time overlaps an existing event")

    async def delete_event(self, event_id):
        await self.db.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
