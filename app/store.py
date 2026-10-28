"""Persistence for the multi-user deployment: accounts, login sessions, and per-user conversation history.

SQLite via the standard library (no extra dependency). The database file lives in ``ZAKAT_DATA_DIR``
(default ``./data``); on Fly.io that directory is a mounted volume so history survives deploys.
Passwords are hashed with scrypt (hashlib) and a per-user salt; login sessions are random tokens kept
in an HttpOnly cookie and stored server-side so they can be revoked.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    office TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    clone TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).hex()


@dataclass
class User:
    id: int
    email: str
    name: str
    role: str
    office: str | None
    created_at: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role, "office": self.office, "created_at": self.created_at}


class Store:
    def __init__(self, path: str | Path | None = None):
        data_dir = Path(os.environ.get("ZAKAT_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
        self.path = Path(path) if path else data_dir / "zakat.db"
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self.admin_emails = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

    # ------------------------------------------------------------------ users
    def _row_user(self, row: sqlite3.Row | None) -> User | None:
        if row is None:
            return None
        return User(row["id"], row["email"], row["name"], row["role"], row["office"], row["created_at"])

    def user_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, email: str, name: str, password: str, office: str | None = None) -> User:
        email = email.strip().lower()
        salt = secrets.token_hex(16)
        # the very first account and any ADMIN_EMAILS address become admins
        role = "admin" if (self.user_count() == 0 or email in self.admin_emails) else "user"
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO users(email, name, password_hash, salt, role, office, created_at, last_login_at) VALUES (?,?,?,?,?,?,?,?)",
                (email, name.strip(), hash_password(password, salt), salt, role, (office or "").strip() or None, _now(), _now()),
            )
            self._conn.commit()
        return self.get_user(cur.lastrowid)  # type: ignore[return-value]

    def get_user(self, user_id: int) -> User | None:
        return self._row_user(self._conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def get_user_by_email(self, email: str) -> User | None:
        return self._row_user(self._conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone())

    def verify_password(self, email: str, password: str) -> User | None:
        row = self._conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if row is None:
            hash_password(password, secrets.token_hex(16))  # constant-time-ish: always do the work
            return None
        if not hmac.compare_digest(hash_password(password, row["salt"]), row["password_hash"]):
            return None
        with self._lock:
            self._conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), row["id"]))
            self._conn.commit()
        return self._row_user(row)

    def change_password(self, user_id: int, new_password: str) -> None:
        salt = secrets.token_hex(16)
        with self._lock:
            self._conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (hash_password(new_password, salt), salt, user_id))
            self._conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._conn.commit()

    def list_users(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM conversations c WHERE c.user_id=u.id) AS conversations
               FROM users u ORDER BY u.created_at"""
        ).fetchall()
        return [{**self._row_user(r).to_dict(), "last_login_at": r["last_login_at"], "conversations": r["conversations"]} for r in rows]  # type: ignore[union-attr]

    def set_role(self, user_id: int, role: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
            self._conn.commit()

    def delete_user(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id=?)", (user_id,))
            self._conn.execute("DELETE FROM conversations WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            self._conn.commit()

    # --------------------------------------------------------------- sessions
    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute("INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES (?,?,?,?)", (token, user_id, _now(), expires))
            self._conn.commit()
        return token

    def user_for_session(self, token: str | None) -> User | None:
        if not token:
            return None
        row = self._conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at > ?", (token, _now())
        ).fetchone()
        return self._row_user(row)

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            self._conn.commit()

    # ---------------------------------------------------------- conversations
    def create_conversation(self, user_id: int, clone: str, title: str) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations(user_id, clone, title, created_at, updated_at) VALUES (?,?,?,?,?)",
                (user_id, clone, title.strip()[:120] or "محادثة", now, now),
            )
            self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def conversation_owner(self, conversation_id: int) -> int | None:
        row = self._conn.execute("SELECT user_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return row["user_id"] if row else None

    def add_message(self, conversation_id: int, role: str, content: dict) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                (conversation_id, role, json.dumps(content, ensure_ascii=False), now),
            )
            self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
            self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def list_conversations(self, user_id: int, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS messages
               FROM conversations c WHERE c.user_id=? ORDER BY c.updated_at DESC, c.id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: int, user_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)).fetchone()
        if row is None:
            return None
        msgs = self._conn.execute("SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
        return {**dict(row), "messages": [{"id": m["id"], "role": m["role"], "content": json.loads(m["content"]), "created_at": m["created_at"]} for m in msgs]}

    def rename_conversation(self, conversation_id: int, user_id: int, title: str) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE conversations SET title=? WHERE id=? AND user_id=?", (title.strip()[:120] or "محادثة", conversation_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete_conversation(self, conversation_id: int, user_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0

    def search_messages(self, user_id: int, query: str, limit: int = 30) -> list[dict]:
        q = f"%{query.strip()}%"
        rows = self._conn.execute(
            """SELECT m.conversation_id, c.title, c.clone, m.content, m.created_at FROM messages m
               JOIN conversations c ON c.id=m.conversation_id
               WHERE c.user_id=? AND m.role='user' AND m.content LIKE ? ORDER BY m.id DESC LIMIT ?""",
            (user_id, q, limit),
        ).fetchall()
        return [{"conversation_id": r["conversation_id"], "title": r["title"], "clone": r["clone"], "text": json.loads(r["content"]).get("text", ""), "created_at": r["created_at"]} for r in rows]

    def stats(self) -> dict:
        c = self._conn
        return {
            "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "conversations": c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
            "messages": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "questions_by_clone": {r["clone"]: r["n"] for r in c.execute("SELECT clone, COUNT(*) AS n FROM conversations GROUP BY clone")},
            "active_sessions": c.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (_now(),)).fetchone()[0],
        }


    # ------------------------------------------------------------- admin analytics
    def analytics(self, days: int = 30) -> dict:
        """Usage figures for the admin dashboard; derived from saved messages only."""
        c = self._conn
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        per_day = {
            r["d"]: r["n"]
            for r in c.execute(
                "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM messages WHERE role='user' AND created_at>=? GROUP BY d ORDER BY d",
                (since,),
            )
        }
        users_per_day = {
            r["d"]: r["n"]
            for r in c.execute(
                "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM users WHERE created_at>=? GROUP BY d ORDER BY d",
                (since,),
            )
        }
        by_clone = {
            r["clone"]: r["n"]
            for r in c.execute(
                "SELECT c.clone, COUNT(*) AS n FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.role='user' GROUP BY c.clone"
            )
        }
        by_stage = {
            (r["stage"] or "answer"): r["n"]
            for r in c.execute(
                "SELECT json_extract(content,'$.stage') AS stage, COUNT(*) AS n FROM messages WHERE role='assistant' GROUP BY stage"
            )
        }
        by_office = [
            {"office": r["office"] or "—", "users": r["u"], "questions": r["q"]}
            for r in c.execute(
                """SELECT u.office, COUNT(DISTINCT u.id) AS u,
                          (SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id
                           WHERE m.role='user' AND c.user_id IN (SELECT id FROM users x WHERE COALESCE(x.office,'')=COALESCE(u.office,''))) AS q
                   FROM users u GROUP BY COALESCE(u.office,'') ORDER BY q DESC"""
            )
        ]
        top_users = [
            {"id": r["id"], "name": r["name"], "email": r["email"], "office": r["office"], "questions": r["q"], "last_login_at": r["last_login_at"]}
            for r in c.execute(
                """SELECT u.id, u.name, u.email, u.office, u.last_login_at,
                          (SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.role='user' AND c.user_id=u.id) AS q
                   FROM users u ORDER BY q DESC, u.created_at LIMIT 10"""
            )
        ]
        active = lambda d: c.execute(  # noqa: E731
            "SELECT COUNT(DISTINCT c.user_id) FROM messages m JOIN conversations c ON c.id=m.conversation_id JOIN users u ON u.id=c.user_id WHERE m.role='user' AND m.created_at>=?",
            ((datetime.now(timezone.utc) - timedelta(days=d)).isoformat(timespec="seconds"),),
        ).fetchone()[0]
        total_q = c.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0]
        return {
            "days": days,
            "questions_total": total_q,
            "questions_per_day": per_day,
            "new_users_per_day": users_per_day,
            "questions_by_clone": by_clone,
            "answers_by_stage": by_stage,
            "by_office": by_office,
            "top_users": top_users,
            "active_users_7d": active(7),
            "active_users_30d": active(30),
            **self.stats(),
        }

    def activity(self, limit: int = 100, user_id: int | None = None, query: str | None = None) -> list[dict]:
        """Latest questions with who asked them and how the system answered (stage)."""
        sql = """SELECT m.id, m.created_at, m.conversation_id, c.clone, c.title,
                        json_extract(m.content,'$.text') AS text,
                        u.id AS user_id, u.name, u.email, u.office,
                        COALESCE((SELECT json_extract(a.content,'$.stage') FROM messages a
                          WHERE a.conversation_id=m.conversation_id AND a.role='assistant' AND a.id>m.id ORDER BY a.id LIMIT 1), 'answer') AS stage
                 FROM messages m JOIN conversations c ON c.id=m.conversation_id JOIN users u ON u.id=c.user_id
                 WHERE m.role='user'"""
        args: list = []
        if user_id is not None:
            sql += " AND u.id=?"
            args.append(user_id)
        if query:
            sql += " AND (json_extract(m.content,'$.text') LIKE ? OR u.email LIKE ? OR u.name LIKE ?)"
            args += [f"%{query}%"] * 3
        sql += " ORDER BY m.id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._conn.execute(sql, args)]

    def export_rows(self) -> list[dict]:
        return self.activity(limit=1_000_000)[::-1]


class RateLimiter:
    """Fixed-window in-memory limiter keyed by user id or client IP (per process; enough for one small VM)."""

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        bucket = int(time.time() // self.window)
        with self._lock:
            b, n = self._hits.get(key, (bucket, 0))
            if b != bucket:
                n = 0
            n += 1
            self._hits[key] = (bucket, n)
            if len(self._hits) > 5000:
                self._hits = {k: v for k, v in self._hits.items() if v[0] == bucket}
        return n <= self.limit
