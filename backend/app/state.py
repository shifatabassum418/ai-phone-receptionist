from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    """Everything the agent remembers during one call."""

    session_id: str
    customer_id: str | None = None
    customer_name: str | None = None
    phone_number: str | None = None
    intent: str | None = None            # book | reschedule | cancel | inquiry | other
    service: str | None = None
    appointment_id: str | None = None
    requested_date: str | None = None    # YYYY-MM-DD, business-local
    requested_time: str | None = None    # HH:MM, business-local
    confirmed: bool = False              # the caller said yes to the last action that was read back
    handoff_requested: bool = False
    handoff_reason: str | None = None
    pending: dict[str, Any] | None = None            # action waiting for the caller's yes/no
    failures: int = 0                                # invalid tool calls so far
    conversation_history: list[dict[str, str]] = field(default_factory=list)   # {"role": caller|agent, "text": ...}

    def public(self) -> dict[str, Any]:
        """What the dashboard and the model see (no raw pending arguments, no transcript)."""
        return {"session_id": self.session_id, "customer_id": self.customer_id, "customer_name": self.customer_name, "phone_number": self.phone_number,
                "intent": self.intent, "service": self.service, "appointment_id": self.appointment_id, "requested_date": self.requested_date,
                "requested_time": self.requested_time, "confirmed": self.confirmed, "handoff_requested": self.handoff_requested,
                "handoff_reason": self.handoff_reason, "awaiting_confirmation": self.pending["summary"] if self.pending else None,
                "turns": len(self.conversation_history) // 2}
