"""Database setup for the coder chat app (SPEC §0, §1).

sqlite3, WAL mode, foreign_keys=ON, threading.Lock.  All tables from T01 details.
Times are ISO-8601 UTC strings.
"""
from __future__ import annotations

import json
import time

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import hash_password


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    pw_hash     TEXT NOT NULL,
    rights      TEXT NOT NULL DEFAULT 'chat',
    theme       TEXT NOT NULL DEFAULT 'auto',
    icon        TEXT NOT NULL DEFAULT '',
    created     TEXT NOT NULL,
    last_login  TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id_hash   TEXT PRIMARY KEY,
    username  TEXT NOT NULL REFERENCES users(username),
    created   TEXT NOT NULL,
    expires   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    rights     TEXT NOT NULL,
    note       TEXT,
    created    TEXT NOT NULL,
    expires    TEXT NOT NULL,
    used_by    TEXT,
    deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chats (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT NOT NULL REFERENCES users(username),
    title                 TEXT NOT NULL DEFAULT 'New chat',
    profile               TEXT NOT NULL DEFAULT 'general',
    created               TEXT NOT NULL,
    updated               TEXT NOT NULL,
    last_prompt_tokens    INTEGER DEFAULT NULL,
    last_completion_tokens INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role    TEXT NOT NULL,
    content TEXT NOT NULL,
    created TEXT NOT NULL,
    tokens  INTEGER DEFAULT NULL,
    tps     REAL DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL REFERENCES users(username),
    name     TEXT NOT NULL,
    size     INTEGER NOT NULL,
    content  BLOB NOT NULL,
    created  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_files (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    PRIMARY KEY (message_id, file_id)
);

CREATE TABLE IF NOT EXISTS login_failures (
    key  TEXT NOT NULL,
    at   TEXT NOT NULL,
    PRIMARY KEY (key, at)
);

CREATE TABLE IF NOT EXISTS passkeys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL REFERENCES users(username),
    credential_id BLOB NOT NULL UNIQUE,
    public_key    BLOB NOT NULL,
    sign_count    INTEGER NOT NULL DEFAULT 0,
    name          TEXT NOT NULL,
    created       TEXT NOT NULL,
    last_used     TEXT
);

CREATE TABLE IF NOT EXISTS webauthn_challenges (
    key_hash  TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    username  TEXT,
    challenge BLOB NOT NULL,
    expires   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL REFERENCES users(username),
    name     TEXT NOT NULL,
    created  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_settings (
    username     TEXT NOT NULL REFERENCES users(username),
    profile      TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    web          INTEGER NOT NULL DEFAULT 1,
    seeded       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (username, profile)
);

CREATE TABLE IF NOT EXISTS sources (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL REFERENCES users(username),
    profile  TEXT NOT NULL,
    kind     TEXT NOT NULL,
    title    TEXT NOT NULL,
    domain   TEXT NOT NULL DEFAULT '',
    note     TEXT NOT NULL DEFAULT '',
    filters  TEXT NOT NULL DEFAULT '{}',
    content  TEXT NOT NULL DEFAULT '',
    enabled  INTEGER NOT NULL DEFAULT 1,
    created  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, data_dir: str) -> None:
        self._lock = threading.RLock()
        self._path = Path(data_dir)
        self._path.mkdir(parents=True, exist_ok=True)
        self._db_path = self._path / "coder.db"
        self._local = threading.local()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._db_path.as_uri(),
                uri=True,
                timeout=30,
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript(_SCHEMA)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(chats)")}
            if "folder_id" not in cols:
                conn.execute("ALTER TABLE chats ADD COLUMN folder_id INTEGER DEFAULT NULL")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
            if "stopped" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN stopped INTEGER NOT NULL DEFAULT 0")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
            if "sources" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN sources TEXT NOT NULL DEFAULT '[]'")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
            if "trail" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN trail TEXT NOT NULL DEFAULT '[]'")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
            if "verify" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN verify TEXT DEFAULT NULL")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
            if "thinking" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN thinking TEXT NOT NULL DEFAULT ''")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
            if "thought_s" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN thought_s REAL DEFAULT NULL")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
            if "icon" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN icon TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def create_user(self, username: str, pw_hash: str, rights: list[str]) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO users (username, pw_hash, rights, created) VALUES (?, ?, ?, ?)",
                (username, pw_hash, ",".join(sorted(rights)), _now_iso()),
            )
            conn.commit()

    def user_count(self) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            return row["c"]  # type: ignore[no-any-return]

    def seed_admin(self, username: str, password: str) -> None:
        """Create the first admin user when the users table is empty."""
        with self._lock:
            conn = self._connect()
            count: int = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            if count == 0:
                conn.execute(
                    "INSERT INTO users (username, pw_hash, rights, created) VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), "admin", _now_iso()),
                )
                conn.commit()

    def get_user(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["rights"] = d["rights"].split(",")
            return d

    def get_user_by_hash(self, pw_hash: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM users WHERE pw_hash = ?", (pw_hash,)).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["rights"] = d["rights"].split(",")
            return d

    def record_login_failure(self, key: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("INSERT OR IGNORE INTO login_failures (key, at) VALUES (?, ?)", (key, _now_iso()))
            conn.commit()

    def has_recent_failures(self, key: str, minutes: int = 15, limit: int = 5) -> bool:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM login_failures WHERE key = ? AND at >= datetime('now', ?)",
                (key, f"-{minutes} minutes"),
            ).fetchone()
            return (row["c"] or 0) >= limit  # type: ignore[no-any-return]

    def clear_failures(self, username: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM login_failures WHERE key IN (?, ?)",
                         (f"user:{username}", f"ip:temp"))
            conn.commit()

    def create_session(self, username: str, session_id: str) -> str:
        import hashlib
        id_hash = hashlib.sha256(session_id.encode()).hexdigest()
        exp_dt = datetime.now(timezone.utc) + timedelta(days=30)
        expires = exp_dt.isoformat()
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO sessions (id_hash, username, created, expires) VALUES (?, ?, ?, ?)",
                (id_hash, username, _now_iso(), expires),
            )
            conn.commit()
        return id_hash

    def get_session(self, id_hash: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id_hash = ?", (id_hash,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def delete_session(self, id_hash: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
            conn.commit()

    def delete_user_sessions(self, username: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
            conn.commit()

    def create_chat(self, username: str, profile: str = "general") -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO chats (username, title, profile, created, updated) VALUES (?, ?, ?, ?, ?)",
                (username, "New chat", profile, now, now),
            )
            conn.commit()
            return {"id": cur.lastrowid, "title": "New chat", "profile": profile, "updated": now}

    def get_chat(self, chat_id: int, username: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM chats WHERE id = ? AND username = ?", (chat_id, username)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def list_chats(self, username: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, title, profile, updated, folder_id FROM chats WHERE username = ? ORDER BY updated DESC",
                (username,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_chat_title(self, chat_id: int, username: str, title: str) -> dict[str, Any] | None:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE chats SET title = ?, updated = ? WHERE id = ? AND username = ?",
                (title, now, chat_id, username),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return None
            conn.commit()
            return {"id": chat_id, "title": title}

    def delete_chat(self, chat_id: int, username: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM chats WHERE id = ? AND username = ?", (chat_id, username))
            conn.commit()

    def add_message(self, chat_id: int, role: str, content: str,
                    tokens: int | None = None, tps: float | None = None,
                    stopped: bool = False, sources: list | None = None,
                    trail: list | None = None, verify: dict | None = None,
                    thinking: str = "", thought_s: float | None = None) -> int:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO messages (chat_id, role, content, created, tokens, tps, stopped, sources, trail, verify, thinking, thought_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, role, content, now, tokens, tps, 1 if stopped else 0,
                 json.dumps(sources or []), json.dumps(trail or []),
                 json.dumps(verify) if verify is not None else None,
                 thinking or "", thought_s),
            )
            conn.commit()
            return cur.lastrowid

    def get_messages(self, chat_id: int, username: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            # Verify ownership
            owner = conn.execute(
                "SELECT username FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if owner is None or owner["username"] != username:
                return []
            rows = conn.execute(
                """SELECT m.*, mf.file_id, f.name as file_name, f.size as file_size
                   FROM messages m
                   LEFT JOIN message_files mf ON m.id = mf.message_id
                   LEFT JOIN files f ON mf.file_id = f.id
                   WHERE m.chat_id = ?
                   ORDER BY m.created, m.id""",
                (chat_id,),
            ).fetchall()
            # Group by message id
            msgs: dict[int, dict[str, Any]] = {}
            for r in rows:
                mid = r["id"]
                if mid not in msgs:
                    msgs[mid] = {
                        "id": mid,
                        "role": r["role"],
                        "content": r["content"],
                        "created": r["created"],
                        "tokens": r["tokens"],
                        "tps": r["tps"],
                        "stopped": bool(r["stopped"]),
                        "sources": json.loads(r["sources"] or "[]"),
                        "trail": json.loads(r["trail"] or "[]"),
                        "verify": json.loads(r["verify"]) if r["verify"] else None,
                        "thinking": r["thinking"] or "",
                        "thought_s": r["thought_s"],
                        "files": [],
                    }
                if r["file_id"] is not None:
                    msgs[mid]["files"].append({
                        "id": r["file_id"],
                        "name": r["file_name"],
                        "size": r["file_size"],
                    })
            return list(msgs.values())

    def get_messages_for_completion(self, chat_id: int) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT m.role, m.content
                   FROM messages m
                   WHERE m.chat_id = ?
                    ORDER BY m.created, m.id""",
                 (chat_id,),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    def add_file(self, username: str, name: str, size: int, content: bytes) -> int:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO files (username, name, size, content, created) VALUES (?, ?, ?, ?, ?)",
                (username, name, size, content, now),
            )
            conn.commit()
            return cur.lastrowid

    def get_file(self, file_id: int, username: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            if username:
                row = conn.execute(
                    "SELECT * FROM files WHERE id = ? AND username = ?", (file_id, username)
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def get_file_content(self, file_id: int) -> bytes | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT content FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                return None
            return row["content"]

    def add_message_files(self, message_id: int, file_ids: list[int]) -> None:
        with self._lock:
            conn = self._connect()
            for fid in file_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO message_files (message_id, file_id) VALUES (?, ?)",
                    (message_id, fid),
                )
            conn.commit()

    def create_invite(self, rights: list[str], note: str | None = None) -> dict[str, Any]:
        import hashlib
        import secrets
        token = secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = _now_iso()
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO invites (token_hash, rights, note, created, expires) VALUES (?, ?, ?, ?, ?)",
                (token_hash, ",".join(sorted(rights)), note, now, expires),
            )
            conn.commit()
            return {"id": cur.lastrowid, "token": token, "rights": sorted(rights),
                    "note": note, "created": now, "expires": expires}

    def get_invite_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM invites WHERE token_hash = ? AND deleted = 0", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["rights"] = d["rights"].split(",")
            return d

    def use_invite(self, token_hash: str, username: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE invites SET used_by = ? WHERE token_hash = ?",
                (username, token_hash),
            )
            conn.commit()

    def delete_invite(self, invite_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE invites SET deleted = 1 WHERE id = ?", (invite_id,))
            conn.commit()

    def list_invites(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, rights, note, created, expires, used_by FROM invites WHERE deleted = 0 ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT username, rights, created, last_login FROM users ORDER BY created"
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["rights"] = d["rights"].split(",")
                out.append(d)
            return out

    def delete_user(self, username: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
            conn.execute("DELETE FROM chats WHERE username = ?", (username,))
            conn.execute("DELETE FROM files WHERE username = ?", (username,))
            conn.execute("DELETE FROM passkeys WHERE username = ?", (username,))
            conn.execute("DELETE FROM folders WHERE username = ?", (username,))
            conn.execute("DELETE FROM profile_settings WHERE username = ?", (username,))
            conn.execute("DELETE FROM sources WHERE username = ?", (username,))
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()

    def update_user_theme(self, username: str, theme: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE users SET theme = ? WHERE username = ?", (theme, username))
            conn.commit()

    def set_last_login(self, username: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE users SET last_login = ? WHERE username = ?", (_now_iso(), username))
            conn.commit()

    def add_message_at(self, chat_id: int, role: str, content: str, created: str) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO messages (chat_id, role, content, created, tokens, tps) VALUES (?, ?, ?, ?, NULL, NULL)",
                (chat_id, role, content, created),
            )
            conn.commit()
            return cur.lastrowid

    def reset_chat_usage(self, chat_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE chats SET last_prompt_tokens = NULL, last_completion_tokens = NULL WHERE id = ?",
                (chat_id,),
            )
            conn.commit()

    def add_passkey(self, username: str, credential_id: bytes, public_key: bytes,
                    sign_count: int, name: str) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO passkeys (username, credential_id, public_key, sign_count, name, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, credential_id, public_key, sign_count, name, now),
            )
            conn.commit()
            return {"id": cur.lastrowid, "name": name, "created": now}

    def list_passkeys(self, username: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, name, created, last_used, credential_id FROM passkeys "
                "WHERE username = ? ORDER BY id",
                (username,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_passkey(self, credential_id: bytes) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM passkeys WHERE credential_id = ?", (credential_id,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def delete_passkey(self, passkey_id: int, username: str) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM passkeys WHERE id = ? AND username = ?",
                (passkey_id, username),
            )
            conn.commit()
            return cur.rowcount > 0

    def touch_passkey(self, passkey_id: int, sign_count: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE passkeys SET sign_count = ?, last_used = ? WHERE id = ?",
                (sign_count, _now_iso(), passkey_id),
            )
            conn.commit()

    def put_challenge(self, key_hash: str, kind: str, username: str | None,
                      challenge: bytes, ttl: float = 300) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "DELETE FROM webauthn_challenges WHERE expires < ?",
                (time.time(),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO webauthn_challenges (key_hash, kind, username, challenge, expires) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_hash, kind, username, challenge, time.time() + ttl),
            )
            conn.commit()

    def take_challenge(self, key_hash: str, kind: str) -> tuple[bytes, str] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM webauthn_challenges WHERE key_hash = ? AND kind = ?",
                (key_hash, kind),
            ).fetchone()
            conn.execute(
                "DELETE FROM webauthn_challenges WHERE key_hash = ?",
                (key_hash,),
            )
            conn.commit()
            if row is None:
                return None
            d = dict(row)
            if d["expires"] < time.time():  # type: ignore[operator]
                return None
            username = d["username"] or ""
            return (d["challenge"], username)  # type: ignore[return-value]

    def create_folder(self, username: str, name: str) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO folders (username, name, created) VALUES (?, ?, ?)",
                (username, name, now),
            )
            conn.commit()
            return {"id": cur.lastrowid, "name": name, "created": now}

    def list_folders(self, username: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, name, created FROM folders WHERE username = ? ORDER BY name COLLATE NOCASE, id",
                (username,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_folder(self, folder_id: int, username: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM folders WHERE id = ? AND username = ?",
                (folder_id, username),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def rename_folder(self, folder_id: int, username: str, name: str) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE folders SET name = ? WHERE id = ? AND username = ?",
                (name, folder_id, username),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_folder(self, folder_id: int, username: str) -> bool:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE chats SET folder_id = NULL WHERE folder_id = ? AND username = ?",
                (folder_id, username),
            )
            cur = conn.execute(
                "DELETE FROM folders WHERE id = ? AND username = ?",
                (folder_id, username),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_chat_folder(self, chat_id: int, username: str, folder_id: int | None) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE chats SET folder_id = ? WHERE id = ? AND username = ?",
                (folder_id, chat_id, username),
            )
            conn.commit()
            return cur.rowcount > 0

    # TODO (task R01): folders and stopped answers (chat/SPEC.md §11). Tests: test_step_round2.py
    #   1. Add to _SCHEMA:  folders(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
    #      name TEXT NOT NULL, created TEXT NOT NULL)
    #   2. In init_schema, after executescript (existing databases have the old tables!):
    #        cols = {r["name"] for r in conn.execute("PRAGMA table_info(chats)")}
    #        if "folder_id" not in cols: ALTER TABLE chats ADD COLUMN folder_id INTEGER DEFAULT NULL
    #      and the same for messages: "stopped" -> ALTER TABLE messages ADD COLUMN stopped INTEGER NOT NULL DEFAULT 0
    #   3. list_chats also selects folder_id. add_message gets a keyword `stopped: bool = False` and stores 1/0.
    #      get_messages puts "stopped": bool(r["stopped"]) in every message dict.
    #   4. delete_user also deletes the user's folders.
    #   5. New methods (each with self._lock and self._connect(), like the others):
    #      create_folder(username, name) -> {"id", "name", "created"}
    #      list_folders(username) -> [{"id", "name", "created"}] ORDER BY name COLLATE NOCASE, id
    #      get_folder(folder_id, username) -> that dict or None (None for someone else's folder)
    #      rename_folder(folder_id, username, name) -> True if a row changed
    #      delete_folder(folder_id, username) -> True if deleted; then also
    #         UPDATE chats SET folder_id = NULL WHERE folder_id = ? AND username = ?
    #      set_chat_folder(chat_id, username, folder_id or None) -> True if the chat is yours and changed

    def get_profile_settings(self, username: str, profile: str) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM profile_settings WHERE username = ? AND profile = ?",
                (username, profile),
            ).fetchone()
            if row is None:
                return {"instructions": "", "web": True, "seeded": False}
            d = dict(row)
            return {"instructions": d["instructions"], "web": bool(d["web"]), "seeded": bool(d["seeded"])}

    def set_profile_settings(self, username: str, profile: str, **fields: Any) -> None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM profile_settings WHERE username = ? AND profile = ?",
                (username, profile),
            ).fetchone()
            if row is None:
                current = {"instructions": "", "web": True, "seeded": False}
            else:
                current = {"instructions": row["instructions"], "web": bool(row["web"]), "seeded": bool(row["seeded"])}
            for k in ("instructions", "web", "seeded"):
                if k in fields:
                    current[k] = fields[k]
            conn.execute(
                "INSERT OR REPLACE INTO profile_settings (username, profile, instructions, web, seeded) VALUES (?, ?, ?, ?, ?)",
                (username, profile, current["instructions"], 1 if current["web"] else 0, 1 if current["seeded"] else 0),
            )
            conn.commit()

    def add_source(self, username: str, profile: str, kind: str, title: str,
                   domain: str = "", note: str = "", filters: Any = None, content: str = "") -> dict[str, Any]:
        now = _now_iso()
        if filters is None:
            filters = "{}"
        elif isinstance(filters, dict):
            filters = json.dumps(filters)
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO sources (username, profile, kind, title, domain, note, filters, content, enabled, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (username, profile, kind, title, domain, note, filters, content, now),
            )
            conn.commit()
            return self.get_source(cur.lastrowid, username)  # type: ignore[return-value]

    def list_sources(self, username: str, profile: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM sources WHERE username = ? AND profile = ? ORDER BY id",
                (username, profile),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["filters"] = json.loads(d["filters"])
                d["enabled"] = bool(d["enabled"])
                out.append(d)
            return out

    def get_source(self, source_id: int, username: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM sources WHERE id = ? AND username = ?",
                (source_id, username),
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["filters"] = json.loads(d["filters"])
            d["enabled"] = bool(d["enabled"])
            return d

    def update_source(self, source_id: int, username: str, **fields: Any) -> bool:
        known = {"title", "note", "enabled", "filters", "content"}
        set_parts = []
        vals = []
        for k, v in fields.items():
            if k == "enabled":
                set_parts.append("enabled = ?")
                vals.append(1 if v else 0)
            elif k == "filters":
                set_parts.append("filters = ?")
                vals.append(json.dumps(v))
            elif k in known:
                set_parts.append(f"{k} = ?")
                vals.append(v)
        if not set_parts:
            return False
        vals.append(source_id)
        vals.append(username)
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                f"UPDATE sources SET {', '.join(set_parts)} WHERE id = ? AND username = ?",
                tuple(vals),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_source(self, source_id: int, username: str) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM sources WHERE id = ? AND username = ?",
                (source_id, username),
            )
            conn.commit()
            return cur.rowcount > 0

    # TODO (task E05): EvidenceHunt v2 storage (chat/SPEC.md §13). Tests: test_step_research.py (storage parts),
    #   test_accept_evidence.py.
    #   1. init_schema migration, like "sources": messages gets trail TEXT NOT NULL DEFAULT '[]', verify TEXT DEFAULT NULL,
    #      thinking TEXT NOT NULL DEFAULT '' and thought_s REAL DEFAULT NULL when missing; users gets
    #      icon TEXT NOT NULL DEFAULT '' when missing (PRAGMA table_info(users)).
    #   2. add_message gets the keywords trail: list | None = None, verify: dict | None = None, thinking: str = "",
    #      thought_s: float | None = None; stored as json.dumps(trail or []), None or json.dumps(verify), thinking or "", thought_s.
    #   3. get_messages adds "trail": json.loads(...), "verify": json.loads(...) or None, "thinking", "thought_s".
    #   4. update_user_icon(username, icon): UPDATE users SET icon = ? WHERE username = ?
    # Done (task E05)

    def update_user_icon(self, username: str, icon: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE users SET icon = ? WHERE username = ?", (icon, username))
            conn.commit()

    def update_chat_usage(self, chat_id: int, prompt_tokens: int, completion_tokens: int) -> None:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE chats SET last_prompt_tokens = ?, last_completion_tokens = ?, updated = ? WHERE id = ?",
                (prompt_tokens, completion_tokens, now, chat_id),
            )
            conn.commit()
