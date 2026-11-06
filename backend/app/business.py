import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.timeutil import local_to_utc

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Service:
    name: str
    duration_min: int


@dataclass(frozen=True)
class Business:
    name: str
    phone: str
    address: str
    timezone: str
    hours: dict[str, tuple[str, str] | None]
    services: tuple[Service, ...]
    faq: tuple[dict, ...] = field(default_factory=tuple)
    slot_minutes: int = 30
    min_notice_minutes: int = 60
    escalation_keywords: tuple[str, ...] = ()
    escalation_message: str = "Let me connect you with a human receptionist."

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def service(self, name: str | None) -> Service | None:
        if not name:
            return None
        n = name.strip().lower()
        exact = [s for s in self.services if s.name.lower() == n]
        partial = [s for s in self.services if n in s.name.lower() or s.name.lower() in n]
        return (exact or (partial if len(partial) == 1 else []) or [None])[0]

    def window(self, day: date) -> tuple[datetime, datetime] | None:
        """Opening and closing time of a local day as UTC datetimes, or None when closed."""
        h = self.hours.get(DAYS[day.weekday()])
        return (local_to_utc(day, h[0], self.tz), local_to_utc(day, h[1], self.tz)) if h else None

    def hours_text(self) -> str:
        return "; ".join(f"{d.capitalize()} {h[0]} to {h[1]}" if h else f"{d.capitalize()} closed" for d, h in self.hours.items())


def load_business(path: str) -> Business:
    raw = json.loads(Path(path).read_text())
    ZoneInfo(raw["timezone"])
    hours = {d: (tuple(raw["hours"][d]) if raw["hours"].get(d) else None) for d in DAYS}
    return Business(raw["name"], raw["phone"], raw["address"], raw["timezone"], hours,
                    tuple(Service(s["name"], int(s["duration_min"])) for s in raw["services"]), tuple(raw.get("faq", [])),
                    int(raw.get("slot_minutes", 30)), int(raw.get("min_notice_minutes", 60)),
                    tuple(k.lower() for k in raw.get("escalation_keywords", [])),
                    raw.get("escalation_message", Business.escalation_message))
