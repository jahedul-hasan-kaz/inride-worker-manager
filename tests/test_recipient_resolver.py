from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.constants.notification_constants import MessageDirection, NotificationType
from app.domain.models import DeliveryJob, JobKind
from app.eligibility.context import StepOutcome, StepResult
from app.pipeline.recipient_resolver import RecipientResolver


def _job(**kwargs) -> DeliveryJob:
    defaults = {
        "job_kind": JobKind.IMMEDIATE_SINGLE,
        "notification_id": uuid4(),
        "tenant_id": uuid4(),
        "notification_type": NotificationType.SMS.value,
        "direction": MessageDirection.INBOUND.value,
        "thread_id": "thread-1",
    }
    defaults.update(kwargs)
    return DeliveryJob(**defaults)


def test_resolve_returns_targets_for_candidate_devices():
    user_id = uuid4()
    job = _job(direction=MessageDirection.INBOUND.value)

    device = MagicMock()
    device.token = "ExponentPushToken[abc]"
    device.id = uuid4()
    device.platform = "ios"

    session = MagicMock()
    device_query = MagicMock()
    device_query.join.return_value = device_query
    device_query.filter.return_value = device_query
    device_query.order_by.return_value = device_query
    device_query.all.return_value = [(device, user_id, "agent")]

    config_query = MagicMock()
    config_query.filter.return_value.all.return_value = []

    session.query.side_effect = lambda *args, **kwargs: (
        config_query if args and getattr(args[0], "__name__", "") == "NotificationConfigInDB" else device_query
    )

    with patch(
        "app.pipeline.recipient_resolver.get_conversation_candidate_reasons",
        return_value={user_id: ["flagged"]},
    ), patch(
        "app.pipeline.recipient_resolver.evaluate_user",
        side_effect=lambda ctx: (
            setattr(ctx, "eligible_devices", list(ctx.devices)),
            StepResult(outcome=StepOutcome.CONTINUE),
        )[1],
    ):
        targets = RecipientResolver.resolve(session, job)

    assert len(targets) == 1
    assert targets[0].user_id == user_id
    assert targets[0].push_token == "ExponentPushToken[abc]"


def test_resolve_does_not_exclude_actor_on_inbound():
    actor_id = uuid4()
    job = _job(
        direction=MessageDirection.INBOUND.value,
        actor_user_id=actor_id,
    )

    captured_filters = []
    query = MagicMock()
    query.join.return_value = query
    query.filter.side_effect = lambda *args, **kwargs: captured_filters.append(args) or query
    query.order_by.return_value = query
    query.all.return_value = []

    session = MagicMock()
    session.query.return_value = query

    with patch(
        "app.pipeline.recipient_resolver.get_conversation_candidate_reasons",
        return_value={actor_id: ["flagged"]},
    ):
        RecipientResolver.resolve(session, job)

    assert not any("!=" in str(args) for args in captured_filters)


def test_resolve_excludes_actor_on_outbound():
    actor_id = uuid4()
    job = _job(
        direction=MessageDirection.OUTBOUND.value,
        actor_user_id=actor_id,
    )

    query = MagicMock()
    query.join.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = []

    session = MagicMock()
    session.query.return_value = query

    with patch(
        "app.pipeline.recipient_resolver.get_conversation_candidate_reasons",
        return_value={actor_id: ["flagged"]},
    ):
        RecipientResolver.resolve(session, job)

    assert query.filter.call_count >= 2
