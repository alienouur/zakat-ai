"""Conversation persistence: one SQLite row per session; the message list is append-only."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionStore:
    def __init__(self, data_dir: Path, retention_days: int = 30):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(data_dir / "sessions.sqlite", check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, messages TEXT NOT NULL, "
            "questions TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.commit()
        self.purge_older_than(retention_days)

    def purge_older_than(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def create(self) -> str:
        sid = str(uuid.uuid4())
        now = _now()
        with self._lock:
            self._conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (sid, "[]", "[]", now, now))
            self._conn.commit()
        return sid

    def get(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT session_id, messages, questions, created_at, updated_at FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return {"session_id": row[0], "messages": json.loads(row[1]), "questions": json.loads(row[2]),
                "created_at": row[3], "updated_at": row[4]}

    def get_or_create(self, session_id: str | None) -> dict:
        if session_id:
            s = self.get(session_id)
            if s:
                return s
        return self.get(self.create())  # type: ignore[return-value]

    def append(self, session_id: str, new_messages: list[dict], question: str | None = None) -> None:
        """Appends only: existing messages are never modified or removed."""
        with self._lock:
            row = self._conn.execute("SELECT messages, questions FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if not row:
                raise KeyError(session_id)
            messages = json.loads(row[0]) + list(new_messages)
            questions = json.loads(row[1]) + ([question] if question else [])
            self._conn.execute(
                "UPDATE sessions SET messages=?, questions=?, updated_at=? WHERE session_id=?",
                (json.dumps(messages, ensure_ascii=False), json.dumps(questions, ensure_ascii=False), _now(), session_id),
            )
            self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
