import asyncio
from datetime import date, timedelta

import pytest

from app import timeutil
from app.agent import CLAIM_RE, classify_yes_no
from app.booking import Booking, BookingError, normalize_phone
from app.business import load_business
from app.calendars import LocalCalendar, SlotTaken
from app.db import Database

THURSDAY, SUNDAY = date(2026, 10, 8), date(2026, 10, 11)


@pytest.fixture
async def env():
    db = Database(":memory:")
    await db.connect()
    biz = load_business("business.json")
    booking = Booking(db, LocalCalendar(db), biz)
    customer, _ = await booking.create_customer("Priya", "9876543210")
    yield booking, biz, customer
    await db.close()


async def test_slots_follow_hours_notice_and_closed_days(env):
    booking, biz, _ = env
    consult = biz.service("General consultation")
    assert len(await booking.slots(THURSDAY, consult)) == 16                       # 09:00 to 16:30 every 30 minutes
    assert await booking.slots(SUNDAY, consult) == []                              # closed
    today = [s.astimezone(biz.tz).strftime("%H:%M") for s in await booking.slots(date(2026, 10, 7), consult)]
    assert today[0] == "11:00"                                                     # it is 10:00 and 60 minutes notice is required
    checkup = biz.service("Health check-up")                                       # 60 minutes: last start is 16:00
    assert [s.astimezone(biz.tz).strftime("%H:%M") for s in await booking.slots(THURSDAY, checkup)][-1] == "16:00"


async def test_booking_blocks_double_booking_and_overlap(env):
    booking, _, c = env
    first = await booking.book(c["id"], "General consultation", THURSDAY, "15:00")
    assert first["status"] == "booked"
    with pytest.raises(BookingError) as e:
        await booking.book(c["id"], "General consultation", THURSDAY, "15:00")
    assert e.value.code == "slot_unavailable"
    with pytest.raises(BookingError):                                              # a 60-minute check-up at 14:30 would overlap 15:00
        await booking.book(c["id"], "Health check-up", THURSDAY, "14:30")


async def test_invalid_requests_are_rejected(env):
    booking, _, c = env
    with pytest.raises(BookingError, match="closed"):
        await booking.book(c["id"], "General consultation", SUNDAY, "10:00")
    with pytest.raises(BookingError, match="not available"):
        await booking.book(c["id"], "General consultation", THURSDAY, "18:00")      # after hours
    with pytest.raises(BookingError, match="not available"):
        await booking.book(c["id"], "General consultation", date(2026, 10, 7), "10:30")   # inside the notice window
    with pytest.raises(BookingError, match="available services"):
        await booking.book(c["id"], "Massage", THURSDAY, "10:00")
    with pytest.raises(BookingError):
        normalize_phone("12")


async def test_reschedule_and_cancel_free_the_slot_and_check_ownership(env):
    booking, biz, c = env
    other, _ = await booking.create_customer("Ravi", "9123456780")
    appt = await booking.book(c["id"], "General consultation", THURSDAY, "10:00")
    moved = await booking.reschedule(appt["id"], THURSDAY, "10:30", c["id"])        # overlaps nothing but its own old slot
    assert timeutil.parse_iso(moved["starts_at"]).astimezone(biz.tz).strftime("%H:%M") == "10:30"
    with pytest.raises(BookingError, match="no appointment"):
        await booking.cancel(appt["id"], other["id"])                               # someone else's appointment
    await booking.cancel(appt["id"], c["id"])
    await booking.book(other["id"], "General consultation", THURSDAY, "10:30")      # slot is free again
    with pytest.raises(BookingError, match="already cancelled"):
        await booking.cancel(appt["id"])


async def test_calendar_creates_are_atomic_under_concurrency(env):
    booking, _, _ = env
    cal, start = booking.calendar, timeutil.local_to_utc(THURSDAY, "12:00", booking.business.tz)
    end = start + timedelta(minutes=30)
    results = await asyncio.gather(*(cal.create_event("x", start, end) for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(r, str) for r in results) == 1 and sum(isinstance(r, SlotTaken) for r in results) == 4


def test_yes_no_classifier():
    assert [classify_yes_no(t) for t in ("Yes", "yes please", "yeah sure", "go ahead", "No", "no thanks", "nope", "yes but make it 4", "hello", "what time are you open")] == \
        ["yes", "yes", "yes", "yes", "no", "no", "no", None, None, None]


def test_claim_guard_blocks_unverified_success_claims_but_not_ordinary_speech():
    for bad in ("Great, your appointment is booked for tomorrow at 4.", "You're all set, see you then.", "I've booked that for you.",
                "Your appointment has been cancelled.", "You are booked for Friday."):
        assert CLAIM_RE.search(bad), bad
    for ok in ("Sorry, 4 PM is already booked.", "Would you like me to book that?", "I can book you in at 3 PM if you like.", "We're open until 5."):
        assert not CLAIM_RE.search(ok), ok
