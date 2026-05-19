from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  handle TEXT PRIMARY KEY,
  chat_id TEXT,
  title TEXT,
  username TEXT,
  raw_json TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  channel TEXT NOT NULL,
  id TEXT NOT NULL,
  date TEXT,
  text TEXT,
  views INTEGER DEFAULT 0,
  forwards INTEGER DEFAULT 0,
  replies INTEGER DEFAULT 0,
  reactions INTEGER DEFAULT 0,
  media_type TEXT,
  link TEXT,
  raw_json TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (channel, id)
);
CREATE TABLE IF NOT EXISTS post_snapshots (
  channel TEXT NOT NULL,
  post_id TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  views INTEGER DEFAULT 0,
  forwards INTEGER DEFAULT 0,
  replies INTEGER DEFAULT 0,
  reactions INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comments (
  channel TEXT NOT NULL,
  post_id TEXT NOT NULL,
  id TEXT NOT NULL,
  date TEXT,
  text TEXT,
  reactions INTEGER DEFAULT 0,
  raw_json TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (channel, post_id, id)
);
CREATE TABLE IF NOT EXISTS similar_channels (
  channel TEXT NOT NULL,
  handle TEXT NOT NULL,
  title TEXT,
  participants_count INTEGER,
  raw_json TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (channel, handle)
);
CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL,
  lint_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_policies (
  channel TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL,
  policy_json TEXT NOT NULL,
  source_path TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def upsert_channel(self, handle: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO channels(handle, chat_id, title, username, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(handle) DO UPDATE SET
                  chat_id=excluded.chat_id, title=excluded.title, username=excluded.username,
                  raw_json=excluded.raw_json, updated_at=excluded.updated_at
                """,
                (
                    handle,
                    str(payload.get("id") or payload.get("chat_id") or ""),
                    payload.get("title"),
                    payload.get("username"),
                    json.dumps(payload, ensure_ascii=False),
                    now_iso(),
                ),
            )

    def upsert_posts(self, channel: str, posts: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for post in posts:
                count += 1
                post_id = str(post["id"])
                metrics = (
                    int(post.get("views") or 0),
                    int(post.get("forwards") or 0),
                    int(post.get("replies") or 0),
                    int(post.get("reactions") or 0),
                )
                conn.execute(
                    """
                    INSERT INTO posts(
                      channel, id, date, text, views, forwards, replies, reactions,
                      media_type, link, raw_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel, id) DO UPDATE SET
                      date=excluded.date, text=excluded.text, views=excluded.views,
                      forwards=excluded.forwards, replies=excluded.replies,
                      reactions=excluded.reactions, media_type=excluded.media_type,
                      link=excluded.link, raw_json=excluded.raw_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        channel,
                        post_id,
                        post.get("date"),
                        post.get("text"),
                        *metrics,
                        post.get("media_type"),
                        post.get("link"),
                        json.dumps(post, ensure_ascii=False),
                        now_iso(),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO post_snapshots(
                      channel, post_id, captured_at, views, forwards, replies, reactions
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (channel, post_id, now_iso(), *metrics),
                )
        return count

    def upsert_comments(self, channel: str, post_id: str, comments: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for comment in comments:
                count += 1
                conn.execute(
                    """
                    INSERT INTO comments(channel, post_id, id, date, text, reactions, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel, post_id, id) DO UPDATE SET
                      date=excluded.date, text=excluded.text, reactions=excluded.reactions,
                      raw_json=excluded.raw_json, updated_at=excluded.updated_at
                    """,
                    (
                        channel,
                        str(post_id),
                        str(comment["id"]),
                        comment.get("date"),
                        comment.get("text"),
                        int(comment.get("reactions") or 0),
                        json.dumps(comment, ensure_ascii=False),
                        now_iso(),
                    ),
                )
        return count

    def list_posts(self, channel: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM posts WHERE channel=? ORDER BY date DESC LIMIT ?",
                (channel, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_post_snapshots(self, channel: str, *, limit: int = 10000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT channel, post_id, captured_at, views, forwards, replies, reactions
                FROM post_snapshots
                WHERE channel=?
                ORDER BY captured_at ASC
                LIMIT ?
                """,
                (channel, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_post(self, channel: str, post_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM posts WHERE channel=? AND id=?",
                (channel, str(post_id)),
            ).fetchone()
        return dict(row) if row else None

    def list_comments(self, channel: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM comments WHERE channel=? ORDER BY date DESC LIMIT ?",
                (channel, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def overview(self, channel: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            totals = {
                table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
                for table in [
                    "channels",
                    "posts",
                    "comments",
                    "drafts",
                    "automation_policies",
                    "audit_events",
                ]
            }
            handles: set[str] = set()
            if channel:
                handles.add(channel)
            for table in ["channels", "posts", "comments", "drafts", "automation_policies"]:
                column = "handle" if table == "channels" else "channel"
                rows = conn.execute(f"SELECT DISTINCT {column} AS handle FROM {table}").fetchall()
                handles.update(row["handle"] for row in rows if row["handle"])

            channels: list[dict[str, Any]] = []
            for handle in sorted(handles):
                channel_row = conn.execute(
                    "SELECT handle, chat_id, title, username, updated_at FROM channels WHERE handle=?",
                    (handle,),
                ).fetchone()
                top_post = conn.execute(
                    """
                    SELECT id, date, text, views, forwards, replies, reactions, link
                    FROM posts
                    WHERE channel=?
                    ORDER BY (forwards * 5 + replies * 3 + reactions * 2 + views / 100) DESC
                    LIMIT 1
                    """,
                    (handle,),
                ).fetchone()
                policy_row = conn.execute(
                    "SELECT enabled, source_path, updated_at FROM automation_policies WHERE channel=?",
                    (handle,),
                ).fetchone()
                channels.append(
                    {
                        "handle": handle,
                        "known": channel_row is not None,
                        "title": channel_row["title"] if channel_row else None,
                        "username": channel_row["username"] if channel_row else handle.lstrip("@"),
                        "updated_at": channel_row["updated_at"] if channel_row else None,
                        "posts": conn.execute(
                            "SELECT COUNT(*) AS count FROM posts WHERE channel=?",
                            (handle,),
                        ).fetchone()["count"],
                        "comments": conn.execute(
                            "SELECT COUNT(*) AS count FROM comments WHERE channel=?",
                            (handle,),
                        ).fetchone()["count"],
                        "drafts": conn.execute(
                            "SELECT COUNT(*) AS count FROM drafts WHERE channel=?",
                            (handle,),
                        ).fetchone()["count"],
                        "last_post_date": conn.execute(
                            "SELECT MAX(date) AS last_post_date FROM posts WHERE channel=?",
                            (handle,),
                        ).fetchone()["last_post_date"],
                        "policy_enabled": bool(policy_row["enabled"]) if policy_row else False,
                        "policy_source": policy_row["source_path"] if policy_row else None,
                        "top_post": dict(top_post) if top_post else None,
                    }
                )

        return {
            "sqlite_path": str(self.path),
            "totals": totals,
            "channels": channels,
        }

    def create_draft(self, channel: str, text: str) -> dict[str, Any]:
        digest = hashlib.sha256(f"{channel}\0{text}\0{now_iso()}".encode()).hexdigest()[:12]
        draft_id = f"draft_{digest}"
        created = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO drafts(id, channel, text, status, lint_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (draft_id, channel, text, "draft", "{}", created, created),
            )
        return {"id": draft_id, "channel": channel, "status": "draft", "created_at": created}

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def update_draft_lint(self, draft_id: str, lint: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE drafts SET lint_json=?, updated_at=? WHERE id=?",
                (json.dumps(lint, ensure_ascii=False), now_iso(), draft_id),
            )

    def update_draft_status(self, draft_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE drafts SET status=?, updated_at=? WHERE id=?",
                (status, now_iso(), draft_id),
            )

    def set_policy(self, channel: str, policy: dict[str, Any], source_path: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO automation_policies(channel, enabled, policy_json, source_path, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel) DO UPDATE SET
                  enabled=excluded.enabled, policy_json=excluded.policy_json,
                  source_path=excluded.source_path, updated_at=excluded.updated_at
                """,
                (channel, 1, json.dumps(policy, ensure_ascii=False), source_path, now_iso()),
            )

    def get_policy(self, channel: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT policy_json FROM automation_policies WHERE channel=? AND enabled=1",
                (channel,),
            ).fetchone()
        return json.loads(row["policy_json"]) if row else None

    def audit(self, event_type: str, payload: dict[str, Any], audit_log_path: Path | None = None) -> None:
        record = {"created_at": now_iso(), "event_type": event_type, "payload": payload}
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(created_at, event_type, payload_json) VALUES (?, ?, ?)",
                (record["created_at"], event_type, json.dumps(payload, ensure_ascii=False)),
            )
        if audit_log_path:
            audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_log_path.open("a") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
