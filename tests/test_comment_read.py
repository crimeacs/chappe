from copy import deepcopy

import pytest
from typer.testing import CliRunner

import chappe.cli as cli
import chappe.comment_read as comment_module
from chappe.comment_read import SERVER_ID_UNIT as U, read_comments, validate_public_post_url
from chappe.errors import ChappeError

URL = "https://t.me/nn_for_science/2740"


def message(n, *, chat=-200, text="comment", root=100):
    return {
        "id": n * U,
        "chat_id": chat,
        "date": 1,
        "message_thread_id": root * U,
        "reply_to": {"@type": "messageReplyToMessage", "chat_id": chat, "message_id": root * U},
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": text}},
    }


class Gateway:
    def __init__(self, pages, count, *, end_count=None):
        self.pages = iter(pages)
        self.count = count
        self.end_count = count if end_count is None else end_count
        self.calls = []
        self.post = message(2740, chat=-100, text="exact post")
        self.post["interaction_info"] = {"reply_info": {"reply_count": count}}

    def send(self, query):
        self.calls.append(query)
        kind = query["@type"]
        if kind == "getMessageLinkInfo":
            return {"is_public": True, "chat_id": -100, "message": deepcopy(self.post)}
        if kind == "getMessageThread":
            count = (
                self.end_count
                if any(q["@type"] == "getMessage" for q in self.calls)
                else self.count
            )
            return {
                "chat_id": -200,
                "message_thread_id": 100 * U,
                "messages": [message(100, text="discussion root"), deepcopy(self.post)],
                "reply_info": {"reply_count": count},
            }
        if kind == "getMessageThreadHistory":
            return {"messages": deepcopy(next(self.pages)), "total_count": self.count}
        if kind == "getMessage":
            post = deepcopy(self.post)
            post["interaction_info"]["reply_info"]["reply_count"] = self.end_count
            return post
        raise AssertionError(kind)


def test_exact_post_root_exclusion_nested_edges_and_empty_terminal():
    photo = message(105)
    photo.pop("message_thread_id")
    photo["topic_id"] = {"@type": "messageTopicThread", "message_thread_id": 100 * U}
    photo["content"] = {
        "@type": "messagePhoto",
        "caption": {"@type": "formattedText", "text": "caption exact"},
    }
    reply = message(104)
    reply["reply_to"]["message_id"] = 105 * U
    gateway = Gateway([[photo, reply, message(100, text="root")], []], 2)
    result = read_comments(gateway, URL)
    assert result["coverage"]["complete"]
    assert result["coverage"]["root_rows_excluded"] == 1
    assert result["coverage"]["stop_reason"] == "empty_terminal_page"
    assert result["comments"][0]["reply_to"]["message_id"] == str(105 * U)
    assert result["comments"][1]["caption"] == "caption exact"
    assert result["comments"][1]["message_thread_id"] is None
    assert result["comments"][1]["topic_id"] == photo["topic_id"]
    history = [q for q in gateway.calls if q["@type"] == "getMessageThreadHistory"]
    assert history[0]["chat_id"] == -200
    assert history[0]["message_id"] == 100 * U
    assert history[1]["from_message_id"] == 99 * U
    assert not any("link" in c for c in result["comments"])


def test_more_than_100_short_pages_dedup_and_terminal():
    pages = [[message(i)] for i in range(205, 100, -1)] + [[message(101)], []]
    result = read_comments(Gateway(pages, 105), URL)
    assert len(result["comments"]) == 105
    assert result["coverage"]["duplicates_removed"] == 1
    assert result["coverage"]["complete"] is False  # repeated server page fails progress honestly
    # A full >100-message scan needs an empty page; no100-message clamp.
    result = read_comments(
        Gateway(
            [
                [message(i) for i in range(250, 150, -1)],
                [message(i) for i in range(150, 100, -1)],
                [],
            ],
            150,
        ),
        URL,
    )
    assert result["coverage"]["complete"] and len(result["comments"]) == 150


@pytest.mark.parametrize(
    "kwargs,reason", [({"max_pages": 1}, "max_pages"), ({"max_messages": 1}, "max_messages")]
)
def test_limits_never_claim_complete(kwargs, reason):
    result = read_comments(Gateway([[message(103), message(102)]], 2), URL, **kwargs)
    assert not result["ok"]
    assert result["coverage"]["stop_reason"] == reason


def test_changed_count_and_cursor_only_page_not_silently_complete():
    result = read_comments(Gateway([[message(103)], []], 1, end_count=2), URL)
    assert not result["coverage"]["complete"]
    assert result["coverage"]["accessible_history_terminal"]
    result = read_comments(Gateway([[message(103)], [message(103)]], 1), URL)
    assert not result["coverage"]["complete"]
    assert result["coverage"]["stop_reason"] == "cursor_did_not_decrease"


def test_link_neighbor_result_refused():
    gateway = Gateway([], 0)
    gateway.post["id"] = 2733 * U
    with pytest.raises(ChappeError, match="exact requested post"):
        read_comments(gateway, URL)


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/c/123/4",
        "https://evil.test/nn_for_science/2740",
        "https://t.me/nn_for_science/2740?comment=5",
        "https://t.me:bad/a/1",
        "@nn_for_science",
        "https://t.me/nn_for_science/0",
    ],
)
def test_invalid_public_post_links(url):
    with pytest.raises(ChappeError):
        validate_public_post_url(url)


def test_cli_refuses_before_auth_and_closes(monkeypatch, tmp_path):
    class Unauthorized:
        closed = False

        def __init__(self, config):
            pass

        def configure(self):
            return {"@type": "authorizationStateWaitPhoneNumber"}

        def close(self):
            Unauthorized.closed = True

        def send(self, query):
            raise AssertionError("History accessed before authReady")

    monkeypatch.setattr(cli, "TDLibGateway", Unauthorized)
    result = CliRunner().invoke(
        cli.app, ["comments", "read", URL], env={"CHAPPE_HOME": str(tmp_path)}
    )
    assert result.exit_code != 0
    assert "not ready" in result.output
    assert Unauthorized.closed


def photo_gateway(tmp_path, *, complete=True):
    cached = tmp_path / "synthetic-photo-cache.jpg"
    cached.write_bytes(b"synthetic JPEG fixture")
    raw = message(105)
    raw["content"] = {
        "@type": "messagePhoto",
        "caption": {"@type": "formattedText", "text": "photo caption"},
        "photo": {
            "sizes": [
                {"width": 10, "height": 10, "photo": {"id": 11, "size": 1}},
                {"width": 100, "height": 100, "photo": {"id": 22, "size": cached.stat().st_size}},
            ]
        },
    }

    class PhotoGateway:
        calls = []

        def send(self, query, **kwargs):
            self.calls.append(query)
            if query["@type"] == "getMessage":
                return raw
            assert query["@type"] == "downloadFile"
            return {"local": {"path": str(cached), "is_downloading_completed": complete}}

    return PhotoGateway(), cached


def test_photo_export_only_selected_largest_and_no_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(
        comment_module,
        "read_comments",
        lambda *a: {"url": URL, "discussion_chat_id": "-200", "comments": [{"id": str(105 * U)}]},
    )
    gateway, cached = photo_gateway(tmp_path)
    output = tmp_path / "result.jpg"
    result = comment_module.download_comment_photo(gateway, URL, 105 * U, str(output))
    assert output.read_bytes() == cached.read_bytes()
    assert result["caption"] == "photo caption"
    assert [q["@type"] for q in gateway.calls] == ["getMessage", "downloadFile"]
    assert gateway.calls[-1]["file_id"] == 22
    assert gateway.calls[-1]["limit"] == 10 * 1024 * 1024 + 1
    with pytest.raises(ChappeError, match="new file"):
        comment_module.download_comment_photo(gateway, URL, 105 * U, str(output))


def test_photo_wrong_comment_never_fetches_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        comment_module,
        "read_comments",
        lambda *a: {"url": URL, "discussion_chat_id": "-200", "comments": []},
    )
    gateway, _ = photo_gateway(tmp_path)
    with pytest.raises(ChappeError, match="not an accessible comment"):
        comment_module.download_comment_photo(gateway, URL, 105 * U, str(tmp_path / "out.jpg"))
    assert not gateway.calls


@pytest.mark.parametrize("complete,limit", [(False, 1000), (True, 2)])
def test_photo_partial_or_oversize_not_exported(monkeypatch, tmp_path, complete, limit):
    monkeypatch.setattr(
        comment_module,
        "read_comments",
        lambda *a: {"url": URL, "discussion_chat_id": "-200", "comments": [{"id": str(105 * U)}]},
    )
    gateway, _ = photo_gateway(tmp_path, complete=complete)
    output = tmp_path / "out.jpg"
    with pytest.raises(ChappeError):
        comment_module.download_comment_photo(gateway, URL, 105 * U, str(output), max_bytes=limit)
    assert not output.exists()
