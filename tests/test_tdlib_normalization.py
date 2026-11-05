import json

from chappe.config import ChappeConfig
from chappe.tdlib import TDLibGateway, normalize_message


def test_normalize_message_extracts_tdlib_metrics():
    msg = {
        "id": 123,
        "date": 1700000000,
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": "hello"}},
        "interaction_info": {
            "view_count": 100,
            "forward_count": 5,
            "reactions": {"reactions": [{"total_count": 7}]},
        },
        "reply_info": {"reply_count": 2},
    }
    post = normalize_message(msg, channel="@x", username="x")
    assert post["text"] == "hello"
    assert post["forwards"] == 5
    assert post["reactions"] == 7
    assert post["link"] == "https://t.me/x/123"


def test_normalize_message_extracts_nested_reply_info():
    msg = {
        "id": 124,
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": "hello"}},
        "interaction_info": {
            "view_count": 100,
            "forward_count": 5,
            "reply_info": {"@type": "messageReplyInfo", "reply_count": 9},
        },
    }

    post = normalize_message(msg, channel="@x", username="x")

    assert post["replies"] == 9


def test_normalize_message_extracts_reaction_count_fallback():
    msg = {
        "id": 125,
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": "hello"}},
        "interaction_info": {"reaction_count": 4},
    }

    post = normalize_message(msg, channel="@x", username="x")

    assert post["reactions"] == 4


def test_normalize_message_excludes_paid_reactions():
    msg = {
        "id": 126,
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": "hello"}},
        "interaction_info": {
            "reaction_count": 1107,
            "reactions": {
                "reactions": [
                    {"type": {"@type": "reactionTypePaid"}, "total_count": 1103},
                    {"type": {"@type": "reactionTypeEmoji", "emoji": "🔥"}, "total_count": 4},
                ]
            },
        },
    }

    post = normalize_message(msg, channel="@x", username="x")

    assert post["reactions"] == 4


class FakeGateway:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def send(self, query, *, timeout=20.0):
        self.calls.append(query)
        return self.responses.get(query["from_message_id"], {"@type": "messages", "messages": []})


def test_chat_history_paginates_until_requested_limit():
    gateway = FakeGateway(
        {
            0: {"@type": "messages", "total_count": 3, "messages": [{"id": 30}, {"id": 20}]},
            20: {"@type": "messages", "total_count": 3, "messages": [{"id": 20}, {"id": 10}]},
        }
    )

    history = TDLibGateway.chat_history(gateway, 123, limit=3)

    assert [message["id"] for message in history["messages"]] == [30, 20, 10]
    assert history["total_count"] == 3
    assert history["next_from_message_id"] == 10
    assert [call["from_message_id"] for call in gateway.calls] == [0, 20]
    assert [call["limit"] for call in gateway.calls] == [3, 2]


def test_chat_history_handles_zero_limit():
    gateway = FakeGateway({})

    history = TDLibGateway.chat_history(gateway, 123, limit=0)

    assert history == {
        "@type": "messages",
        "total_count": 0,
        "messages": [],
        "next_from_message_id": None,
    }
    assert gateway.calls == []


def test_tdlib_client_disables_native_logs(monkeypatch, tmp_path):
    class FakeTDJson:
        def __init__(self):
            self.executed = []

        def td_execute(self, payload):
            self.executed.append(json.loads(payload.decode("utf-8")))
            return None

        def td_create_client_id(self):
            return 1

    fake = FakeTDJson()
    monkeypatch.setattr("chappe.tdlib._load_tdjson_module", lambda: fake)

    gateway = TDLibGateway(ChappeConfig.load(tmp_path / "config.toml"))
    gateway._ensure_client()

    assert fake.executed[0] == {"@type": "setLogVerbosityLevel", "new_verbosity_level": 0}


def test_configure_restarts_client_after_logout_finishes(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    gateway = TDLibGateway(ChappeConfig.load(tmp_path / "config.toml"))
    gateway._tdjson = object()
    gateway._client = 1
    gateway._last_authorization_state = {"@type": "authorizationStateLoggingOut"}
    states = iter(
        [
            {"@type": "authorizationStateLoggingOut"},
            {"@type": "authorizationStateWaitPhoneNumber"},
        ]
    )

    def fake_send(query, *, timeout=20.0):
        assert query == {"@type": "getAuthorizationState"}
        return next(states)

    def fake_wait(current_type, *, timeout=20.0):
        assert current_type == "authorizationStateLoggingOut"
        return {"@type": "authorizationStateClosed"}

    monkeypatch.setattr(gateway, "send", fake_send)
    monkeypatch.setattr(gateway, "wait_for_authorization_state_change", fake_wait)

    state = gateway.configure()

    assert state == {"@type": "authorizationStateWaitPhoneNumber"}
    assert gateway._tdjson is None
    assert gateway._client is None
    assert gateway._last_authorization_state is None
