import json
from collections import deque

import pytest

from chappe.config import ChappeConfig
from chappe.errors import ChappeError, ExitCode
from chappe.tdlib import TDLibGateway


def authorization_update(state):
    return {"@type": "updateAuthorizationState", "authorization_state": {"@type": state}}


class FakeTDJson:
    def __init__(self, events):
        self.events = deque(events)
        self.requests = []
        self.elapsed = 0.0
        self.receive_calls = 0

    def td_send(self, client, payload):
        self.requests.append((client, json.loads(payload)))

    def td_receive(self, timeout):
        self.receive_calls += 1
        self.elapsed += timeout
        event = self.events.popleft() if self.events else None
        return json.dumps(event).encode("utf-8") if event is not None else None


@pytest.fixture
def make_gateway(monkeypatch, tmp_path):
    def make(events, state="authorizationStateReady"):
        transport = FakeTDJson(events)
        monkeypatch.setattr("chappe.tdlib.time.monotonic", lambda: transport.elapsed)
        gateway = TDLibGateway(ChappeConfig.load(tmp_path / "config.toml"))
        gateway._tdjson = transport
        gateway._client = 1
        gateway._last_authorization_state = {"@type": state}
        return gateway, transport

    return make


def test_close_waits_past_acknowledgment_for_delayed_closed_update(make_gateway):
    gateway, transport = make_gateway(
        [
            {"@type": "ok", "@extra": "chappe:1"},
            authorization_update("authorizationStateClosing"),
            None,
            {"@type": "updateOption", "name": "connection_state"},
            authorization_update("authorizationStateClosed"),
        ]
    )

    gateway.close()

    assert transport.elapsed > 1.0
    assert not transport.events
    assert transport.requests == [(1, {"@type": "close", "@extra": "chappe:1"})]
    assert gateway._tdjson is None
    assert gateway._client is None
    assert gateway._last_authorization_state is None
    gateway.close()
    assert len(transport.requests) == 1


def test_close_does_not_create_an_uninitialized_client(monkeypatch, tmp_path):
    def unexpected_import():
        pytest.fail("Closing an unused gateway must not load TDLib")

    monkeypatch.setattr("chappe.tdlib._load_tdjson_module", unexpected_import)
    gateway = TDLibGateway(ChappeConfig.load(tmp_path / "config.toml"))

    gateway.close()

    assert gateway._client is None


def test_close_does_not_send_to_an_already_closed_client(make_gateway):
    gateway, transport = make_gateway([], state="authorizationStateClosed")

    gateway.close()

    assert not transport.requests
    assert transport.receive_calls == 0
    assert gateway._client is None


def test_close_only_drains_an_already_closing_client(make_gateway):
    gateway, transport = make_gateway(
        [None, authorization_update("authorizationStateClosed")],
        state="authorizationStateClosing",
    )

    gateway.close()

    assert not transport.requests
    assert not transport.events
    assert gateway._client is None


def test_close_timeout_is_reported_and_preserves_client_for_retry(make_gateway):
    gateway, transport = make_gateway(
        [
            {"@type": "ok", "@extra": "chappe:1"},
            authorization_update("authorizationStateClosing"),
        ]
    )

    with pytest.raises(ChappeError, match="finish closing") as caught:
        gateway.close(timeout=2.25)

    assert caught.value.code == ExitCode.TELEGRAM_ERROR
    assert transport.elapsed == 2.25
    assert gateway._tdjson is transport
    assert gateway._client == 1
    transport.events.append(authorization_update("authorizationStateClosed"))
    gateway.close()
    assert len(transport.requests) == 1
    assert gateway._client is None


def test_close_does_not_suppress_request_errors(make_gateway):
    gateway, transport = make_gateway(
        [{"@type": "error", "@extra": "chappe:1", "code": 500, "message": "Close failed"}]
    )

    with pytest.raises(ChappeError, match="Close failed"):
        gateway.close()

    assert gateway._tdjson is transport
    assert gateway._client == 1


def test_close_recognizes_closed_update_received_before_ack(make_gateway):
    gateway, transport = make_gateway(
        [
            authorization_update("authorizationStateClosed"),
            {"@type": "ok", "@extra": "chappe:1"},
        ]
    )

    gateway.close()

    assert transport.receive_calls == 2
    assert gateway._client is None
