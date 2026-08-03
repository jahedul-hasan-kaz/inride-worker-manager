import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.pipeline.notification_pubsub_client import (
    NotificationPubSubClient,
    build_pubsub_push_message,
)


def test_build_pubsub_push_message_matches_agent_format():
    message = build_pubsub_push_message(
        push_token="ExponentPushToken[abc]",
        title="New SMS from thread-1",
        body="hello",
        data={"notificationId": "notif-1", "threadId": "thread-1"},
    )

    assert message == {
        "to": "ExponentPushToken[abc]",
        "medium": "push_notification",
        "args": {
            "title": "New SMS from thread-1",
            "body": "hello",
            "data": {"notificationId": "notif-1", "threadId": "thread-1"},
            "sound": "default",
        },
    }


def test_send_push_batch_publishes_each_item(monkeypatch):
    published: list[dict] = []
    fake_publisher = SimpleNamespace(
        publish_message=lambda data: published.append(json.loads(data)),
    )
    monkeypatch.setattr(
        "app.pipeline.notification_pubsub_client.notification_pubsub",
        SimpleNamespace(publisher=fake_publisher),
    )

    device_token_id = uuid4()
    client = NotificationPubSubClient()
    response = client.send_push_batch(
        [
            {
                "device_token_id": str(device_token_id),
                "push_token": "ExponentPushToken[token-1]",
                "title": "New Email from sender@example.com",
                "body": "hello",
                "data": {"threadId": "thread-root-1"},
            }
        ]
    )

    assert response.sent_count == 1
    assert response.failed_count == 0
    assert len(published) == 1
    assert published[0]["medium"] == "push_notification"
    assert published[0]["to"] == "ExponentPushToken[token-1]"


def test_send_push_batch_fails_when_publisher_missing():
    client = NotificationPubSubClient()
    response = client.send_push_batch(
        [
            {
                "device_token_id": str(uuid4()),
                "push_token": "ExponentPushToken[token-1]",
                "title": "title",
                "body": "body",
                "data": {},
            }
        ]
    )
    assert response.sent_count == 0
    assert response.failed_count == 1
