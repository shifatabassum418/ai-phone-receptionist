# AI Phone Receptionist

[Watch the demo video](https://drive.google.com/file/d/1opo1VFFgZ675Vzi0T_PXeV952Pc6GANs/view?usp=drivesdk)

[Live app](https://ai-phone-receptionist-k4tq.onrender.com)


An AI receptionist that talks with callers by voice to answer business questions, check availability, book, reschedule and cancel appointments, take customer details, send confirmations and hand over to a human. It keeps conversation state for the whole call, calls real tools instead of pretending to act, and ships with a dark admin dashboard.

Everything runs offline in **mock mode** (rule-based model, local calendar, browser voice). Switch each component to a real provider with environment variables. The UI shows a MOCK MODE banner listing exactly what is simulated.

## 1. Architecture

```
 Browser dashboard (React + Vite + Tailwind)
   mic -> VAD -> PCM16 frames ----+        typed text -----+
                                  v                        v
                    WebSocket  /api/calls/{session_id}/stream
                                  |
   FastAPI  --------------------- | -------------------------------- SQLite
     CallSession (one per call)   |                          customers, appointments,
        |                         |                          call_sessions, messages,
   STT -> text -> Receptionist agent -> reply -> TTS -> audio  tool_calls, calendar_events
                       |   ^
        tool calls ----+   +---- tool results
                       v
   +--------------------------------------------------------------+
   | update_session  get_business_information  check_availability |
   | get_customer  create_customer  send_confirmation              |
   | book / reschedule / cancel (read back, then caller says yes)  |
   | transfer_to_human                                             |
   +--------------------------------------------------------------+
            |                          |                    |
      Booking service  ->  CalendarProvider           Notifier
      (rules, locking)     (LocalCalendar | Google)   (mock | SMS)

   Provider interfaces:  STT (mock | openai)   LLM (mock | openai)   TTS (mock | edge)
```

## 2. Project structure

```
ai-phone-receptionist/
  backend/
    business.json          the business: hours, services, FAQ, escalation keywords
    app/
      main.py              app factory
      api.py               REST + WebSocket endpoints
      services.py          wires providers from settings
      calls.py             CallSession: persistence, events, STT/TTS, handoff
      agent.py             Receptionist: guardrails, confirmation, tool loop
      tools.py             the ten tools
      state.py             SessionState
      booking.py           appointment rules (shared by tools and REST API)
      calendars.py         CalendarProvider interface + LocalCalendar
      notify.py            Notifier interface + MockNotifier
      llm.py  mock_llm.py  LLM interface, OpenAI-compatible client, offline mock
      voice.py             STT/TTS interfaces, OpenAI-compatible STT, Edge TTS, mocks
      business.py  config.py  db.py  timeutil.py
    tests/                 40 tests
  web/src/
    App.tsx  api.ts  useCall.ts  audio.ts  ui.tsx  format.ts
    pages/  Dashboard  CallConsole  Calls  CallDetail  Appointments  Customers
```

## 3. Setup

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --port 8000

# dashboard (Node 20+), second terminal
cd web
npm install
npm run dev            # http://localhost:5173/dashboard
```

Open **Live call**, press **Start call**, and type what the caller says, for example "I need an appointment tomorrow", then "my number is 9876543210", and so on.

Edit `backend/business.json` for your business (name, timezone, opening hours, services and durations, FAQ answers, emergency keywords).

## 4. Environment variables (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `STT_PROVIDER` | `mock` | `mock` or `openai` (any server with `/audio/transcriptions`, e.g. Groq Whisper) |
| `LLM_PROVIDER` | `mock` | `mock` or `openai` (any OpenAI-compatible `/chat/completions` with tool calling) |
| `TTS_PROVIDER` | `mock` | `mock` (browser voice) or `edge` (Microsoft Edge neural voices, no key) |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Groq URL, empty, `llama-3.3-70b-versatile` | Required when `LLM_PROVIDER=openai`. The app refuses to start without the key |
| `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL` | empty, empty, `whisper-large-v3-turbo` | STT defaults to the LLM URL and key |
| `TTS_VOICE` | `en-US-JennyNeural` | Voice for `edge` |
| `REQUIRE_CONFIRMATION` | `true` | Read back and wait for "yes" before booking, rescheduling or cancelling |
| `DATABASE_PATH` | `data/receptionist.db` | SQLite file |
| `BUSINESS_CONFIG` | `business.json` | Business definition |
| `CORS_ORIGINS` | `http://localhost:5173` | Allowed browser origins |

Going live with voice: set `STT_PROVIDER=openai`, `LLM_PROVIDER=openai`, `TTS_PROVIDER=edge`, add `LLM_API_KEY`. The mic button then activates. Use headphones, otherwise the receptionist can hear itself.

## 5. Local testing

```bash
cd backend && pip install -r requirements-dev.txt && pytest     # 40 tests, about 1 second
cd web && npm run build                                          # type-check and build
```

Tests drive real conversations through the same `CallSession` the WebSocket uses, against an in-memory database and a frozen clock. They cover: multi-turn slot filling ("tomorrow" then "around 4"), read-back before booking, only a plain "yes" books, unavailable times, closed days, cancel and reschedule, other callers' appointments, double-booking races (two callers and concurrent calendar writes), human handoff (request, frustration, emergency keywords, silence), calendar outage with the exact fallback message, a model that lies about a booking (blocked and logged), repeated invalid tool calls, voice path, and every REST and WebSocket endpoint.

REST examples:

```bash
curl localhost:8000/api/health
curl -X POST localhost:8000/api/appointments -H 'content-type: application/json' \
  -d '{"customer_name":"Priya","phone_number":"9876543210","service":"General consultation","date":"2026-10-12","time":"10:00"}'
curl localhost:8000/api/appointments
```

## 6. WebSocket: `/api/calls/{session_id}/stream`

`POST /api/calls/session` creates a call and returns `session_id` and `stream_url`. Open the WebSocket on `stream_url`. A call has one live listener.

| Client to server | Meaning |
|---|---|
| binary frames | PCM16 mono 16 kHz audio of one utterance |
| `{"type":"audio.end"}` | utterance finished: transcribe and answer |
| `{"type":"text","text":"..."}` | typed caller input (always available; the only input in mock STT mode) |
| `{"type":"interrupt"}` | caller started speaking over the reply: drop pending audio |
| `{"type":"hangup"}` | end the call |

| Server to client | Meaning |
|---|---|
| `session.ready` | components and their mock or live mode |
| `message` | `role` caller, agent or system; agent messages carry `voice`: `server` (audio follows) or `browser` (speak the text locally) |
| `agent.status` | the agent is thinking |
| `tool.call` | one tool call with arguments, result, status, duration |
| `session.state` | full session state plus the current appointment |
| `handoff` | `Human handoff requested.` with the reason |
| `audio` | `format` and `bytes`; the next binary frame is the audio |
| `call.ended` | final status `ended` or `transferred` |

Turns are processed one at a time. A turn that is mid-booking is never cancelled, even if the caller disconnects.

## 7. Voice pipeline

1. The browser opens the mic (echo cancellation on) and an adaptive energy VAD splits speech into utterances.
2. Frames stream as PCM16 over the WebSocket; `audio.end` closes the utterance. Speaking over a reply sends `interrupt` and stops playback.
3. `STTProvider.transcribe` turns audio into text. Silence or unclear audio gets "Sorry, I didn't catch that"; after three misses, or two STT failures, the call is handed to a human.
4. The agent runs (section 8) and returns text.
5. `TTSProvider.synthesize` returns audio sent as an `audio` event plus a binary frame. With the mock TTS (or if TTS fails) the dashboard speaks the text with the browser's voice instead.

Swap any stage by implementing its small interface in `voice.py` / `llm.py` and passing it to `build_services(...)`.

## 8. Tool calling

Each caller turn builds a prompt containing today's date, business hours, services and the current **session state** (intent, name, phone, service, requested date and time, appointment id, confirmed, handoff). The model calls tools; results go back to it until it answers (max 6 steps).

The rules are enforced in code, not by the prompt:

- **Availability** comes only from `check_availability`, and `book`/`reschedule` re-check the calendar when they run.
- **book / reschedule / cancel** are validated first, then read back ("Shall I book ... Please say yes or no."). They run only after a plain "yes" (a longer or numeric reply is a new request). The spoken result is composed from the tool result, so the model never announces an outcome.
- **A confirmation text** is sent after each change through the `Notifier`. The caller is told one was sent only if the notifier reports delivery (the mock notifier never does).
- **Model text that claims** an appointment was booked, changed or cancelled without a tool is replaced and logged as a `guardrail` tool call.
- **Tool outage** (calendar or database error): the caller hears "I’m having trouble accessing the appointment system. Let me connect you with a human receptionist." and the call is transferred. Three invalid tool calls trigger the same.
- **Handoff triggers**: caller asks for a person, frustration, emergency keywords from `business.json`, model decision, tool outage, repeated unclear audio, or the dashboard button / `POST /api/handoff`. After handoff the AI stays silent and the dashboard shows "Human handoff requested."
- Every tool call, including pending confirmations, is stored in `tool_calls`.

## 9. Google Calendar

The calendar is an interface (`calendars.py`): `get_availability`, `create_event`, `update_event`, `delete_event` (getAvailability, createEvent, updateEvent, deleteEvent). `LocalCalendar` is the development implementation. To use Google Calendar:

1. In Google Cloud, enable the **Google Calendar API** and create a **service account**; download its JSON key.
2. In Google Calendar, share the business calendar with the service account's email with permission **Make changes to events**. Note the calendar id.
3. `pip install google-api-python-client google-auth`, add `GOOGLE_SERVICE_ACCOUNT_FILE` and `GOOGLE_CALENDAR_ID` to `.env`.
4. Add an adapter (this sketch is not part of the repository):

```python
# backend/app/google_calendar.py
import asyncio, os
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.calendars import CalendarError, free_slots
from app.timeutil import iso, parse_iso


class GoogleCalendar:
    name, is_mock = "google", False

    def __init__(self, key_file: str, calendar_id: str):
        creds = service_account.Credentials.from_service_account_file(key_file, scopes=["https://www.googleapis.com/auth/calendar"])
        self.api, self.cal = build("calendar", "v3", credentials=creds, cache_discovery=False), calendar_id

    async def _run(self, request):
        try:
            return await asyncio.to_thread(request.execute)
        except HttpError as e:
            raise CalendarError(f"Google Calendar error: {e}") from e

    async def get_availability(self, window_start, window_end, duration_min, step_min, exclude_event_id=None):
        body = {"timeMin": iso(window_start), "timeMax": iso(window_end), "items": [{"id": self.cal}]}
        busy = (await self._run(self.api.freebusy().query(body=body)))["calendars"][self.cal]["busy"]
        intervals = [(parse_iso(b["start"][:19] + "Z"), parse_iso(b["end"][:19] + "Z")) for b in busy]
        return free_slots(intervals, window_start, window_end, duration_min, step_min)
        # to let a reschedule ignore its own event, list events and drop the one whose id == exclude_event_id

    async def create_event(self, title, start, end, description=""):
        body = {"summary": title, "description": description, "start": {"dateTime": iso(start)}, "end": {"dateTime": iso(end)}}
        return (await self._run(self.api.events().insert(calendarId=self.cal, body=body)))["id"]

    async def update_event(self, event_id, start, end):
        body = {"start": {"dateTime": iso(start)}, "end": {"dateTime": iso(end)}}
        await self._run(self.api.events().patch(calendarId=self.cal, eventId=event_id, body=body))

    async def delete_event(self, event_id):
        await self._run(self.api.events().delete(calendarId=self.cal, eventId=event_id))
```

5. Wire it in `app/main.py`: `app = create_app(calendar=GoogleCalendar(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"], os.environ["GOOGLE_CALENDAR_ID"]))`. The calendar is then no longer listed as mock in the UI banner. Nothing else changes: `appointments.calendar_event_id` stores the Google event id, and `Booking` serialises bookings within the process.

## Known limits

- The dashboard has no login: run it on a trusted network.
- Callers are identified by phone number only; a production phone line would also use caller ID.
- Booking is serialised per process and the local calendar is atomic in SQLite; with Google Calendar, run a single server process.
- Audio is raw PCM16 over the WebSocket; this is a browser call console, not a telephone-network (SIP/Twilio) integration.
- The mock model is a small rule-based policy for demos and tests, not a language model.
