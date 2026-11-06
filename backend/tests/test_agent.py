import asyncio

import pytest

from app.agent import TOOL_FAILURE, UNVERIFIED_REPLY
from app.calendars import CalendarError
from app.llm import LLMError, LLMResponse, ToolCallRequest
from app.voice import SpeechError
from tests.conftest import TOMORROW

ALL_DETAILS = "I'd like a general consultation tomorrow at 4 pm, my name is Asha, my number is 9123456780"


class ScriptedLLM:
    name, is_mock = "scripted", False

    def __init__(self, responder):
        self.responder, self.calls = responder, 0

    async def chat(self, messages, tools):
        self.calls += 1
        return self.responder(self.calls, messages)


class BrokenCalendar:
    name, is_mock = "broken", False

    async def get_availability(self, *a, **k):
        raise CalendarError("calendar unreachable")

    async def create_event(self, *a, **k):
        raise CalendarError("calendar unreachable")

    async def update_event(self, *a, **k):
        raise CalendarError("calendar unreachable")

    async def delete_event(self, *a, **k):
        raise CalendarError("calendar unreachable")


async def make_customer_with_appointment(phone, day=TOMORROW, hhmm="10:00"):
    from datetime import date
    customer, _ = await phone.svc.booking.create_customer("Priya Sharma", "9876543210")
    appt = await phone.svc.booking.book(customer["id"], "General consultation", date.fromisoformat(day), hhmm)
    return customer, appt


# ---------------------------------------------------------------- the booking conversation
async def test_booking_conversation_combines_details_across_turns_and_confirms_before_booking(make_phone):
    p = await make_phone()
    assert "Sunrise Family Clinic" in p.rec.events[1]["text"]                                  # greeting
    assert "phone number" in await p.say("Hi, I need an appointment tomorrow.")
    assert "your name" in await p.say("My number is 9876543210")
    assert "Which service" in await p.say("My name is Priya Sharma")
    assert "What time" in await p.say("General consultation")
    readback = await p.say("Around 4")                                                         # tomorrow + 4 PM, not a new request
    assert readback == "Shall I book a General consultation on Thursday, 8 October at 4:00 PM for Priya Sharma? Please say yes or no."
    assert (p.state.requested_date, p.state.requested_time) == (TOMORROW, "16:00")
    assert await p.appointments() == []                                                        # nothing is booked until the caller says yes
    assert await p.svc.db.select("calendar_events") == []

    spoken = await p.say("Yes")
    assert spoken == "You're all set. Your General consultation is booked for Thursday, 8 October at 4:00 PM."
    [appt] = await p.appointments()
    assert appt["status"] == "booked" and appt["session_id"] == p.call.id and appt["customer_id"] == p.state.customer_id
    assert len(await p.svc.db.select("calendar_events")) == 1
    assert p.state.confirmed and p.state.appointment_id == appt["id"] and p.state.intent == "book"

    tools = {r["name"]: r for r in await p.tool_rows()}
    assert tools["check_availability"]["result"]["preferred_time_available"] is True
    assert tools["book_appointment"]["status"] == "ok"
    assert tools["send_confirmation"]["result"]["delivered"] is False                          # mock notifier: reported honestly
    assert "confirmation text" not in spoken                                                   # ...and the caller is not told one was sent
    assert any(r["status"] == "pending_confirmation" for r in await p.tool_rows())


async def test_unavailable_time_offers_real_alternatives_and_books_nothing(make_phone):
    p = await make_phone()
    other, _ = await p.svc.booking.create_customer("Ravi", "9000000001")
    from datetime import date
    await p.svc.booking.book(other["id"], "General consultation", date.fromisoformat(TOMORROW), "16:00")
    reply = await p.say(ALL_DETAILS)
    assert "isn't available" in reply and "3:30 PM" in reply and "4:30 PM" in reply and "4:00 PM" not in reply
    assert len(await p.appointments()) == 1                                                    # only the pre-existing one


async def test_closed_day_is_reported_from_the_calendar_not_invented(make_phone):
    p = await make_phone()
    reply = await p.say("I want a general consultation on 2026-10-11 at 10 am, name is Asha, number 9123456780")
    assert "closed on Sundays" in reply and await p.appointments() == []


@pytest.mark.parametrize("answer", ["no", "what are your opening hours", "yes but make it 5 pm"])
async def test_only_a_plain_yes_books(make_phone, answer):
    p = await make_phone()
    assert "Shall I book" in await p.say(ALL_DETAILS)
    await p.say(answer)
    assert await p.appointments() == []


async def test_confirmation_can_be_switched_off(make_phone):
    p = await make_phone(settings_kw={"require_confirmation": False})
    assert "booked for Thursday, 8 October at 4:00 PM" in await p.say(ALL_DETAILS)
    assert len(await p.appointments()) == 1


async def test_cancel_flow(make_phone):
    p = await make_phone()
    _, appt = await make_customer_with_appointment(p)
    assert await p.say("I need to cancel my appointment, my number is 9876543210") == \
        "Shall I cancel your General consultation on Thursday, 8 October at 10:00 AM? Please say yes or no."
    assert (await p.svc.booking.get(appt["id"]))["status"] == "booked"                         # still booked until confirmed
    assert await p.say("yes") == "Okay, your General consultation on Thursday, 8 October at 10:00 AM has been cancelled."
    assert (await p.svc.booking.get(appt["id"]))["status"] == "cancelled" and await p.svc.db.select("calendar_events") == []


async def test_reschedule_flow(make_phone):
    p = await make_phone()
    _, appt = await make_customer_with_appointment(p)
    reply = await p.say("I want to reschedule my appointment to 2026-10-10 at 11 am, my number is 9876543210")
    assert reply == "Shall I move your General consultation from Thursday, 8 October at 10:00 AM to Saturday, 10 October at 11:00 AM? Please say yes or no."
    assert await p.say("yes") == "Done. Your General consultation is now on Saturday, 10 October at 11:00 AM."
    moved = await p.svc.booking.get(appt["id"])
    assert moved["starts_at"] == "2026-10-10T05:30:00Z" and len(await p.svc.db.select("calendar_events")) == 1


async def test_cannot_touch_another_callers_appointment(make_phone):
    from app.tools import ToolContext, ToolError
    p = await make_phone()
    _, appt = await make_customer_with_appointment(p)
    stranger, _ = await p.svc.booking.create_customer("Stranger", "9555555555")
    p.state.customer_id, p.state.customer_name = stranger["id"], "Stranger"
    ctx = ToolContext(p.state, p.svc.booking, p.svc.business, p.svc.db, p.svc.notifier)
    with pytest.raises(ToolError, match="no appointment"):
        await p.svc.agent.tools["cancel_appointment"].prepare(ctx, {"appointment_id": appt["id"]})


async def test_business_questions_come_from_the_business_config(make_phone):
    p = await make_phone()
    assert "09:00 to 17:00" in await p.say("What are your opening hours?")
    assert "Free parking" in await p.say("Is there parking?")
    assert "12 Garden Road" in await p.say("Where are you located?")
    assert "don't have pricing" in await p.say("How much does a consultation cost?")        # never invents a price


# ---------------------------------------------------------------- hand-off
async def test_human_request_hands_off_and_the_ai_stops_answering(make_phone):
    p = await make_phone()
    assert await p.say("I want to talk to a real person") == "Of course. I'll connect you with a human receptionist now."
    assert p.events("handoff")[0]["message"] == "Human handoff requested."
    row = await p.svc.db.one("call_sessions", id=p.call.id)
    assert row["status"] == "transferred" and row["handoff_requested"] == 1 and row["handoff_reason"] == "caller asked for a human"
    assert await p.say("hello? are you there") is None and await p.say("anyone?") is None
    assert [m["role"] for m in await p.svc.db.select("messages", order="seq", session_id=p.call.id)].count("system") == 1   # notice once
    assert (await p.tool_rows())[0]["name"] == "transfer_to_human"


@pytest.mark.parametrize("text,reply_part,reason_part", [("This is useless, you are not helping", "human receptionist", "frustrated"),
                                                          ("My father has chest pain", "emergency number", "sensitive")])
async def test_frustration_and_sensitive_situations_hand_off(make_phone, text, reply_part, reason_part):
    p = await make_phone()
    assert reply_part in await p.say(text)
    assert reason_part in p.state.handoff_reason


async def test_calendar_outage_gives_the_fixed_message_and_hands_off(make_phone):
    p = await make_phone(calendar=BrokenCalendar())
    assert await p.say(ALL_DETAILS) == TOOL_FAILURE == "I’m having trouble accessing the appointment system. Let me connect you with a human receptionist."
    assert p.state.handoff_requested and "tool failure" in p.state.handoff_reason
    failed = [r for r in await p.tool_rows() if r["status"] == "failed"]
    assert failed and failed[0]["name"] == "check_availability" and "calendar unreachable" in failed[0]["result"]["message"]
    assert await p.appointments() == []


async def test_outage_at_confirmation_time_does_not_claim_success(make_phone):
    p = await make_phone()
    assert "Shall I book" in await p.say(ALL_DETAILS)
    p.svc.booking.calendar = BrokenCalendar()                                                  # calendar dies between read-back and "yes"
    assert await p.say("yes") == TOOL_FAILURE
    assert await p.appointments() == []


async def test_llm_outage_hands_off(make_phone):
    class Down:
        name, is_mock = "down", False

        async def chat(self, messages, tools):
            raise LLMError("boom")

    p = await make_phone(llm=Down())
    assert "trouble right now" in await p.say("hello")
    assert p.state.handoff_requested


# ---------------------------------------------------------------- the model cannot bypass the rules
async def test_model_text_claiming_a_booking_is_blocked_and_logged(make_phone):
    llm = ScriptedLLM(lambda n, m: LLMResponse("Great news, your appointment is booked for tomorrow at 4 PM!"))
    p = await make_phone(llm=llm)
    assert await p.say("book me tomorrow at 4") == UNVERIFIED_REPLY
    blocked = [r for r in await p.tool_rows() if r["status"] == "blocked"]
    assert blocked and "booked" in blocked[0]["arguments"]["blocked_reply"] and await p.appointments() == []


async def test_ordinary_unavailable_wording_is_not_blocked(make_phone):
    p = await make_phone(llm=ScriptedLLM(lambda n, m: LLMResponse("Sorry, 4 PM is already booked. Would 5 PM work?")))
    assert await p.say("tomorrow at 4?") == "Sorry, 4 PM is already booked. Would 5 PM work?"


async def test_model_cannot_book_without_a_known_customer_or_a_free_slot(make_phone):
    def responder(n, messages):
        if n == 1:
            return LLMResponse(None, [ToolCallRequest("book_appointment", {"service": "General consultation", "date": TOMORROW, "time": "16:00"})])
        return LLMResponse("I need your phone number first.")
    p = await make_phone(llm=ScriptedLLM(responder))
    assert await p.say("book it") == "I need your phone number first."
    rows = await p.tool_rows()
    assert rows[0]["status"] == "error" and rows[0]["result"]["error"] == "not_identified" and await p.appointments() == []


async def test_repeated_invalid_tool_calls_hand_off(make_phone):
    llm = ScriptedLLM(lambda n, m: LLMResponse(None, [ToolCallRequest("update_session", {"requested_date": "tomorrow"})]))
    p = await make_phone(llm=llm)
    assert await p.say("hello") == TOOL_FAILURE and llm.calls == 3


async def test_two_callers_cannot_take_the_same_slot(make_phone, tmp_path):
    first = await make_phone()
    second = await make_phone(svc=first.svc)
    ra, rb = await asyncio.gather(first.say(ALL_DETAILS), second.say(ALL_DETAILS.replace("Asha", "Meera").replace("9123456780", "9888888888")))
    assert "Shall I book" in ra and "Shall I book" in rb                                       # both were offered the slot
    results = await asyncio.gather(first.say("yes"), second.say("yes"))
    assert sorted(r.startswith("You're all set") for r in results) == [False, True]
    assert len(await first.appointments()) == 1 and len(await first.svc.db.select("calendar_events")) == 1
    assert next(r for r in results if not r.startswith("You're")).startswith("Sorry")


# ---------------------------------------------------------------- voice path
async def test_audio_turns_use_the_stt_provider_and_repeated_silence_hands_off(make_phone):
    p = await make_phone()
    await p.call.handle_audio(b"MOCK:what are your opening hours")                            # MockSTT test hook
    assert "09:00 to 17:00" in [e for e in p.events("message") if e["role"] == "agent"][-1]["text"]
    for _ in range(3):
        await p.call.handle_audio(b"\x00\x01" * 1600)                                           # mock STT hears nothing
    assert p.state.handoff_requested and p.state.handoff_reason == "caller could not be understood"


async def test_stt_failure_twice_hands_off(make_phone):
    class BadSTT:
        name, is_mock = "bad", False

        async def transcribe(self, pcm, rate):
            raise SpeechError("down")

    p = await make_phone(stt=BadSTT())
    await p.call.handle_audio(b"\x00\x01" * 1600)
    assert not p.state.handoff_requested
    await p.call.handle_audio(b"\x00\x01" * 1600)
    assert p.state.handoff_requested


async def test_overlong_audio_is_refused(make_phone):
    p = await make_phone()
    await p.call.handle_audio(b"\x00\x01" * (16000 * 31))
    assert "shorter" in [e for e in p.events("message")][-1]["text"] and not p.state.handoff_requested
