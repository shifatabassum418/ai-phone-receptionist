"""All timestamps are stored in UTC as 'YYYY-MM-DDTHH:MM:SSZ'; business-local time is only used for input and speech."""
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(FMT)


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=UTC)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_hhmm(s: str) -> time:
    return datetime.strptime(s, "%H:%M").time()


def local_to_utc(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=tz).astimezone(UTC)


def spoken_time(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def spoken(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return f"{local:%A}, {local.day} {local:%B} at {spoken_time(dt, tz)}"
