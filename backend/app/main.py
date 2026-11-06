import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app import timeutil
from app.api import router
from app.calendars import CalendarError
from app.config import Settings, get_settings
from app.db import Database
from app.services import build_services


def create_app(settings: Settings | None = None, **overrides) -> FastAPI:
    """overrides (llm=, stt=, tts=, calendar=, notifier=, business=) replace individual components; used by tests and for real integrations."""
    s = settings or get_settings()
    s.validate_providers()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(s.database_path)
        await db.connect()
        # calls that were live when the previous process stopped can never finish
        await db.execute("UPDATE call_sessions SET status=CASE WHEN handoff_requested=1 THEN 'transferred' ELSE 'ended' END, ended_at=? WHERE status='active'",
                         (timeutil.iso(timeutil.utcnow()),))
        app.state.services = build_services(s, db, **overrides)
        yield
        await db.close()

    app = FastAPI(title="AI Phone Receptionist", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)

    @app.exception_handler(CalendarError)
    async def calendar_down(_: Request, exc: CalendarError):
        return JSONResponse(status_code=503, content={"detail": "the calendar is unavailable"})

    _serve_dashboard(app)
    return app


def _serve_dashboard(app: FastAPI) -> None:
    """When the built dashboard exists (production / Docker), serve it from this same server so one URL does everything."""
    dist = Path(os.environ.get("WEB_DIST", "../web/dist")).resolve()
    index = dist / "index.html"
    if not index.is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        target = (dist / path).resolve()
        if path and target.is_file() and dist in target.parents:
            return FileResponse(target)
        return FileResponse(index)   # client-side routes like /calls/123 load the app


app = create_app()