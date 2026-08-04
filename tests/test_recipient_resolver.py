from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.constants.notification_constants import MessageDirection, NotificationType
from app.domain.models import DeliveryJob, JobKind
from app.eligibility.context import EffectiveConfig, StepOutcome, StepResult
from app.eligibility.conversation_eligibility import ConversationMatchContext
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


def _enabled_config(**kwargs) -> EffectiveConfig:
    defaults = {
        "is_enable": True,
        "is_all_tenants": True,
        "is_block": False,
        "is_in_flagged": True,
        "is_manual_sms": True,
        "is_sms_enable": True,
        "is_email_enable": True,
        "platform_os": "both",
    }
    defaults.update(kwargs)
    return EffectiveConfig(**defaults)


def _match_for_user(user_id, *, manual_reply: bool = False) -> ConversationMatchContext:
    return ConversationMatchContext(
        flagged_user_ids={user_id},
        manual_user_id=user_id if manual_reply else None,
    )


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
    session.query.return_value = device_query

    with patch(
        "app.pipeline.recipient_resolver.resolve_effective_config_for_job",
        return_value=(_enabled_config(), "global"),
    ), patch(
        "app.pipeline.recipient_resolver.load_conversation_match_context",
        return_value=_match_for_user(user_id),
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
        "app.pipeline.recipient_resolver.resolve_effective_config_for_job",
        return_value=(_enabled_config(), "global"),
    ), patch(
        "app.pipeline.recipient_resolver.load_conversation_match_context",
        return_value=_match_for_user(actor_id),
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
        "app.pipeline.recipient_resolver.resolve_effective_config_for_job",
        return_value=(_enabled_config(), "global"),
    ), patch(
        "app.pipeline.recipient_resolver.load_conversation_match_context",
        return_value=_match_for_user(actor_id),
    ):
        RecipientResolver.resolve(session, job)

    assert query.filter.call_count >= 2


def test_resolve_logs_ineligible_for_no_device_manual_reply_user():
    manual_user_id = uuid4()
    job = _job(direction=MessageDirection.INBOUND.value)

    session = MagicMock()
    trace = MagicMock()

    with patch(
        "app.pipeline.recipient_resolver.resolve_effective_config_for_job",
        return_value=(
            _enabled_config(is_in_flagged=True, is_manual_sms=False),
            "global",
        ),
    ), patch(
        "app.pipeline.recipient_resolver.load_conversation_match_context",
        return_value=ConversationMatchContext(
            flagged_user_ids=set(),
            manual_user_id=manual_user_id,
        ),
    ), patch(
        "app.eligibility.conversation_eligibility.resolve_lead_ids_for_notification",
        return_value={uuid4()},
    ):
        targets = RecipientResolver.resolve(session, job, trace=trace)

    assert targets == []
    trace.ineligible_user.assert_called_once()
    assert trace.ineligible_user.call_args.kwargs["user_id"] == manual_user_id
    assert trace.ineligible_user.call_args.kwargs["reason"] == "manual_reply_disabled_in_config"
    trace.no_devices_user.assert_not_called()


def test_resolve_skips_conversation_match_when_notifications_disabled():
    job = _job()
    session = MagicMock()
    trace = MagicMock()

    with patch(
        "app.pipeline.recipient_resolver.resolve_effective_config_for_job",
        return_value=(_enabled_config(is_enable=False), "global_disabled"),
    ), patch(
        "app.pipeline.recipient_resolver.load_conversation_match_context",
    ) as load_match:
        targets = RecipientResolver.resolve(session, job, trace=trace)

    assert targets == []
    load_match.assert_not_called()
    trace.skipped.assert_called_once()
    assert trace.skipped.call_args.kwargs["reason"] == "notifications_disabled"


def test_resolve_skips_conversation_match_when_all_paths_disabled():
    job = _job()
    session = MagicMock()
    trace = MagicMock()

    with patch(
        "app.pipeline.recipient_resolver.resolve_effective_config_for_job",
        return_value=(
            _enabled_config(is_in_flagged=False, is_manual_sms=False),
            "global",
        ),
    ), patch(
        "app.pipeline.recipient_resolver.load_conversation_match_context",
    ) as load_match:
        targets = RecipientResolver.resolve(session, job, trace=trace)

    assert targets == []
    load_match.assert_not_called()
    trace.skipped.assert_called_once()
    assert trace.skipped.call_args.kwargs["reason"] == "all_conversation_paths_disabled"


def test_resolve_config_before_recipients_trace_order():
    job = _job()
    session = MagicMock()
    trace = MagicMock()

    with patch(
        "app.pipeline.recipient_resolver.resolve_effective_config_for_job",
        return_value=(_enabled_config(is_enable=False), "global_disabled"),
    ):
        RecipientResolver.resolve(session, job, trace=trace)

    assert trace.resolve_config.called
    assert not trace.resolve_recipients.called
    assert trace.resolve_config.call_count == 1
