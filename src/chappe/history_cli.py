"""Read-only, bounded chat discovery and text history for authorized research."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
from typing import Any, Callable

import typer

from .errors import ChappeError, ExitCode
from .output import emit, fail
from .tdlib import TDLibGateway
from .store import Store


chat_app = typer.Typer(no_args_is_help=True, help="Find known chats and read text history.")


def _run(ctx: typer.Context, operation: Callable[[TDLibGateway], dict[str, Any]]) -> None:
    options = ctx.obj or {}
    pretty = bool(options.get("pretty"))
    gateway = None
    try:
        try:
            gateway = TDLibGateway(options["config"])
            state = gateway.authorization_state()
            if state.get("@type") != "authorizationStateReady":
                raise ChappeError(
                    "Telegram authorization is required before reading chat history.",
                    ExitCode.AUTH_ERROR,
                    details={"authorization_state": state.get("@type")},
                    next_command="chappe onboard --check-auth",
                )
            payload = operation(gateway)
        finally:
            if gateway is not None:
                gateway.close()
        emit({"ok": True, **payload}, pretty=pretty)
    except ChappeError as exc:
        fail(exc, pretty=pretty)


def _chat_summary(chat: dict[str, Any]) -> dict[str, Any]:
    # resolve_chat returns a normalized wrapper; getChat returns a raw chat.
    raw = chat.get("raw") or chat
    chat_type = raw.get("type") or {}
    usernames = (raw.get("usernames") or {}).get("active_usernames") or []
    return {
        "id": str(raw["id"]),
        "title": raw.get("title"),
        "type": chat_type.get("@type"),
        "is_channel": chat_type.get("is_channel"),
        "username": usernames[0] if usernames else chat.get("username"),
    }


@chat_app.command("search")
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Nonempty title or username query for known chats."),
    limit: int = typer.Option(100, "--limit", min=1, max=200, help="Maximum chat summaries."),
) -> None:
    """Search account-known chats locally and on the server; never read message previews."""
    query = query.strip()
    if not query:
        fail(ChappeError("A nonempty chat query is required.", ExitCode.USAGE_ERROR))

    def operation(gateway: TDLibGateway) -> dict[str, Any]:
        chat_ids: list[int] = []
        counts: dict[str, int] = {}
        for method in ("searchChats", "searchChatsOnServer"):
            # https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1search_chats.html
            # searchChatsOnServer has the same query/limit fields.
            result = gateway.send({"@type": method, "query": query, "limit": limit})
            ids = result.get("chat_ids") or []
            counts[method] = len(ids)
            for chat_id in ids:
                if int(chat_id) not in chat_ids:
                    chat_ids.append(int(chat_id))
        chats = [
            _chat_summary(gateway.send({"@type": "getChat", "chat_id": chat_id}))
            for chat_id in chat_ids[:limit]
        ]
        return {
            "query": query,
            "scope": "account_known_chat_titles_and_usernames",
            "chats": chats,
            "returned_count": len(chats),
            "requested_limit": limit,
            "source_result_counts": counts,
            "possibly_truncated": len(chat_ids) > limit or any(n >= limit for n in counts.values()),
            "complete": False,
            "coverage_note": "Search matches known chats; it is not an exhaustive account inventory.",
        }

    _run(ctx, operation)


@chat_app.command("user")
def user(
    ctx: typer.Context,
    user_id: int = typer.Argument(..., min=1, help="Known sender ID from a scoped chat export."),
) -> None:
    """Read minimal known-user identity metadata; never export phone or presence."""
    def operation(gateway: TDLibGateway) -> dict[str, Any]:
        raw = gateway.send({"@type": "getUser", "user_id": user_id})
        return {"user": {
            "id": str(raw["id"]),
            "first_name": raw.get("first_name"),
            "last_name": raw.get("last_name"),
            "usernames": (raw.get("usernames") or {}).get("active_usernames", []),
            "type": (raw.get("type") or {}).get("@type"),
        }}
    _run(ctx, operation)


def _media(message: dict[str, Any]) -> dict[str, Any] | None:
    content = message.get("content") or {}
    for name in ("video", "audio", "document", "animation", "voice_note", "video_note"):
        item = content.get(name)
        if not isinstance(item, dict):
            continue
        file = item.get(name)
        if isinstance(file, dict) and file.get("id"):
            return {
                "file_id": file["id"], "kind": name,
                "file_name": item.get("file_name"), "mime_type": item.get("mime_type"),
                "duration": item.get("duration"), "width": item.get("width"),
                "height": item.get("height"), "size": file.get("size"),
                "expected_size": file.get("expected_size"),
            }
    sizes = (content.get("photo") or {}).get("sizes") or []
    if sizes:
        item = max(sizes, key=lambda value: value.get("width", 0) * value.get("height", 0))
        file = item.get("photo") or {}
        if file.get("id"):
            return {
                "file_id": file["id"], "kind": "photo", "mime_type": "image/jpeg",
                "width": item.get("width"), "height": item.get("height"),
                "size": file.get("size"), "expected_size": file.get("expected_size"),
            }
    return None


@chat_app.command("message")
def message(
    ctx: typer.Context,
    chat_id: int = typer.Argument(..., help="Known numeric chat ID."),
    message_id: int = typer.Argument(..., min=1, help="TDLib message ID from chat history."),
) -> None:
    """Get one message with media metadata; do not download or mark it read."""
    def operation(gateway: TDLibGateway) -> dict[str, Any]:
        raw = gateway.send({"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        return {"message": _message_summary(raw, chat_id), "media": _media(raw)}
    _run(ctx, operation)


@chat_app.command("resolve-user")
def resolve_user(
    ctx: typer.Context,
    user_id: int = typer.Argument(..., min=1, help="Known user ID for an authorized conversation."),
) -> None:
    """Resolve an existing private chat from a known sender without reading messages."""
    def operation(gateway: TDLibGateway) -> dict[str, Any]:
        # https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1create_private_chat.html
        # Despite the name, TDLib documents this as returning an existing chat.
        raw = gateway.send({"@type": "createPrivateChat", "user_id": user_id, "force": False})
        return {"chat": _chat_summary(raw), "messages_read": False}
    _run(ctx, operation)


@chat_app.command("download")
def download(
    ctx: typer.Context,
    chat_id: int = typer.Argument(..., help="Known numeric chat ID."),
    message_id: int = typer.Argument(..., min=1, help="TDLib message ID from chat history."),
    output: Path = typer.Option(..., "--output", help="New destination file; never overwrite."),
    max_bytes: int = typer.Option(250_000_000, "--max-bytes", min=1, help="Maximum media size."),
    timeout: float = typer.Option(60.0, "--timeout", min=1, max=600, help="Download timeout seconds."),
) -> None:
    """Download the primary attachment of one message with hash and source provenance."""
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        fail(ChappeError("Destination already exists; choose a new file.", ExitCode.USAGE_ERROR))

    def operation(gateway: TDLibGateway) -> dict[str, Any]:
        raw = gateway.send({"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        media = _media(raw)
        if media is None:
            raise ChappeError("Message has no supported primary attachment.", ExitCode.NOT_FOUND)
        known_size = max(media.get("size") or 0, media.get("expected_size") or 0)
        if known_size > max_bytes:
            raise ChappeError("Attachment exceeds --max-bytes.", ExitCode.USAGE_ERROR)
        # Official TDLib downloadFile: synchronous returns only after completion/failure.
        # The byte limit also bounds files whose size is initially unknown.
        file = gateway.send({
            "@type": "downloadFile", "file_id": media["file_id"], "priority": 1,
            "offset": 0, "limit": max_bytes, "synchronous": True,
        }, timeout=timeout)
        local = file.get("local") or {}
        if not local.get("is_downloading_completed") or not local.get("path"):
            raise ChappeError("Download incomplete; no export was written.", ExitCode.TELEGRAM_ERROR)
        source = Path(local["path"])
        created = False
        try:
            size = source.stat().st_size
            if size > max_bytes:
                raise ChappeError("Downloaded file exceeds --max-bytes.", ExitCode.USAGE_ERROR)
            output.parent.mkdir(parents=True, exist_ok=True)
            # x mode prevents races from overwriting an existing destination.
            with source.open("rb") as src, output.open("xb") as dst:
                created = True
                shutil.copyfileobj(src, dst)
            with output.open("rb") as fh:
                hasher = hashlib.sha256()
                for block in iter(lambda: fh.read(1024 * 1024), b""):
                    hasher.update(block)
                digest = hasher.hexdigest()
        except OSError as exc:
            if created:
                output.unlink(missing_ok=True)
            raise ChappeError("Could not export the attachment.", ExitCode.TELEGRAM_ERROR) from exc
        payload = {
            "message": _message_summary(raw, chat_id), "media": media,
            "download": {"path": str(output), "size_bytes": size, "sha256": digest},
        }
        config = ctx.obj["config"]
        Store(config.storage.sqlite_path).audit(
            "chat_media_download",
            {"source_id": payload["message"]["source_id"], **payload["download"]},
            config.storage.audit_log_path,
        )
        return payload
    _run(ctx, operation)


def _message_summary(message: dict[str, Any], chat_id: int) -> dict[str, Any]:
    message_id = int(message["id"])
    content = message.get("content") or {}
    sender = message.get("sender_id") or {}
    reply = message.get("reply_to") or {}
    date = message.get("date")
    sender_summary = {
        "type": sender.get("@type"),
        **{key: str(sender[key]) for key in ("user_id", "chat_id") if key in sender},
    }
    reply_summary = None
    if reply:
        reply_summary = {
            "type": reply.get("@type"),
            **{key: str(reply[key]) for key in ("chat_id", "message_id") if key in reply},
        }
    elif message.get("reply_to_message_id"):
        reply_summary = {
            "type": "legacy_reply_to_message_id",
            "chat_id": str(chat_id),
            "message_id": str(message["reply_to_message_id"]),
        }
    return {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "source_id": f"telegram:chat:{chat_id}:message:{message_id}",
        "id_namespace": "tdlib",
        "date_unix": date,
        "date": datetime.fromtimestamp(date, tz=timezone.utc).isoformat() if date else None,
        "edit_date_unix": message.get("edit_date") or None,
        "sender": sender_summary,
        "is_outgoing": message.get("is_outgoing"),
        "content_type": content.get("@type"),
        "text": (content.get("text") or {}).get("text", ""),
        "caption": (content.get("caption") or {}).get("text", ""),
        "reply_to": reply_summary,
        "message_thread_id": str(message["message_thread_id"])
        if message.get("message_thread_id") else None,
    }


@chat_app.command("history")
def history(
    ctx: typer.Context,
    chat: str = typer.Argument(..., help="Numeric chat ID or @username; use chat search for titles."),
    limit: int = typer.Option(100, "--limit", min=1, max=5000, help="Maximum messages in this page."),
    from_message_id: int = typer.Option(
        0, "--from-message-id", min=0, help="Fetch messages older than this TDLib ID; 0 starts latest."
    ),
) -> None:
    """Read text/captions and provenance; no media download or message read marking."""
    if not chat.startswith("@"):
        try:
            int(chat)
        except ValueError:
            fail(ChappeError("Use a numeric chat ID or @username.", ExitCode.USAGE_ERROR))

    def operation(gateway: TDLibGateway) -> dict[str, Any]:
        resolved = gateway.resolve_chat(chat)
        chat_id = int(resolved["id"])
        # TDLib's cursor is inclusive. Fetch an extra row and exclude the cursor
        # so that consumers can append pages without duplicating the boundary.
        result = gateway.chat_history(
            chat_id, limit=limit + bool(from_message_id), from_message_id=from_message_id
        )
        by_id = {
            int(message["id"]): message
            for message in result.get("messages") or []
            if message.get("id") and (
                not from_message_id or int(message["id"]) < from_message_id
            )
        }
        ids = sorted(by_id, reverse=True)[:limit]
        messages = [_message_summary(by_id[message_id], chat_id) for message_id in ids]
        return {
            "chat": _chat_summary(resolved),
            "messages": messages,
            "returned_count": len(messages),
            "requested_limit": limit,
            "from_message_id": str(from_message_id),
            "next_from_message_id": str(ids[-1]) if ids else None,
            "cursor_semantics": "exclusive_older_than_tdlib_message_id",
            "order": "message_id_descending",
            "complete": False,
            "stop_reason": "limit_reached" if len(messages) == limit else "no_more_rows_returned",
            "coverage_note": (
                "A short or empty page does not prove complete history. Deleted or inaccessible "
                "messages and delayed server results cannot be recovered by this export."
            ),
            "media_downloaded": False,
        }

    _run(ctx, operation)
