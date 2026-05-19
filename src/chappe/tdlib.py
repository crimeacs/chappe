from __future__ import annotations

import hashlib
import json
import os
import time
from base64 import b64encode
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .config import ChappeConfig
from .errors import ChappeError, ExitCode


class TDLibUnavailable(ChappeError):
    def __init__(self, detail: str = "TDLib Python binding is not installed or failed to import."):
        super().__init__(
            detail,
            ExitCode.AUTH_ERROR,
            next_command="pipx install chappe or uvx --from chappe chappe doctor",
        )


def tdjson_available() -> bool:
    try:
        import tdjson  # noqa: F401

        return True
    except Exception:
        return False


def _load_tdjson_module():
    try:
        import tdjson

        return tdjson
    except Exception as exc:  # pragma: no cover - depends on optional native library
        raise TDLibUnavailable(str(exc)) from exc


def _date(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def _formatted_text(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    if value.get("@type") == "formattedText":
        return value.get("text") or ""
    if "text" in value and isinstance(value["text"], dict):
        return _formatted_text(value["text"])
    return str(value.get("text") or "")


def _td_bytes(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    ctype = content.get("@type")
    if ctype == "messageText":
        return _formatted_text(content.get("text"))
    if "caption" in content:
        return _formatted_text(content.get("caption"))
    return ""


def message_media_type(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    ctype = content.get("@type", "message")
    return ctype.replace("message", "", 1).lower() or "text"


def _reply_info(message: dict[str, Any]) -> dict[str, Any]:
    interaction = message.get("interaction_info") or {}
    return interaction.get("reply_info") or message.get("reply_info") or {}


def _reaction_total(message: dict[str, Any]) -> int:
    interaction = message.get("interaction_info") or {}
    reactions = interaction.get("reactions") or message.get("reactions") or {}
    total = 0
    for reaction in reactions.get("reactions", []) or reactions.get("results", []):
        total += int(
            reaction.get("total_count")
            or reaction.get("count")
            or reaction.get("reaction_count")
            or 0
        )
    return int(total or interaction.get("reaction_count") or message.get("reaction_count") or 0)


def normalize_chat(chat: dict[str, Any], handle: str | None = None) -> dict[str, Any]:
    usernames = chat.get("usernames") or {}
    active_usernames = usernames.get("active_usernames") or []
    username = active_usernames[0] if active_usernames else chat.get("username")
    return {
        "id": chat.get("id"),
        "handle": handle or (f"@{username}" if username else str(chat.get("id"))),
        "title": chat.get("title"),
        "username": username,
        "type": (chat.get("type") or {}).get("@type"),
        "raw": chat,
    }


def normalize_message(message: dict[str, Any], *, channel: str, username: str | None = None) -> dict[str, Any]:
    interaction = message.get("interaction_info") or {}
    reply_info = _reply_info(message)
    post_id = str(message.get("id"))
    handle = username or channel.lstrip("@")
    return {
        "id": post_id,
        "date": _date(message.get("date")),
        "text": message_text(message),
        "views": int(interaction.get("view_count") or message.get("views") or 0),
        "forwards": int(interaction.get("forward_count") or message.get("forwards") or 0),
        "replies": int(reply_info.get("reply_count") or message.get("replies") or 0),
        "reactions": _reaction_total(message),
        "media_type": message_media_type(message),
        "link": f"https://t.me/{handle}/{post_id}" if handle else None,
        "raw": message,
    }


class TDLibGateway:
    def __init__(self, config: ChappeConfig):
        self.config = config
        self._tdjson = None
        self._client = None
        self._extra = 0
        self._last_authorization_state: dict[str, Any] | None = None

    def _ensure_client(self):
        if self._tdjson and self._client:
            return
        self._tdjson = _load_tdjson_module()
        try:
            self._tdjson.td_execute(
                json.dumps({"@type": "setLogVerbosityLevel", "new_verbosity_level": 0}).encode("utf-8")
            )
        except Exception:
            pass
        self._client = self._tdjson.td_create_client_id()

    def close(self) -> None:
        if self._tdjson and self._client:
            try:
                self.send({"@type": "close"}, timeout=1)
            except Exception:
                pass
        self._tdjson = None
        self._client = None

    def execute(self, query: dict[str, Any]) -> dict[str, Any] | None:
        self._ensure_client()
        assert self._tdjson is not None
        raw = self._tdjson.td_execute(json.dumps(query).encode("utf-8"))
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))

    def _receive(self, timeout: float = 1.0) -> dict[str, Any] | None:
        self._ensure_client()
        assert self._tdjson is not None
        raw = self._tdjson.td_receive(timeout)
        if raw is None:
            return None
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("@type") == "updateAuthorizationState":
            self._last_authorization_state = payload.get("authorization_state")
        return payload

    def send(self, query: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
        self._ensure_client()
        assert self._tdjson is not None and self._client is not None
        self._extra += 1
        extra = f"chappe:{self._extra}"
        request = {**query, "@extra": extra}
        self._tdjson.td_send(self._client, json.dumps(request).encode("utf-8"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self._receive(timeout=0.5)
            if result is None:
                continue
            if result.get("@extra") != extra:
                continue
            if result.get("@type") == "error":
                raise ChappeError(
                    result.get("message") or "TDLib error",
                    ExitCode.TELEGRAM_ERROR,
                    details=result,
                )
            return result
        raise ChappeError(
            f"Timed out waiting for TDLib response to {query.get('@type')}.",
            ExitCode.TELEGRAM_ERROR,
        )

    def wait_for_authorization_state(self, *, timeout: float = 20.0) -> dict[str, Any]:
        self._ensure_client()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._last_authorization_state:
                return self._last_authorization_state
            update = self._receive(timeout=0.5)
            if not update:
                continue
            if update.get("@type") == "updateAuthorizationState":
                state = update.get("authorization_state") or {}
                self._last_authorization_state = state
                return state
        raise ChappeError("Timed out waiting for TDLib authorization state.", ExitCode.AUTH_ERROR)

    def _send_auth_request(self, query: dict[str, Any]) -> dict[str, Any]:
        result = self.send(query)
        if result.get("@type") == "error":
            raise ChappeError(
                result.get("message") or "TDLib error",
                ExitCode.AUTH_ERROR,
                details=result,
            )
        return result

    def configure(self) -> dict[str, Any]:
        if not self.config.telegram.api_id or not self.config.telegram.api_hash:
            raise ChappeError(
                "Telegram API credentials are missing.",
                ExitCode.AUTH_ERROR,
                next_command="chappe config init; export TELEGRAM_API_ID=... TELEGRAM_API_HASH=...",
            )
        self.config.ensure_dirs()
        state = self.send({"@type": "getAuthorizationState"})
        while True:
            stype = state.get("@type")
            if stype == "authorizationStateWaitTdlibParameters":
                key = self.config.telegram.database_encryption_key or os.getenv(
                    self.config.telegram.database_encryption_key_env, ""
                )
                encoded_key = _td_bytes(key)
                self._send_auth_request(
                    {
                        "@type": "setTdlibParameters",
                        "use_test_dc": False,
                        "database_directory": str(self.config.storage.tdlib_dir),
                        "files_directory": str(self.config.storage.tdlib_dir / "files"),
                        "database_encryption_key": encoded_key,
                        "use_file_database": True,
                        "use_chat_info_database": True,
                        "use_message_database": True,
                        "use_secret_chats": False,
                        "api_id": int(self.config.telegram.api_id),
                        "api_hash": self.config.telegram.api_hash,
                        "system_language_code": "en",
                        "device_model": "Chappe CLI",
                        "system_version": os.uname().sysname if hasattr(os, "uname") else "unknown",
                        "application_version": __version__,
                    }
                )
                state = self.send({"@type": "getAuthorizationState"})
                continue
            if stype == "authorizationStateWaitEncryptionKey":
                key = self.config.telegram.database_encryption_key or os.getenv(
                    self.config.telegram.database_encryption_key_env, ""
                )
                self._send_auth_request(
                    {"@type": "checkDatabaseEncryptionKey", "encryption_key": _td_bytes(key)}
                )
                state = self.send({"@type": "getAuthorizationState"})
                continue
            return state

    def authorization_state(self) -> dict[str, Any]:
        try:
            return self.configure()
        except ChappeError:
            raise

    def login_interactive(
        self,
        phone: str | None = None,
        *,
        code: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        state = self.configure()
        stype = state.get("@type")
        if stype == "authorizationStateReady":
            return {"authorized": True, "state": stype}
        if stype == "authorizationStateWaitPhoneNumber":
            if not phone:
                raise ChappeError(
                    "Phone number is required for TDLib login.",
                    ExitCode.USAGE_ERROR,
                    next_command="chappe auth login --phone +15551234567",
                )
            self._send_auth_request(
                {
                    "@type": "setAuthenticationPhoneNumber",
                    "phone_number": phone,
                    "settings": None,
                }
            )
            return {
                "authorized": False,
                "state": "authorizationStateWaitCode",
                "next_command": "chappe auth login --phone <phone> --code <telegram-code>",
            }
        if stype == "authorizationStateWaitCode":
            if not code:
                return {
                    "authorized": False,
                    "state": stype,
                    "next_command": "chappe auth login --code <telegram-code>",
                    "code_info": state.get("code_info"),
                }
            self._send_auth_request({"@type": "checkAuthenticationCode", "code": code})
            next_state = self.wait_for_authorization_state()
            if next_state.get("@type") == "authorizationStateWaitPassword":
                return {
                    "authorized": False,
                    "state": "authorizationStateWaitPassword",
                    "next_command": "chappe auth login --password <2fa-password>",
                }
            return {
                "authorized": next_state.get("@type") == "authorizationStateReady",
                "state": next_state.get("@type"),
            }
        if stype == "authorizationStateWaitPassword":
            if not password:
                return {
                    "authorized": False,
                    "state": stype,
                    "password_hint": state.get("password_hint"),
                    "next_command": "chappe auth login --password <2fa-password>",
                }
            self._send_auth_request({"@type": "checkAuthenticationPassword", "password": password})
            next_state = self.wait_for_authorization_state()
            return {
                "authorized": next_state.get("@type") == "authorizationStateReady",
                "state": next_state.get("@type"),
            }
        return {"authorized": False, "state": stype, "raw": state}

    def resolve_chat(self, handle: str) -> dict[str, Any]:
        if handle.startswith("@"):
            chat = self.send({"@type": "searchPublicChat", "username": handle[1:]})
            return normalize_chat(chat, handle)
        chat = self.send({"@type": "getChat", "chat_id": int(handle)})
        return normalize_chat(chat, handle)

    def chat_statistics(self, chat_id: int | str) -> dict[str, Any]:
        return self.send({"@type": "getChatStatistics", "chat_id": int(chat_id), "is_dark": False})

    def chat_history(self, chat_id: int | str, *, limit: int = 100, from_message_id: int = 0) -> dict[str, Any]:
        target = max(0, int(limit))
        if target == 0:
            return {"@type": "messages", "total_count": 0, "messages": [], "next_from_message_id": None}

        collected: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        cursor = int(from_message_id)
        total_count: int | None = None
        empty_batches = 0

        while len(collected) < target:
            remaining = target - len(collected)
            batch_limit = min(100, remaining + (1 if cursor else 0))
            result = self.send(
                {
                    "@type": "getChatHistory",
                    "chat_id": int(chat_id),
                    "from_message_id": cursor,
                    "offset": 0,
                    "limit": batch_limit,
                    "only_local": False,
                }
            )
            if total_count is None and result.get("total_count") is not None:
                total_count = int(result["total_count"])

            messages = result.get("messages") or []
            new_messages: list[dict[str, Any]] = []
            for message in messages:
                message_id = message.get("id")
                if message_id is None or int(message_id) in seen_ids:
                    continue
                seen_ids.add(int(message_id))
                new_messages.append(message)

            if not new_messages:
                empty_batches += 1
                if empty_batches >= 2:
                    break
                continue

            empty_batches = 0
            collected.extend(new_messages)
            next_cursor = new_messages[-1].get("id")
            if not next_cursor or int(next_cursor) == cursor:
                break
            cursor = int(next_cursor)

            if len(messages) < batch_limit and len(collected) >= target:
                break

        return {
            "@type": "messages",
            "total_count": total_count if total_count is not None else len(collected),
            "messages": collected[:target],
            "next_from_message_id": cursor or None,
        }

    def message_thread(self, chat_id: int | str, message_id: int | str, *, limit: int = 100) -> dict[str, Any]:
        return self.send(
            {
                "@type": "getMessageThreadHistory",
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "from_message_id": 0,
                "offset": 0,
                "limit": min(int(limit), 100),
            }
        )

    def search_messages(self, chat_id: int | str, query: str, *, limit: int = 100) -> dict[str, Any]:
        return self.send(
            {
                "@type": "searchChatMessages",
                "chat_id": int(chat_id),
                "topic_id": None,
                "query": query,
                "sender_id": None,
                "from_message_id": 0,
                "offset": 0,
                "limit": min(int(limit), 100),
                "filter": None,
            }
        )

    def similar_chats(self, chat_id: int | str) -> dict[str, Any]:
        return self.send({"@type": "getChatSimilarChats", "chat_id": int(chat_id)})

    def send_text(self, chat_id: int | str, text: str) -> dict[str, Any]:
        return self.send(
            {
                "@type": "sendMessage",
                "chat_id": int(chat_id),
                "message_thread_id": 0,
                "reply_to": None,
                "options": None,
                "reply_markup": None,
                "input_message_content": {
                    "@type": "inputMessageText",
                    "text": {"@type": "formattedText", "text": text, "entities": []},
                    "link_preview_options": None,
                    "clear_draft": False,
                },
            }
        )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
