from __future__ import annotations

from uuid import uuid4

from app.constants.notification_constants import MessageDirection, NotificationType
from app.domain.models import DeliveryJob, JobKind
from app.pipeline.push_message_builder import build_push_item, build_title
from app.domain.models import DeliveryTarget


def test_build_title_for_sms():
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        notification_type=NotificationType.SMS.value,
        sender="+15551234567",
    )
    assert build_title(job) == "New SMS from +15551234567"


def test_build_push_item_includes_worker_payload():
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=uuid4(),
        notification_type=NotificationType.SMS.value,
        sender="+15551234567",
        preview_text="Hello",
        direction=MessageDirection.INBOUND.value,
    )
    target = DeliveryTarget(
        device_token_id=uuid4(),
        push_token="ExponentPushToken[abc]",
    )
    item = build_push_item(job, target)
    assert item["push_token"] == "ExponentPushToken[abc]"
    assert item["title"] == "New SMS from +15551234567"
    assert item["body"] == "Hello"
    assert item["data"]["notificationId"] == str(job.notification_id)
