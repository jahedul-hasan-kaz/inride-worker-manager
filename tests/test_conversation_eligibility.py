from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.constants.notification_constants import MessageDirection, NotificationType
from app.domain.models import DeliveryJob, JobKind
from app.eligibility.context import EffectiveConfig, EligibilityContext, StepOutcome
from app.eligibility.conversation_eligibility import (
    is_conversation_flagged_by_user,
    is_reply_to_user_manual_conversation,
    should_notify_user_for_conversation,
)
from app.eligibility.steps import step_conversation_eligibility


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


def _chain_query(*, first=None, all_rows=None):
    query = MagicMock()
    query.join.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.first.return_value = first
    query.all.return_value = all_rows or []
    return query


def test_is_conversation_flagged_by_user_true():
    session = MagicMock()
    session.execute.return_value.scalar.return_value = True

    assert is_conversation_flagged_by_user(
        session,
        tenant_id=uuid4(),
        thread_id="thread-1",
        user_id=uuid4(),
    )


def test_is_conversation_flagged_by_user_false_without_thread():
    session = MagicMock()
    assert not is_conversation_flagged_by_user(
        session,
        tenant_id=uuid4(),
        thread_id=None,
        user_id=uuid4(),
    )
    session.query.assert_not_called()


def test_is_reply_to_user_manual_conversation_sms_true():
    user_id = uuid4()
    tenant_id = uuid4()
    session = MagicMock()
    session.query.return_value = _chain_query(first=(str(user_id),))

    job = _job(tenant_id=tenant_id, direction=MessageDirection.INBOUND.value)
    assert is_reply_to_user_manual_conversation(session, job, user_id)


def test_is_reply_to_user_manual_conversation_sms_false_when_automated():
    user_id = uuid4()
    tenant_id = uuid4()
    session = MagicMock()
    session.query.return_value = _chain_query(first=("automated",))

    job = _job(tenant_id=tenant_id, direction=MessageDirection.INBOUND.value)
    assert not is_reply_to_user_manual_conversation(session, job, user_id)


def test_is_reply_to_user_manual_conversation_sms_false_when_different_user():
    user_id = uuid4()
    tenant_id = uuid4()
    session = MagicMock()
    session.query.return_value = _chain_query(first=(str(uuid4()),))

    job = _job(tenant_id=tenant_id, direction=MessageDirection.INBOUND.value)
    assert not is_reply_to_user_manual_conversation(session, job, user_id)


def test_is_reply_to_user_manual_conversation_requires_inbound():
    session = MagicMock()
    job = _job(direction=MessageDirection.OUTBOUND.value)
    assert not is_reply_to_user_manual_conversation(session, job, uuid4())
    session.query.assert_not_called()


def test_is_reply_to_user_manual_conversation_email_true(monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    email_log_id = uuid4()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility._resolve_email_lead_id",
        lambda session, job: uuid4(),
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility._last_outbound_sent_by_user_email",
        lambda session, tenant_id, lead_id, user_id: True,
    )

    session = MagicMock()
    job = _job(
        tenant_id=tenant_id,
        notification_type=NotificationType.EMAIL.value,
        direction=MessageDirection.INBOUND.value,
        email_log_id=email_log_id,
        thread_id=None,
    )
    assert is_reply_to_user_manual_conversation(session, job, user_id)


def test_should_notify_user_for_conversation_flagged_path(monkeypatch):
    session = MagicMock()
    job = _job()
    user_id = uuid4()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.is_conversation_flagged_by_user",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.is_reply_to_user_manual_conversation",
        lambda *args, **kwargs: False,
    )

    assert should_notify_user_for_conversation(session, job, user_id)


def test_should_notify_user_for_conversation_manual_reply_path(monkeypatch):
    session = MagicMock()
    job = _job()
    user_id = uuid4()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.is_conversation_flagged_by_user",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.is_reply_to_user_manual_conversation",
        lambda *args, **kwargs: True,
    )

    assert should_notify_user_for_conversation(session, job, user_id)


def test_step_conversation_eligibility_pass(monkeypatch):
    monkeypatch.setattr(
        "app.eligibility.steps.should_notify_user_for_conversation",
        lambda session, job, user_id: True,
    )
    ctx = EligibilityContext(
        job=_job(),
        user_id=uuid4(),
        user_role="agent",
        config=EffectiveConfig(),
        devices=[],
        session=MagicMock(),
    )
    result = step_conversation_eligibility(ctx)
    assert result.outcome == StepOutcome.CONTINUE


def test_step_conversation_eligibility_skip(monkeypatch):
    monkeypatch.setattr(
        "app.eligibility.steps.should_notify_user_for_conversation",
        lambda session, job, user_id: False,
    )
    ctx = EligibilityContext(
        job=_job(),
        user_id=uuid4(),
        user_role="agent",
        config=EffectiveConfig(),
        devices=[],
        session=MagicMock(),
    )
    result = step_conversation_eligibility(ctx)
    assert result.outcome == StepOutcome.SKIP_USER
    assert result.reason == "not_flagged_or_manual_reply"
