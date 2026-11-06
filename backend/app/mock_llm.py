"""Offline stand-in for the language model, used in mock mode. It is a small rule-based dialogue policy, NOT a language model:
it understands a handful of English phrasings, fills slots from the session state, and drives the same tools a real model would."""
import json
import re
from datetime import date, timedelta

from app.llm import LLMMessage, LLMResponse, ToolCallRequest, ToolSchema

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
NAME_RE = re.compile(r"\b(?:my name is|name is|this is|name's|i am|i'm|call me)\s+([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*)?)", re.I)
NAME_STOP = {"looking", "calling", "trying", "wanting", "hoping", "interested", "not", "here", "just", "fine", "good", "well", "sorry", "a", "an", "the",
             "new", "back", "at", "in", "on", "for", "to", "and", "i", "need", "want", "would", "tomorrow", "today", "after", "around", "about"}
HELP = "I can help you book, reschedule or cancel an appointment, or answer questions. What would you like to do?"


def _call(tool: str, /, **args) -> LLMResponse:
    return LLMResponse(None, [ToolCallRequest(tool, args)])


def _say(text: str) -> LLMResponse:
    return LLMResponse(text, [])


def _spoken_hhmm(t: str) -> str:
    h, m = int(t[:2]), t[3:]
    return f"{h % 12 or 12}:{m} {'AM' if h < 12 else 'PM'}"


def extract_slots(text: str, today: date, services: list[str], last_agent: str) -> dict:
    low, out = text.lower(), {}
    if m := re.search(r"\b(\d{4}-\d{2}-\d{2})\b", low):
        out["requested_date"] = m.group(1)
    elif "day after tomorrow" in low:
        out["requested_date"] = (today + timedelta(days=2)).isoformat()
    elif "tomorrow" in low:
        out["requested_date"] = (today + timedelta(days=1)).isoformat()
    elif re.search(r"\btoday\b", low):
        out["requested_date"] = today.isoformat()
    elif m := re.search(rf"\b(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+({'|'.join(MONTHS)})|({'|'.join(MONTHS)})\s+(\d{{1,2}}))", low):
        month, day = (MONTHS.index(m.group(2)) + 1, int(m.group(1))) if m.group(1) else (MONTHS.index(m.group(3)) + 1, int(m.group(4)))
        d = date(today.year, month, day)
        out["requested_date"] = (d if d >= today else date(today.year + 1, month, day)).isoformat()
    else:
        for i, w in enumerate(WEEKDAYS):
            if re.search(rf"\b{w}\b", low):
                out["requested_date"] = (today + timedelta(days=(i - today.weekday()) % 7 or 7)).isoformat()
    if m := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", low):
        out["requested_time"] = f"{int(m.group(1)) % 12 + (12 if m.group(3).startswith('p') else 0):02d}:{m.group(2) or '00'}"
    elif (m := re.search(r"\b(?:at|around|about|by)\s+(\d{1,2})(?::(\d{2}))?\b", low)) or ("time" in last_agent.lower() and (m := re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", low))):
        h = int(m.group(1))
        out["requested_time"] = f"{h + 12 if 1 <= h <= 8 else h:02d}:{m.group(2) or '00'}"     # business hours: 1 to 8 means the afternoon
    if m := re.search(r"\+?\d[\d\s\-]{6,}\d", re.sub(r"\d{4}-\d{2}-\d{2}", "", text)):
        out["phone_number"] = re.sub(r"[^\d+]", "", m.group())
    if m := NAME_RE.search(text):
        words = []
        for w in m.group(1).split():
            if w.lower() in NAME_STOP:
                break
            words.append(w.capitalize())
        if words:
            out["customer_name"] = " ".join(words)
    elif "your name" in last_agent.lower() and re.fullmatch(r"[A-Za-z][A-Za-z'\- ]{1,40}", text.strip()):
        out["customer_name"] = text.strip().title()
    hits = [s for s in services if s.lower() in low or any(len(w) >= 6 and w in low for w in re.findall(r"[a-z]+", s.lower()))]
    if len(hits) == 1:
        out["service"] = hits[0]
    return out


def detect_intent(low: str, current: str | None) -> str | None:
    if re.search(r"\b(price|cost|fee|charges?|how much)\b", low):
        return "inquiry"
    if re.search(r"\bcancel", low):
        return "cancel"
    if re.search(r"\b(reschedule|change|move|postpone|shift)\b", low):
        return "reschedule"
    if re.search(r"\b(book|appointment|schedule|visit|slot|consult\w*)\b", low) and current not in ("reschedule", "cancel"):
        return "book"
    if re.search(r"\b(hours|open|close[sd]?|price|cost|fee|address|where|located|location|parking|pay|payment|services?)\b", low):
        return "inquiry"
    return None


class MockLLM:
    name, is_mock = "mock", True

    async def chat(self, messages: list[LLMMessage], tools: list[ToolSchema]) -> LLMResponse:
        sys = messages[0].content or ""
        state = json.loads(re.search(r"^SESSION STATE: (.*)$", sys, re.M).group(1))
        today = date.fromisoformat(re.search(r"^TODAY: (\d{4}-\d{2}-\d{2})", sys, re.M).group(1))
        services = json.loads(re.search(r"^SERVICES: (.*)$", sys, re.M).group(1))
        idx = max(i for i, m in enumerate(messages) if m.role == "user")
        user, turn = messages[idx].content or "", messages[idx + 1:]
        last_agent = next((m.content for m in reversed(messages[:idx]) if m.role == "assistant" and m.content), "")
        did = self._results(turn)
        eff = dict(state)
        for name, args, result in did:                       # fold this turn's tool activity into the working state
            if name == "update_session":
                eff.update(args)
            elif name == "get_customer":
                eff.update({"customer_id": "known", "customer_name": result["name"], "upcoming": result["upcoming_appointments"]} if result.get("found") else {})
            elif name == "create_customer" and "customer_id" in result:
                eff.update(customer_id=result["customer_id"])
        names = [n for n, _, _ in did]
        if not did:
            slots = extract_slots(user, today, services, last_agent)
            if "which one" in last_agent.lower():
                slots.pop("requested_date", None)
            intent = detect_intent(user.lower(), state.get("intent"))
            changes = {k: v for k, v in {**slots, "intent": intent}.items() if v and state.get(k) != v}
            if changes:
                return _call("update_session", **changes)
            return self._decide(user, eff, services, names)
        _, _, last = did[-1]
        if last.get("error"):
            return _say(f"Sorry, {last['message']}. Would you like to try something else?")
        return self._after(user, eff, services, names, did[-1][0], last)

    @staticmethod
    def _results(turn: list[LLMMessage]) -> list[tuple[str, dict, dict]]:
        calls = {c.id: c for m in turn if m.role == "assistant" for c in (m.tool_calls or [])}
        return [(m.name, calls[m.tool_call_id].arguments, json.loads(m.content)) for m in turn if m.role == "tool"]

    def _after(self, user, eff, services, names, tool, r):
        if tool == "check_availability":
            if not r.get("open_day"):
                return _say(f"Sorry, we're closed on {r['weekday']}s. Is there another day that works for you?")
            if r.get("preferred_time_available"):
                if eff.get("intent") == "reschedule":
                    return _call("reschedule_appointment", appointment_id=eff["appointment_id"], new_date=r["date"], new_time=r["preferred_time"])
                return _call("book_appointment", service=r["service"], date=r["date"], time=r["preferred_time"])
            times = r["nearest_to_preferred"] or r["slots"][:3]
            if not times:
                return _say(f"I don't have any openings on {r['weekday']}. Would another day work?")
            return _say(f"Sorry, that time isn't available. On {r['weekday']} I have " + ", ".join(map(_spoken_hhmm, times[:-1])) +
                        (" or " if len(times) > 1 else "") + _spoken_hhmm(times[-1]) + ". Which would you like?")
        if tool == "get_business_information":
            return _say(self._info(user.lower(), r))
        if tool == "get_customer" and r.get("found") and eff.get("intent") in ("reschedule", "cancel") and not eff.get("appointment_id"):
            upcoming = r["upcoming_appointments"]
            if not upcoming:
                return _say("I don't see any upcoming appointments for you.")
            pick = upcoming if len(upcoming) == 1 else [a for a in upcoming if any(w in user.lower() and w in a["when"].lower() for w in WEEKDAYS)]
            if len(pick) == 1:
                return _call("update_session", appointment_id=pick[0]["appointment_id"], service=pick[0]["service"])
            return _say("You have " + "; ".join(a["when"] for a in upcoming) + ". Which one do you mean?")
        return self._decide(user, eff, services, names)

    def _decide(self, user, eff, services, names):
        intent = eff.get("intent")
        if intent in (None, "other"):
            return _say(HELP)
        if intent == "inquiry":
            return _call("get_business_information", topic=user)
        if not eff.get("customer_id"):
            if not eff.get("phone_number"):
                return _say("Sure, I can help with that. May I have your phone number, please?")
            if "get_customer" not in names:
                return _call("get_customer", phone_number=eff["phone_number"])
            if not eff.get("customer_name"):
                return _say("I don't see that number yet. May I have your name, please?")
            return _call("create_customer", name=eff["customer_name"], phone_number=eff["phone_number"])
        if intent in ("reschedule", "cancel") and not eff.get("appointment_id"):
            return _call("get_customer", phone_number=eff["phone_number"])
        if intent == "cancel":
            return _call("cancel_appointment", appointment_id=eff["appointment_id"])
        service = eff.get("service") or (services[0] if len(services) == 1 else None)
        if not service:
            return _say("Which service would you like: " + ", ".join(services[:-1]) + f" or {services[-1]}?")
        if not eff.get("requested_date"):
            return _say("What day would you like to come in?")
        if not eff.get("requested_time"):
            return _say("What time would you prefer?")
        return _call("check_availability", date=eff["requested_date"], service=service, preferred_time=eff["requested_time"])

    @staticmethod
    def _info(low: str, info: dict) -> str:
        if re.search(r"price|cost|fee|charge", low):
            return "I don't have pricing information. Would you like me to connect you with a human receptionist?"
        for f in info["faq"]:
            if f["topic"] in low:
                return f["answer"]
        if re.search(r"hours|open|close", low):
            return f"Our hours are: {info['hours'].replace(';', ',')}."
        if re.search(r"address|where|located|location", low):
            return f"We're at {info['address']}."
        if re.search(r"service|offer", low):
            return "We offer " + ", ".join(f"{s['name']} ({s['duration_min']} minutes)" for s in info["services"]) + "."
        return f"You've reached {info['name']}. How can I help you?"
