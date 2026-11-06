"""Thin async SQLite layer. Table and column names come from code, never from user input."""
import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_events(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_sessions(
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL, components TEXT NOT NULL, state TEXT,
  intent TEXT, customer_id TEXT, handoff_requested INTEGER DEFAULT 0, handoff_reason TEXT);
CREATE TABLE IF NOT EXISTS appointments(
  id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), service TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  status TEXT NOT NULL, calendar_event_id TEXT NOT NULL, session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  session_id TEXT NOT NULL REFERENCES call_sessions(id), seq INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(session_id, seq));
CREATE TABLE IF NOT EXISTS tool_calls(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES call_sessions(id), turn INTEGER NOT NULL, name TEXT NOT NULL, arguments TEXT, result TEXT,
  status TEXT NOT NULL, duration_ms INTEGER, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_appt_start ON appointments(starts_at);
CREATE INDEX IF NOT EXISTS ix_event_start ON calendar_events(starts_at);
CREATE INDEX IF NOT EXISTS ix_tool_session ON tool_calls(session_id);
"""
JSON_COLUMNS = {"components", "state", "arguments", "result"}


def _decode(row: aiosqlite.Row) -> dict[str, Any]:
    return {k: json.loads(row[k]) if k in JSON_COLUMNS and row[k] is not None else row[k] for k in row.keys()}


def _encode(values: dict[str, Any]) -> dict[str, Any]:
    return {k: json.dumps(v) if k in JSON_COLUMNS else v for k, v in values.items()}


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def execute(self, sql: str, params: tuple | list = ()) -> int:
        """Runs one statement atomically and returns the number of rows changed."""
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur.rowcount

    async def raw(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        async with self._conn.execute(sql, params) as cur:
            return [_decode(r) for r in await cur.fetchall()]

    async def insert(self, table: str, **values: Any) -> None:
        values = _encode(values)
        await self.execute(f"INSERT INTO {table}({', '.join(values)}) VALUES({', '.join('?' for _ in values)})", list(values.values()))

    async def update(self, table: str, where: dict[str, Any], **values: Any) -> None:
        values = _encode(values)
        await self.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in values)} WHERE {' AND '.join(f'{k}=?' for k in where)}",
                           [*values.values(), *where.values()])

    async def select(self, table: str, order: str | None = None, **where: Any) -> list[dict[str, Any]]:
        cond = " AND ".join(f"{k}=?" for k in where)
        return await self.raw(f"SELECT * FROM {table}" + (f" WHERE {cond}" if cond else "") + (f" ORDER BY {order}" if order else ""), list(where.values()))

    async def one(self, table: str, **where: Any) -> dict[str, Any] | None:
        rows = await self.select(table, **where)
        return rows[0] if rows else None
