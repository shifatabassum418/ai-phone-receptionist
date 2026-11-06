import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import TOMORROW

BOOK_ALL = "I'd like a general consultation tomorrow at 4 pm, my name is Asha, my number is 9123456780"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / "api.db"), business_config="business.json"))
    with TestClient(app) as c:
        yield c


def events_until(ws, stop, limit=60):
    out = []
    for _ in range(limit):
        msg = ws.receive()
        event = {"type": "<audio bytes>"} if msg.get("bytes") is not None else json.loads(msg["text"])
        out.append(event)
        if stop(event):
            return out
    raise AssertionError(f"expected event never arrived: {[e['type'] for e in out]}")


def open_call(client):
    session = client.post("/api/calls/session").json()
    ws = client.websocket_connect(session["stream_url"])
    return session["session_id"], ws


def test_health_marks_mock_mode_and_lists_business_info(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["mock_mode"] is True
    assert {k: v["mode"] for k, v in body["components"].items()} == {"stt": "mock", "llm": "mock", "tts": "mock", "calendar": "mock", "notifier": "mock"}
    assert body["business"]["name"] == "Sunrise Family Clinic" and body["business"]["hours"]["sun"] is None
    assert [s["name"] for s in body["business"]["services"]][0] == "General consultation"


def test_live_call_over_websocket_books_only_after_yes_and_is_fully_logged(client):
    session_id, cm = open_call(client)
    with cm as ws:
        ready = events_until(ws, lambda e: e["type"] == "session.state")
        assert ready[0]["type"] == "session.ready" and ready[0]["mock_mode"] is True
        assert "Sunrise Family Clinic" in ready[1]["text"] and ready[1]["voice"] == "browser"          # mock TTS: the browser speaks it

        ws.send_json({"type": "text", "text": "What are your opening hours?"})
        got = events_until(ws, lambda e: e["type"] == "session.state")
        assert [e["name"] for e in got if e["type"] == "tool.call"] == ["update_session", "get_business_information"]
        assert "09:00 to 17:00" in next(e["text"] for e in got if e["type"] == "message" and e["role"] == "agent")

        ws.send_json({"type": "text", "text": BOOK_ALL})
        got = events_until(ws, lambda e: e["type"] == "session.state")
        state = got[-1]["state"]
        assert state["intent"] == "book" and state["requested_date"] == TOMORROW and state["requested_time"] == "16:00"
        assert state["awaiting_confirmation"] and got[-1]["appointment"] is None
        assert client.get("/api/appointments").json() == []

        ws.send_json({"type": "text", "text": "yes"})
        got = events_until(ws, lambda e: e["type"] == "session.state")
        assert "booked for Thursday, 8 October at 4:00 PM" in next(e["text"] for e in got if e["type"] == "message" and e["role"] == "agent")
        assert got[-1]["appointment"]["status"] == "booked" and got[-1]["state"]["confirmed"] is True
        ws.send_json({"type": "hangup"})
        end = events_until(ws, lambda e: e["type"] == "call.ended")[-1]
        assert end["status"] == "ended"

    [appt] = client.get("/api/appointments").json()
    assert appt["customer_name"] == "Asha" and appt["status"] == "booked" and appt["session_id"] == session_id
    call = client.get(f"/api/calls/{session_id}").json()
    assert call["status"] == "ended" and call["customer_name"] == "Asha" and call["appointments"][0]["id"] == appt["id"]
    assert [m["role"] for m in call["messages"]][:3] == ["agent", "caller", "agent"]
    names = [t["name"] for t in call["tool_calls"]]
    assert {"get_business_information", "check_availability", "book_appointment", "send_confirmation"} <= set(names)
    assert next(t for t in call["tool_calls"] if t["status"] == "pending_confirmation")["name"] == "book_appointment"
    listed = client.get("/api/calls").json()[0]
    assert listed["id"] == session_id and listed["appointments_booked"] == 1 and listed["tool_call_count"] == len(names)


def test_voice_frames_go_through_stt(client):
    _, cm = open_call(client)
    with cm as ws:
        events_until(ws, lambda e: e["type"] == "session.state")
        ws.send_bytes(b"MOCK:what are your opening hours")            # MockSTT test hook
        ws.send_json({"type": "audio.end"})
        got = events_until(ws, lambda e: e["type"] == "session.state")
        assert [e["text"] for e in got if e["type"] == "message"][0] == "what are your opening hours"


def test_handoff_endpoint_notifies_the_live_call_and_marks_it_transferred(client):
    session_id, cm = open_call(client)
    with cm as ws:
        events_until(ws, lambda e: e["type"] == "session.state")
        assert client.post("/api/handoff", json={"session_id": session_id, "reason": "owner wants to take over"}).json()["status"] == "transferred"
        handoff = events_until(ws, lambda e: e["type"] == "handoff")[-1]
        assert handoff["message"] == "Human handoff requested." and handoff["reason"] == "owner wants to take over"
        events_until(ws, lambda e: e["type"] == "session.state")
        ws.send_json({"type": "text", "text": "hello?"})
        got = events_until(ws, lambda e: e["type"] == "session.state")
        assert not [e for e in got if e["type"] == "message" and e["role"] == "agent"]                  # the AI stays quiet
        ws.send_json({"type": "hangup"})
        assert events_until(ws, lambda e: e["type"] == "call.ended")[-1]["status"] == "transferred"
    assert client.get(f"/api/calls/{session_id}").json()["handoff_reason"] == "owner wants to take over"
    assert client.get("/api/calls?status=transferred").json()[0]["id"] == session_id


def test_websocket_rejects_unknown_sessions_and_second_listeners(client):
    with client.websocket_connect("/api/calls/nope/stream") as ws:
        assert ws.receive() == {"type": "websocket.close", "code": 4404, "reason": ""}
    session_id, cm = open_call(client)
    with cm as first:
        events_until(first, lambda e: e["type"] == "session.state")
        with client.websocket_connect(f"/api/calls/{session_id}/stream") as second:
            assert second.receive()["code"] == 4409                      # a call has one live listener


def test_handoff_and_call_lookups_404(client):
    assert client.post("/api/handoff", json={"session_id": "nope"}).status_code == 404
    assert client.get("/api/calls/nope").status_code == 404


def test_appointment_crud_enforces_the_calendar(client):
    body = {"customer_name": "Priya Sharma", "phone_number": "+91 98765 43210", "service": "General consultation", "date": TOMORROW, "time": "10:00"}
    created = client.post("/api/appointments", json=body)
    assert created.status_code == 201 and created.json()["starts_at"] == "2026-10-08T04:30:00Z" and created.json()["customer_name"] == "Priya Sharma"
    appt_id = created.json()["id"]

    assert client.post("/api/appointments", json={**body, "customer_name": "Someone Else", "phone_number": "9000000009"}).status_code == 409   # slot taken
    assert client.post("/api/appointments", json={**body, "date": "2026-10-11"}).status_code == 409                                            # Sunday
    assert client.post("/api/appointments", json={**body, "service": "Massage"}).status_code == 422
    assert client.post("/api/appointments", json={**body, "date": "10/08/2026"}).status_code == 422
    assert client.post("/api/appointments", json={**body, "date": "2026-02-31"}).status_code == 422
    assert client.post("/api/appointments", json={**body, "phone_number": "abc12345"}).status_code == 422

    moved = client.put(f"/api/appointments/{appt_id}", json={"date": TOMORROW, "time": "11:00"})
    assert moved.status_code == 200 and moved.json()["starts_at"] == "2026-10-08T05:30:00Z"
    assert client.put("/api/appointments/nope", json={"date": TOMORROW, "time": "11:00"}).status_code == 404
    assert client.post("/api/appointments", json={**body, "customer_name": "Ravi", "phone_number": "9000000009"}).status_code == 201           # 10:00 is free again

    assert client.delete(f"/api/appointments/{appt_id}").status_code == 204
    assert client.delete(f"/api/appointments/{appt_id}").status_code == 409
    assert [a["status"] for a in client.get("/api/appointments?status=cancelled").json()] == ["cancelled"]
    assert len(client.get("/api/appointments?status=booked&upcoming=true").json()) == 1


def test_customers_list_counts_appointments(client):
    base = {"service": "General consultation", "date": TOMORROW, "phone_number": "9123456780", "customer_name": "Asha"}
    client.post("/api/appointments", json={**base, "time": "09:00"})
    client.post("/api/appointments", json={**base, "time": "09:30"})
    [asha] = client.get("/api/customers").json()
    assert asha["name"] == "Asha" and asha["appointment_count"] == 2 and asha["upcoming_count"] == 2
