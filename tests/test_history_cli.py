import json

import pytest
from typer.testing import CliRunner

import chappe.history_cli as history_cli
from chappe.cli import app
from chappe.errors import ChappeError, ExitCode


runner = CliRunner()


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.state = {"@type": "authorizationStateReady"}
        self.messages = []

    def authorization_state(self):
        self.calls.append("auth")
        return self.state

    def close(self):
        self.closed = True

    def send(self, request):
        self.calls.append(request)
        method = request["@type"]
        if method == "searchChats":
            return {"chat_ids": [-10, -20]}
        if method == "searchChatsOnServer":
            return {"chat_ids": [-20, -30]}
        if method == "getChat":
            return {
                "id": request["chat_id"],
                "title": "Example project",
                "type": {"@type": "chatTypeSupergroup", "is_channel": False},
                "last_message": {"content": "do not export previews"},
                "permissions": {"unneeded": True},
            }
        raise AssertionError(request)

    def resolve_chat(self, handle):
        self.calls.append({"resolve_chat": handle})
        raw = {"id": 42, "title": "Example collaborator", "type": {"@type": "chatTypePrivate"}}
        return {"id": 42, "raw": raw, "username": "example_user"}

    def chat_history(self, chat_id, **kwargs):
        self.calls.append({"chat_history": chat_id, **kwargs})
        return {"messages": self.messages, "total_count": 999999}


@pytest.fixture
def gateway(monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr(history_cli, "TDLibGateway", lambda config: gateway)
    return gateway


def invoke(tmp_path, args):
    return runner.invoke(app, ["chat", *args], env={"CHAPPE_HOME": str(tmp_path)})


def test_search_combines_known_chat_queries_without_message_previews(tmp_path, gateway):
    result = invoke(tmp_path, ["search", "Example"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [chat["id"] for chat in payload["chats"]] == ["-10", "-20", "-30"]
    assert all(chat["type"] == "chatTypeSupergroup" for chat in payload["chats"])
    assert all(chat["is_channel"] is False for chat in payload["chats"])
    assert payload["complete"] is False
    assert payload["possibly_truncated"] is False
    assert "last_message" not in result.stdout
    assert "permissions" not in result.stdout
    assert gateway.calls[0] == "auth"
    assert gateway.calls[1:3] == [
        {"@type": "searchChats", "query": "Example", "limit": 100},
        {"@type": "searchChatsOnServer", "query": "Example", "limit": 100},
    ]
    assert gateway.closed


def test_search_marks_possible_truncation(tmp_path, gateway):
    result = invoke(tmp_path, ["search", "Example", "--limit", "2"])
    payload = json.loads(result.stdout)
    assert payload["returned_count"] == 2
    assert payload["possibly_truncated"] is True


def test_history_exports_only_content_and_ids_with_exclusive_cursor(tmp_path, gateway):
    gateway.messages = [
        {"id": 300, "date": 1700000000},
        {
            "id": 200,
            "date": 1700000000,
            "edit_date": 1700000010,
            "sender_id": {"@type": "messageSenderUser", "user_id": 7, "phone_number": "hidden"},
            "content": {"@type": "messageText", "text": {"text": "Decision rationale"}},
            "reply_to": {"@type": "messageReplyToMessage", "chat_id": 42, "message_id": 100},
        },
        {
            "id": 100,
            "date": 1700000000,
            "sender_id": {"@type": "messageSenderChat", "chat_id": -9},
            "content": {
                "@type": "messagePhoto",
                "caption": {"text": "Example output"},
                "photo": {"file": {"local_path": "/private/unneeded"}},
            },
        },
    ]
    result = invoke(tmp_path, ["history", "@example_user", "--limit", "2", "--from-message-id", "300"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["next_from_message_id"] == "100"
    assert payload["stop_reason"] == "limit_reached"
    assert payload["complete"] is False
    assert payload["chat"]["type"] == "chatTypePrivate"
    assert gateway.calls[-1] == {"chat_history": 42, "limit": 3, "from_message_id": 300}
    first, second = payload["messages"]
    assert first["source_id"] == "telegram:chat:42:message:200"
    assert first["text"] == "Decision rationale"
    assert first["date"] == "2023-11-14T22:13:20+00:00"
    assert first["reply_to"]["message_id"] == "100"
    assert first["sender"]["user_id"] == "7"
    assert second["caption"] == "Example output"
    assert second["sender"]["chat_id"] == "-9"
    assert "hidden" not in result.stdout
    assert "/private/" not in result.stdout
    assert "t.me/" not in result.stdout
    assert "999999" not in result.stdout
    assert gateway.closed


def test_history_deduplicates_sorts_and_caps_result(tmp_path, gateway):
    gateway.messages = [{"id": i} for i in [10, 20, 20, 30]]
    result = invoke(tmp_path, ["history", "--limit", "2", "--", "-42"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [message["message_id"] for message in payload["messages"]] == ["30", "20"]
    assert payload["next_from_message_id"] == "20"


@pytest.mark.parametrize("ids", [[], [300]])
def test_no_cursor_progress_is_not_complete_history(tmp_path, gateway, ids):
    gateway.messages = [{"id": value} for value in ids]
    result = invoke(tmp_path, ["history", "42", "--from-message-id", "300"])
    payload = json.loads(result.stdout)
    assert payload["messages"] == []
    assert payload["next_from_message_id"] is None
    assert payload["stop_reason"] == "no_more_rows_returned"
    assert payload["complete"] is False


@pytest.mark.parametrize("args", [["search", "Example"], ["history", "@example_user"]])
def test_auth_required_before_access_and_no_sensitive_state_output(tmp_path, gateway, args):
    gateway.state = {"@type": "authorizationStateWaitCode", "code_info": {"phone": "private"}}
    result = invoke(tmp_path, args)
    assert result.exit_code == ExitCode.AUTH_ERROR
    payload = json.loads(result.stderr)
    assert payload["error"]["details"] == {"authorization_state": "authorizationStateWaitCode"}
    assert "private" not in result.output
    assert gateway.calls == ["auth"]
    assert gateway.closed


def test_gateway_closes_when_read_fails(tmp_path, gateway):
    def broken(_request):
        raise ChappeError("Read failed", ExitCode.TELEGRAM_ERROR)

    gateway.send = broken
    result = invoke(tmp_path, ["search", "Example"])
    assert result.exit_code == ExitCode.TELEGRAM_ERROR
    assert json.loads(result.stderr)["error"]["message"] == "Read failed"
    assert gateway.closed


def test_gateway_closes_when_auth_check_fails(tmp_path, gateway):
    def broken():
        raise ChappeError("Auth failed", ExitCode.AUTH_ERROR)

    gateway.authorization_state = broken
    result = invoke(tmp_path, ["search", "Example"])
    assert result.exit_code == ExitCode.AUTH_ERROR
    assert gateway.closed


@pytest.mark.parametrize("args", [["search", " "], ["history", "Example title"]])
def test_invalid_query_does_not_open_gateway(tmp_path, gateway, args):
    result = invoke(tmp_path, args)
    assert result.exit_code == ExitCode.USAGE_ERROR
    assert json.loads(result.stderr)["ok"] is False
    assert gateway.calls == []
    assert not gateway.closed
