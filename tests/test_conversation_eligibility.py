from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.constants.notification_constants import MessageDirection, NotificationType
from app.domain.models import DeliveryJob, JobKind
from app.eligibility.context import EffectiveConfig, EligibilityContext, StepOutcome
from app.eligibility.conversation_eligibility import (
    evaluate_conversation_eligibility,
    get_conversation_candidate_reasons,
    get_conversation_candidate_user_ids,
    get_flagged_user_ids_for_notification,
    is_conversation_flagged_by_user,
    is_reply_to_user_manual_conversation,
    resolve_lead_id_for_notification,
    resolve_lead_ids_for_notification,
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
        "sms_log_id": 200,
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


def test_is_conversation_flagged_by_user_true(monkeypatch):
    user_id = uuid4()
    job = _job()
    session = MagicMock()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {user_id},
    )

    assert is_conversation_flagged_by_user(session, job, user_id)


def test_get_flagged_user_ids_for_notification_uses_lead_user_flags(monkeypatch):
    lead_id = uuid4()
    flagged_user = uuid4()
    job = _job()
    session = MagicMock()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.resolve_lead_ids_for_notification",
        lambda session, job: {lead_id},
    )
    session.query.return_value.filter.return_value.all.return_value = [(flagged_user,)]

    assert get_flagged_user_ids_for_notification(session, job) == {flagged_user}


def test_resolve_lead_ids_merges_sms_log_and_thread_matches():
    sms_lead_id = uuid4()
    thread_lead_id = uuid4()
    job = _job(sms_log_id=123, thread_id="thread-1")
    session = MagicMock()
    calls = {"count": 0}

    def fake_query(*args):
        calls["count"] += 1
        query = MagicMock()
        if calls["count"] == 1:
            query.filter.return_value.first.return_value = (sms_lead_id,)
        else:
            query.filter.return_value.all.return_value = [(thread_lead_id,)]
        return query

    session.query.side_effect = fake_query

    assert resolve_lead_ids_for_notification(session, job) == {
        sms_lead_id,
        thread_lead_id,
    }


def test_resolve_lead_id_prefers_sms_log_lead_id():
    lead_id = uuid4()
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = (lead_id,)

    job = _job(sms_log_id=123)
    assert resolve_lead_id_for_notification(session, job) == lead_id


def test_get_conversation_candidate_reasons_sms_merges_flagged_and_manual(monkeypatch):
    flagged_user = uuid4()
    manual_user = uuid4()
    job = _job(notification_type=NotificationType.SMS.value)

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {flagged_user},
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: manual_user,
    )

    reasons = get_conversation_candidate_reasons(MagicMock(), job)
    assert reasons[flagged_user] == ["flagged"]
    assert reasons[manual_user] == ["manual_reply"]


def test_get_conversation_candidate_reasons_user_can_have_both(monkeypatch):
    user_id = uuid4()
    job = _job(notification_type=NotificationType.SMS.value)

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {user_id},
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: user_id,
    )

    reasons = get_conversation_candidate_reasons(MagicMock(), job)
    assert reasons[user_id] == ["flagged", "manual_reply"]


def test_get_conversation_candidate_user_ids_sms_merges_flagged_and_manual(monkeypatch):
    flagged_user = uuid4()
    manual_user = uuid4()
    job = _job(notification_type=NotificationType.SMS.value)

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {flagged_user},
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: manual_user,
    )

    assert get_conversation_candidate_user_ids(MagicMock(), job) == {
        flagged_user,
        manual_user,
    }


def test_is_conversation_flagged_by_user_false_without_lead(monkeypatch):
    session = MagicMock()
    job = _job(thread_id=None)

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.resolve_lead_ids_for_notification",
        lambda session, job: set(),
    )

    assert not is_conversation_flagged_by_user(
        session,
        job,
        uuid4(),
    )
    session.query.assert_not_called()


def test_evaluate_conversation_eligibility_passes_for_flagged_user(monkeypatch):
    user_id = uuid4()
    job = _job()
    session = MagicMock()
    config = EffectiveConfig(is_in_flagged=True)

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {user_id},
    )

    passed, reason, include_reasons = evaluate_conversation_eligibility(
        session, job, user_id, config
    )
    assert passed
    assert reason is None
    assert include_reasons == ["flagged"]


def test_evaluate_conversation_eligibility_skips_flagged_when_disabled_in_config(
    monkeypatch,
):
    user_id = uuid4()
    job = _job()
    session = MagicMock()
    config = EffectiveConfig(is_in_flagged=False, is_manual_sms=True)

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {user_id},
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: None,
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.resolve_lead_ids_for_notification",
        lambda session, job: {uuid4()},
    )

    passed, reason, include_reasons = evaluate_conversation_eligibility(
        session, job, user_id, config
    )
    assert not passed
    assert reason == "flagged_disabled_in_config"
    assert include_reasons == []


def test_evaluate_conversation_eligibility_reports_lead_not_found(monkeypatch):
    job = _job()
    session = MagicMock()
    config = EffectiveConfig()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: set(),
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: None,
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.resolve_lead_ids_for_notification",
        lambda session, job: set(),
    )

    passed, reason, include_reasons = evaluate_conversation_eligibility(
        session, job, uuid4(), config
    )
    assert not passed
    assert reason.startswith("lead_not_found:")
    assert include_reasons == []


def test_is_reply_to_user_manual_conversation_sms_true(monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    session = MagicMock()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility._last_outbound_sender_user_id_sms",
        lambda session, tenant_id, thread_id: user_id,
    )

    job = _job(tenant_id=tenant_id, direction=MessageDirection.INBOUND.value)
    assert is_reply_to_user_manual_conversation(session, job, user_id)


def test_is_reply_to_user_manual_conversation_sms_false_when_automated(monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    session = MagicMock()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility._last_outbound_sender_user_id_sms",
        lambda session, tenant_id, thread_id: None,
    )

    job = _job(tenant_id=tenant_id, direction=MessageDirection.INBOUND.value)
    assert not is_reply_to_user_manual_conversation(session, job, user_id)


def test_is_reply_to_user_manual_conversation_sms_false_when_different_user(monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    session = MagicMock()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility._last_outbound_sender_user_id_sms",
        lambda session, tenant_id, thread_id: uuid4(),
    )

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
    root_id = uuid4()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility._previous_outbound_sender_user_id_email",
        lambda session, tenant_id, root_id, current_email_id: user_id,
    )

    session = MagicMock()
    job = _job(
        tenant_id=tenant_id,
        notification_type=NotificationType.EMAIL.value,
        direction=MessageDirection.INBOUND.value,
        email_log_id=email_log_id,
        thread_id=str(root_id),
    )
    assert is_reply_to_user_manual_conversation(session, job, user_id)


def test_is_reply_to_user_manual_conversation_email_false_when_no_sender_user_id(
    monkeypatch,
):
    tenant_id = uuid4()
    email_log_id = uuid4()
    root_id = uuid4()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility._previous_outbound_sender_user_id_email",
        lambda session, tenant_id, root_id, current_email_id: None,
    )

    session = MagicMock()
    job = _job(
        tenant_id=tenant_id,
        notification_type=NotificationType.EMAIL.value,
        direction=MessageDirection.INBOUND.value,
        email_log_id=email_log_id,
        thread_id=str(root_id),
    )
    assert not is_reply_to_user_manual_conversation(session, job, uuid4())


def test_should_notify_user_for_conversation_email_skips_flagged_path(monkeypatch):
    session = MagicMock()
    user_id = uuid4()
    job = _job(
        notification_type=NotificationType.EMAIL.value,
        direction=MessageDirection.INBOUND.value,
        email_log_id=uuid4(),
        thread_id=str(uuid4()),
    )

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {user_id},
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: None,
    )

    assert not should_notify_user_for_conversation(
        session, job, user_id, EffectiveConfig(is_manual_email=True)
    )


def test_should_notify_user_for_conversation_flagged_path(monkeypatch):
    session = MagicMock()
    job = _job()
    user_id = uuid4()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: {user_id},
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: None,
    )

    assert should_notify_user_for_conversation(
        session, job, user_id, EffectiveConfig(is_in_flagged=True)
    )


def test_should_notify_user_for_conversation_manual_reply_path(monkeypatch):
    session = MagicMock()
    job = _job()
    user_id = uuid4()

    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_flagged_user_ids_for_notification",
        lambda session, job: set(),
    )
    monkeypatch.setattr(
        "app.eligibility.conversation_eligibility.get_manual_reply_user_id",
        lambda session, job: user_id,
    )

    assert should_notify_user_for_conversation(
        session, job, user_id, EffectiveConfig(is_manual_sms=True)
    )


def test_step_conversation_eligibility_pass(monkeypatch):
    monkeypatch.setattr(
        "app.eligibility.steps.evaluate_conversation_eligibility",
        lambda session, job, user_id, config: (True, None, ["manual_reply"]),
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
    assert ctx.include_reasons == ["manual_reply"]


def test_step_conversation_eligibility_skip(monkeypatch):
    monkeypatch.setattr(
        "app.eligibility.steps.evaluate_conversation_eligibility",
        lambda session, job, user_id, config: (
            False,
            "lead_not_found:thread_id='thread-1',sms_log_id=None",
            [],
        ),
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
    assert result.reason.startswith("lead_not_found:")
