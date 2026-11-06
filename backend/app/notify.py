from dataclasses import dataclass
from typing import Protocol


@dataclass
class NotifyResult:
    delivered: bool
    note: str


class Notifier(Protocol):
    name: str
    is_mock: bool

    async def send(self, to: str, text: str) -> NotifyResult: ...


class MockNotifier:
    """Sends nothing. It reports delivered=False so the agent never tells a caller a text was sent when it was not."""

    name, is_mock = "mock", True

    async def send(self, to: str, text: str) -> NotifyResult:
        return NotifyResult(False, "mock mode: message was not delivered")
