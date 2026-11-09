import hashlib
import json

import pytest
from typer.testing import CliRunner

import chappe.history_cli as history_cli
from chappe.cli import app
from chappe.errors import ExitCode


class Gateway:
    def __init__(self, tmp_path):
        self.calls = []
        self.closed = False
        self.local = tmp_path / "cached.bin"
        self.local.write_bytes(b"test media")
        self.complete = True
        self.size = 10

    def authorization_state(self):
        return {"@type": "authorizationStateReady"}

    def close(self):
        self.closed = True

    def send(self, request, **kwargs):
        self.calls.append(request)
        if request["@type"] == "getUser":
            return {
                "id": 7, "first_name": "", "last_name": "",
                "type": {"@type": "userTypeDeleted"}, "phone_number": "hidden",
            }
        if request["@type"] == "getMessage":
            return {
                "id": request["message_id"], "date": 1700000000,
                "content": {"@type": "messageVideo", "video": {
                    "video": {"id": 42, "size": self.size, "remote": {"id": "private"}},
                    "file_name": "../../unsafe.mp4", "duration": 3,
                }},
            }
        if request["@type"] == "createPrivateChat":
            return {
                "id": request["user_id"], "title": "Deleted Account",
                "type": {"@type": "chatTypePrivate", "user_id": request["user_id"]},
                "last_message": {"content": "do not disclose"},
            }
        if request["@type"] == "downloadFile":
            return {"local": {"path": str(self.local), "is_downloading_completed": self.complete}}
        raise AssertionError(request)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    gateway = Gateway(tmp_path)
    monkeypatch.setattr(history_cli, "TDLibGateway", lambda config: gateway)
    return gateway


def invoke(tmp_path, args):
    return CliRunner().invoke(app, ["chat", *args], env={"CHAPPE_HOME": str(tmp_path)})


def test_one_message_metadata_does_not_download_or_disclose_remote_id(tmp_path, gateway):
    result = invoke(tmp_path, ["message", "--", "-42", "100"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["media"]["file_id"] == 42
    assert payload["message"]["source_id"] == "telegram:chat:-42:message:100"
    assert "private" not in result.stdout
    assert [x["@type"] for x in gateway.calls] == ["getMessage"]
    assert gateway.closed


def test_user_metadata_omits_phone(tmp_path, gateway):
    result = invoke(tmp_path, ["user", "7"])
    assert json.loads(result.stdout)["user"]["type"] == "userTypeDeleted"
    assert "hidden" not in result.stdout
    assert "phone" not in result.stdout


def test_resolve_user_returns_only_chat_identity_without_reading_messages(tmp_path, gateway):
    result = invoke(tmp_path, ["resolve-user", "7"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["chat"]["id"] == "7"
    assert json.loads(result.stdout)["messages_read"] is False
    assert "do not disclose" not in result.stdout
    assert gateway.calls == [{"@type": "createPrivateChat", "user_id": 7, "force": False}]
    assert gateway.closed


def test_download_exports_exact_bytes_and_source_hash_with_audit(tmp_path, gateway, monkeypatch):
    events = []
    monkeypatch.setattr(history_cli.Store, "audit", lambda self, *args: events.append(args))
    output = tmp_path / "export" / "chosen.mp4"
    result = invoke(tmp_path, ["download", "42", "100", "--output", str(output)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert output.read_bytes() == b"test media"
    assert payload["download"]["sha256"] == hashlib.sha256(b"test media").hexdigest()
    assert not (tmp_path / "unsafe.mp4").exists()
    assert gateway.calls[-1] == {
        "@type": "downloadFile", "file_id": 42, "priority": 1,
        "offset": 0, "limit": 250_000_000, "synchronous": True,
    }
    assert gateway.closed
    assert events[0][0] == "chat_media_download"
    assert events[0][1]["source_id"] == "telegram:chat:42:message:100"
    assert events[0][1]["sha256"] == payload["download"]["sha256"]


def test_download_refuses_overwrite_before_gateway_access(tmp_path, gateway):
    output = tmp_path / "exists.mp4"
    output.write_text("keep")
    result = invoke(tmp_path, ["download", "42", "100", "--output", str(output)])
    assert result.exit_code == ExitCode.USAGE_ERROR
    assert not gateway.calls
    assert output.read_text() == "keep"


def test_failed_copy_removes_partial_export(tmp_path, gateway, monkeypatch):
    output = tmp_path / "partial.mp4"
    def fail_copy(src, dst):
        dst.write(b"partial")
        raise OSError("disk write failed")
    monkeypatch.setattr(history_cli.shutil, "copyfileobj", fail_copy)
    result = invoke(tmp_path, ["download", "42", "100", "--output", str(output)])
    assert result.exit_code == ExitCode.TELEGRAM_ERROR
    assert not output.exists()
    assert gateway.closed


@pytest.mark.parametrize("failure", ["too_large", "incomplete"])
def test_download_does_not_export_oversize_or_incomplete_files(tmp_path, gateway, failure):
    output = tmp_path / "output.mp4"
    if failure == "too_large":
        gateway.size = 999999999
    else:
        gateway.complete = False
    result = invoke(tmp_path, ["download", "42", "100", "--output", str(output)])
    assert result.exit_code != 0
    assert not output.exists()
    if failure == "too_large":
        assert [x["@type"] for x in gateway.calls] == ["getMessage"]
    assert gateway.closed
