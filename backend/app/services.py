from dataclasses import dataclass
from typing import Any

from app.agent import Receptionist
from app.booking import Booking
from app.business import Business, load_business
from app.calendars import CalendarProvider, LocalCalendar
from app.calls import CallRegistry
from app.config import Settings
from app.db import Database
from app.llm import LLMProvider, OpenAICompatibleLLM
from app.mock_llm import MockLLM
from app.notify import MockNotifier, Notifier
from app.voice import EdgeTTS, MockSTT, MockTTS, OpenAICompatibleSTT, STTProvider, TTSProvider

DETAIL = {("stt", "mock"): "cannot recognise speech; type what the caller says", ("llm", "mock"): "rule-based stand-in, not a language model",
          ("tts", "mock"): "no audio; the browser's built-in voice reads replies", ("calendar", "mock"): "local development calendar (not Google Calendar)",
          ("notifier", "mock"): "text confirmations are not delivered"}


@dataclass
class Services:
    settings: Settings
    db: Database
    business: Business
    calendar: CalendarProvider
    notifier: Notifier
    booking: Booking
    stt: STTProvider
    llm: LLMProvider
    tts: TTSProvider
    agent: Receptionist
    calls: CallRegistry

    def components(self) -> dict[str, dict[str, Any]]:
        parts = {"stt": self.stt, "llm": self.llm, "tts": self.tts, "calendar": self.calendar, "notifier": self.notifier}
        return {k: {"provider": p.name, "mode": "mock" if p.is_mock else "live", "detail": DETAIL.get((k, "mock"), "") if p.is_mock else ""} for k, p in parts.items()}

    @property
    def mock_mode(self) -> bool:
        return any(c["mode"] == "mock" for c in self.components().values())


def build_services(settings: Settings, db: Database, business: Business | None = None, **overrides: Any) -> Services:
    """Wires providers from settings. Pass llm=, stt=, tts=, calendar= or notifier= to substitute any component (this is how Google Calendar plugs in)."""
    business = business or load_business(settings.business_config)
    calendar = overrides.get("calendar") or LocalCalendar(db)
    notifier = overrides.get("notifier") or MockNotifier()
    llm = overrides.get("llm") or (OpenAICompatibleLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
                                   if settings.llm_provider == "openai" else MockLLM())
    stt = overrides.get("stt") or (OpenAICompatibleSTT(settings.stt_base_url or settings.llm_base_url, settings.stt_api_key or settings.llm_api_key, settings.stt_model)
                                   if settings.stt_provider == "openai" else MockSTT())
    tts = overrides.get("tts") or (EdgeTTS(settings.tts_voice) if settings.tts_provider == "edge" else MockTTS())
    booking = Booking(db, calendar, business)
    return Services(settings, db, business, calendar, notifier, booking, stt, llm, tts, Receptionist(llm, booking, business, db, notifier, settings), CallRegistry())
