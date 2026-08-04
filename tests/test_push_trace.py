from __future__ import annotations

from uuid import UUID

from app.pipeline.push_trace import PushTrace, _short_id


def test_short_id_truncates_uuid():
    value = UUID("169d806f-aab4-4086-bfd6-baaa94ab942e")
    assert _short_id(value) == "169d806f…"


def test_push_trace_event_format():
    from loguru import logger

    messages: list[str] = []

    def sink(message):
        messages.append(message.record["message"])

    handler_id = logger.add(sink, level="INFO")
    try:
        trace = PushTrace(UUID("169d806f-aab4-4086-bfd6-baaa94ab942e"))
        trace.start(notification_type="sms", direction="inbound", tenant_id="tenant-1")
        trace.match(candidates=3, flagged=2, manual_reply=1, thread_id="+10000000000")
        trace.eligible_user(
            user_id=UUID("8f58e9d0-1955-414c-9f6b-cfd3d76727f6"),
            devices=2,
            active_devices=1,
            reasons=["flagged"],
        )
        trace.idempotency_skip(prepared=0, skipped=2, total=2)
        trace.complete(publish_count=0, resolved_devices=2, skipped=2, status="sent")
    finally:
        logger.remove(handler_id)

    assert any("[169d806f…] START" in message for message in messages)
    assert any("[169d806f…] MATCH candidates=3" in message for message in messages)
    assert any("[169d806f…] ELIGIBLE user=8f58e9d0…" in message for message in messages)
    assert any("[169d806f…] SKIPPED idempotency=all_targets" in message for message in messages)
    assert any("[169d806f…] DONE publish_count=0" in message for message in messages)
