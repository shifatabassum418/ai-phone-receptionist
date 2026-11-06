from datetime import UTC, datetime

import pytest

from app import timeutil
from app.config import Settings
from app.db import Database
from app.services import build_services

# Wednesday 7 Oct 2026, 10:00 in Asia/Kolkata. "Tomorrow" is Thursday 8 Oct (open 09:00-17:00).
NOW = datetime(2026, 10, 7, 4, 30, tzinfo=UTC)
TOMORROW = "2026-10-08"


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(timeutil, "utcnow", lambda: NOW)


class Recorder:
    def __init__(self):
        self.events, self.audio = [], []

    async def json(self, event):
        self.events.append(event)

    async def bytes(self, data):
        self.audio.append(data)


class Phone:
    """A caller on a call, driven through the same CallSession the WebSocket uses."""

    def __init__(self, svc, call, rec):
        self.svc, self.call, self.rec = svc, call, rec

    async def say(self, text: str) -> str | None:
        start = len(self.rec.events)
        await self.call.handle_text(text)
        replies = [e["text"] for e in self.rec.events[start:] if e["type"] == "message" and e["role"] == "agent"]
        return replies[-1] if replies else None

    def events(self, kind: str):
        return [e for e in self.rec.events if e["type"] == kind]

    @property
    def state(self):
        return self.call.state

    async def tool_rows(self):
        return await self.svc.db.select("tool_calls", order="rowid", session_id=self.call.id)

    async def appointments(self):
        return await self.svc.db.select("appointments", order="rowid")


@pytest.fixture
async def make_phone(tmp_path):
    opened = []

    async def factory(*, db=None, svc=None, settings_kw=None, **overrides) -> Phone:
        if svc is None:
            settings = Settings(database_path=str(tmp_path / "t.db"), business_config="business.json", **(settings_kw or {}))
            db = db or Database(":memory:")
            if db._conn is None:
                await db.connect()
                opened.append(db)
            svc = build_services(settings, db, **overrides)
        call = await svc.calls.create(svc)
        rec = Recorder()
        call.emitter = rec
        await call.start()
        return Phone(svc, call, rec)

    yield factory
    for db in opened:
        await db.close()
