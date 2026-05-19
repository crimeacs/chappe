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
