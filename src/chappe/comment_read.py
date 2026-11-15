"""Read-only, exact-link, live public discussion export; no analytics writes."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from .errors import ChappeError, ExitCode
from .tdlib import message_media_type, message_text

# Official td/telegram/MessageId.h: ServerMessageId is shifted left by20.
# Use the preceding *server* ID, not an inclusive anchor or an invalid low-bit ID.
SERVER_ID_UNIT = 1 << 20
DOCS = [
    "https://github.com/tdlib/td/blob/master/td/generate/scheme/td_api.tl",
    "https://github.com/tdlib/td/blob/master/td/telegram/MessageId.h",
]


def download_comment_photo(
    gateway, url: str, message_id: int, output: str, *, max_bytes: int = 10 * 1024 * 1024
) -> dict:
    """One explicitly selected public-comment photo, exported by the CLI only."""
    destination = Path(output).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if destination.is_relative_to(repo) or destination.exists():
        raise ChappeError(
            "Photo output must be a new file outside the Chappe repository.", ExitCode.USAGE_ERROR
        )
    if not 1 <= max_bytes <= 20 * 1024 * 1024:
        raise ChappeError("Photo byte limit must be1–20MiB.", ExitCode.USAGE_ERROR)
    thread = read_comments(gateway, url)
    found = next((m for m in thread["comments"] if int(m["id"]) == message_id), None)
    if not found:
        raise ChappeError(
            "Selected message is not an accessible comment on this exact post.", ExitCode.NOT_FOUND
        )
    raw = gateway.send(
        {
            "@type": "getMessage",
            "chat_id": int(thread["discussion_chat_id"]),
            "message_id": message_id,
        }
    )
    if int(raw.get("id", 0)) != message_id or int(raw.get("chat_id", 0)) != int(
        thread["discussion_chat_id"]
    ):
        raise ChappeError("Photo lookup returned a different message; refusing export.")
    content = raw.get("content") or {}
    if content.get("@type") != "messagePhoto":
        raise ChappeError(
            "Selected comment is not a photo; other media types are not downloaded.",
            ExitCode.USAGE_ERROR,
        )
    sizes = (content.get("photo") or {}).get("sizes") or []
    if not sizes:
        raise ChappeError("Photo has no accessible sizes.", ExitCode.NOT_FOUND)
    photo = max(sizes, key=lambda s: int(s.get("width", 0)) * int(s.get("height", 0)))
    file = photo.get("photo") or {}
    if (
        not file.get("id")
        or max(int(file.get("size", 0)), int(file.get("expected_size", 0))) > max_bytes
    ):
        raise ChappeError("Photo is unavailable or exceeds the byte limit.", ExitCode.USAGE_ERROR)
    downloaded = gateway.send(
        {
            "@type": "downloadFile",
            "file_id": int(file["id"]),
            "priority": 1,
            "offset": 0,
            "limit": max_bytes + 1,
            "synchronous": True,
        },
        timeout=60,
    )
    local = downloaded.get("local") or {}
    if not local.get("is_downloading_completed") or not local.get("path"):
        raise ChappeError("Photo download was partial or unavailable; no output was exported.")
    source = Path(local["path"])
    size = source.stat().st_size
    if not 0 < size <= max_bytes:
        raise ChappeError(
            "Downloaded photo exceeds the byte limit or is empty; no output was exported."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    return {
        "ok": True,
        "url": thread["url"],
        "message_id": str(message_id),
        "discussion_chat_id": thread["discussion_chat_id"],
        "media_type": "photo",
        "caption": message_text(raw),
        "output": str(destination),
        "size_bytes": size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "scope": "Exactly one selected public comment photo; no Telegram send or media from other comments.",
    }


def validate_public_post_url(url: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ChappeError("Malformed public post URL.", ExitCode.USAGE_ERROR) from exc
    if (
        parsed.scheme not in ("http", "https")
        or parsed.hostname not in ("t.me", "telegram.me")
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ChappeError(
            "Use a full public post URL: https://t.me/channel/123 (no query or fragment).",
            ExitCode.USAGE_ERROR,
        )
    match = re.fullmatch(r"/([A-Za-z][A-Za-z0-9_]{3,31})/([1-9][0-9]*)/?", parsed.path)
    if not match or match[1].lower() == "c":
        raise ChappeError(
            "Only public channel post URLs are supported; private/comment/topic links are not accepted.",
            ExitCode.USAGE_ERROR,
        )
    return f"https://t.me/{match[1]}/{match[2]}", int(match[2])


def _count(message: dict) -> int | None:
    info = (
        (message.get("interaction_info") or {}).get("reply_info") or message.get("reply_info") or {}
    )
    value = info.get("reply_count")
    return int(value) if value is not None and int(value) >= 0 else None


def _message(message: dict) -> dict:
    reply = message.get("reply_to") or {}
    sender = message.get("sender_id") or {}
    return {
        "id": str(message["id"]),
        "chat_id": str(message["chat_id"]),
        "date": datetime.fromtimestamp(message.get("date", 0), timezone.utc).isoformat(),
        "edit_date": message.get("edit_date", 0),
        "text": message_text(message),
        "media_type": message_media_type(message),
        "caption": message_text(message) if "caption" in (message.get("content") or {}) else None,
        "message_thread_id": str(message["message_thread_id"])
        if "message_thread_id" in message
        else None,
        "topic_id": message.get("topic_id"),
        "reply_to": {
            k: str(reply[k]) if k.endswith("_id") else reply[k]
            for k in ("@type", "chat_id", "message_id")
            if k in reply
        },
        "sender": {
            k: str(v) if k.endswith("_id") else v
            for k, v in sender.items()
            if k in ("@type", "chat_id", "user_id")
        },
    }


def read_comments(
    gateway, url: str, *, page_size: int = 100, max_messages: int = 10000, max_pages: int = 1000
) -> dict:
    """Called only by the public CLI after authReady. No sends/media/store access."""
    canonical, public_id = validate_public_post_url(url)
    if not 1 <= page_size <= 100 or not 1 <= max_messages <= 100000 or not 1 <= max_pages <= 10000:
        raise ChappeError(
            "page-size must be1–100, max-messages1–100000, max-pages1–10000.", ExitCode.USAGE_ERROR
        )
    started = datetime.now(timezone.utc).isoformat()
    link = gateway.send({"@type": "getMessageLinkInfo", "url": canonical})
    post = link.get("message")
    if not link.get("is_public") or not post:
        raise ChappeError("The exact public linked post is unavailable.", ExitCode.NOT_FOUND)
    chat_id = int(link.get("chat_id", 0))
    if (
        int(post.get("chat_id", 0)) != chat_id
        or int(post.get("id", 0)) != public_id * SERVER_ID_UNIT
    ):
        raise ChappeError(
            "Resolved message does not match the exact requested post; refusing a neighboring/cache result.",
            ExitCode.NOT_FOUND,
        )
    post_id = int(post["id"])
    thread = gateway.send({"@type": "getMessageThread", "chat_id": chat_id, "message_id": post_id})
    discussion = int(thread["chat_id"])
    root = int(thread["message_thread_id"])
    roots = {(int(m["chat_id"]), int(m["id"])) for m in thread.get("messages", [])}
    roots.add((discussion, root))
    roots.add((chat_id, post_id))
    start_count = _count(post)
    thread_count_start = (thread.get("reply_info") or {}).get("reply_count")
    comments = {}
    duplicates = excluded = 0
    pages = []
    cursor = 0
    terminal = False
    stop_reason = "max_pages"
    seen_cursors = set()
    for _ in range(max_pages):
        if cursor in seen_cursors:
            stop_reason = "cursor_repeated"
            break
        seen_cursors.add(cursor)
        result = gateway.send(
            {
                "@type": "getMessageThreadHistory",
                "chat_id": discussion,
                "message_id": root,
                "from_message_id": cursor,
                "offset": 0,
                "limit": page_size,
            }
        )
        rows = result.get("messages")
        if not isinstance(rows, list):
            raise ChappeError("TDLib thread history returned no messages array.")
        trace = {
            "from_message_id": str(cursor),
            "returned": len(rows),
            "total_count": result.get("total_count"),
        }
        pages.append(trace)
        if not rows:
            terminal = True
            stop_reason = "empty_terminal_page"
            break
        ids = []
        for row in rows:
            key = (int(row["chat_id"]), int(row["id"]))
            ids.append(key[1])
            if key in roots:
                excluded += 1
                continue
            if key[0] != discussion:
                raise ChappeError(
                    "History returned a message outside the resolved discussion chat."
                )
            if key in comments:
                duplicates += 1
                continue
            if len(comments) >= max_messages:
                stop_reason = "max_messages"
                break
            comments[key] = _message(row)
        if stop_reason == "max_messages":
            break
        oldest = min(ids)
        if oldest <= 0 or oldest % SERVER_ID_UNIT:
            stop_reason = "unsupported_nonserver_cursor"
            break
        next_cursor = oldest - SERVER_ID_UNIT
        trace["next_from_message_id"] = str(next_cursor)
        if cursor and next_cursor >= cursor:
            stop_reason = "cursor_did_not_decrease"
            break
        if next_cursor == 0:
            terminal = True
            stop_reason = "server_id_lower_bound"
            break
        cursor = next_cursor
    end_post = gateway.send({"@type": "getMessage", "chat_id": chat_id, "message_id": post_id})
    end_thread = gateway.send(
        {"@type": "getMessageThread", "chat_id": chat_id, "message_id": post_id}
    )
    end_count = _count(end_post)
    thread_count_end = (end_thread.get("reply_info") or {}).get("reply_count")
    same_thread = (
        int(end_thread["chat_id"]) == discussion and int(end_thread["message_thread_id"]) == root
    )
    counts = [
        v for v in (start_count, end_count, thread_count_start, thread_count_end) if v is not None
    ]
    counts_stable = len(set(counts)) <= 1
    count_matches = all(int(v) == len(comments) for v in counts)
    complete = terminal and same_thread and counts_stable and count_matches
    return {
        "ok": complete,
        "url": canonical,
        "post_public_id": public_id,
        "post": _message(post),
        "discussion_chat_id": str(discussion),
        "discussion_root_id": str(root),
        "comments": sorted(comments.values(), key=lambda m: int(m["id"])),
        "coverage": {
            "complete": complete,
            "accessible_history_terminal": terminal,
            "stop_reason": stop_reason,
            "comments_returned": len(comments),
            "root_rows_excluded": excluded,
            "duplicates_removed": duplicates,
            "declared_post_count_start": start_count,
            "declared_post_count_end": end_count,
            "declared_thread_count_start": thread_count_start,
            "declared_thread_count_end": thread_count_end,
            "counts_stable": counts_stable,
            "declared_counts_match": count_matches,
            "same_thread_at_end": same_thread,
            "pages": pages,
            "next_from_message_id": None if terminal else str(cursor),
            "max_messages": max_messages,
            "max_pages": max_pages,
            "scope": "Accessible replies observed sequentially, not an atomic snapshot or deleted/inaccessible history.",
        },
        "provenance": {
            "transport": "live TDLib via public Chappe CLI",
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "primary_docs": DOCS,
            "read_only": True,
            "media_downloaded": False,
        },
    }
