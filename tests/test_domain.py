from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.domain.handle_result import HandleDisposition
from app.domain.models import DeliveryJob, JobKind, PushStatus
from app.domain.policies import ImmediateSingleNotificationPolicy
from app.monitoring.metrics import PushMetrics
from app.pipeline.runner import DeliveryRunner


def test_immediate_policy_always_delivers():
    policy = ImmediateSingleNotificationPolicy()
    job = DeliveryJob(job_kind=JobKind.IMMEDIATE_SINGLE, notification_id=uuid4())
    assert policy.should_deliver(job) is True
    assert policy.skip_status(job) is None


def test_ledger_key_defaults_to_notification_id():
    nid = uuid4()
    job = DeliveryJob(job_kind=JobKind.IMMEDIATE_SINGLE, notification_id=nid)
    assert job.ledger_notification_id == nid


def test_handle_disposition_ack_flags():
    assert HandleDisposition.ACK_PROCESSED.should_ack is True
    assert HandleDisposition.ACK_ALREADY_DONE.should_ack is True
    assert HandleDisposition.ACK_IN_FLIGHT.should_ack is True
    assert HandleDisposition.ACK_EXHAUSTED.should_ack is True
    assert HandleDisposition.NACK_RETRY.should_ack is False
    assert HandleDisposition.NACK_ERROR.should_ack is False


def test_metrics_snapshot_does_not_reset():
    metrics = PushMetrics()
    metrics.notification_finalized("sent")
    metrics.device_attempted("failed")
    snap1 = metrics.snapshot()
    snap2 = metrics.snapshot()
    assert snap1 == snap2
    assert any("sent" in k for k in snap1)
    reset = metrics.snapshot_and_reset()
    assert reset
    assert metrics.snapshot() == {}


def test_handle_notification_already_done():
    nid = uuid4()
    runner = DeliveryRunner(sender=MagicMock())

    with patch("app.pipeline.runner.session_scope") as scope:
        session = MagicMock()
        scope.return_value.__enter__.return_value = session
        scope.return_value.__exit__.return_value = False
        with patch(
            "app.pipeline.runner.NotificationClaimer.claim_by_id",
            return_value=(None, PushStatus.SENT.value),
        ):
            result = runner.handle_notification_id(nid)

    assert result.disposition == HandleDisposition.ACK_ALREADY_DONE


def test_handle_notification_in_flight():
    nid = uuid4()
    runner = DeliveryRunner(sender=MagicMock())

    with patch("app.pipeline.runner.session_scope") as scope:
        session = MagicMock()
        scope.return_value.__enter__.return_value = session
        scope.return_value.__exit__.return_value = False
        with patch(
            "app.pipeline.runner.NotificationClaimer.claim_by_id",
            return_value=(None, PushStatus.PROCESSING.value),
        ):
            result = runner.handle_notification_id(nid)

    assert result.disposition == HandleDisposition.ACK_IN_FLIGHT


def test_handle_notification_missing_nacks():
    nid = uuid4()
    runner = DeliveryRunner(sender=MagicMock())

    with patch("app.pipeline.runner.session_scope") as scope:
        session = MagicMock()
        scope.return_value.__enter__.return_value = session
        scope.return_value.__exit__.return_value = False
        with patch(
            "app.pipeline.runner.NotificationClaimer.claim_by_id",
            return_value=(None, None),
        ):
            result = runner.handle_notification_id(nid)

    assert result.disposition == HandleDisposition.NACK_RETRY


def test_execute_job_partial_success_marks_sent():
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=uuid4(),
    )
    target_ok = SimpleNamespace(
        device_token_id=uuid4(),
        push_token="ExponentPushToken[ok]",
        user_id=uuid4(),
        notification_priority=1,
    )
    target_bad = SimpleNamespace(
        device_token_id=uuid4(),
        push_token="ExponentPushToken[bad]",
        user_id=uuid4(),
        notification_priority=0,
    )

    sender = MagicMock()
    sender.send_push.return_value = (True, None, [])
    runner = DeliveryRunner(sender=sender)

    with patch("app.pipeline.runner.session_scope") as scope, patch(
        "app.pipeline.runner.RecipientResolver.resolve",
        return_value=[target_ok, target_bad],
    ), patch("app.pipeline.runner.DeviceIdempotency.try_claim", return_value=True), patch(
        "app.pipeline.runner.DeviceIdempotency.mark_sent"
    ) as mark_sent, patch(
        "app.pipeline.runner.DeviceIdempotency.seal_open_sending",
        return_value=0,
    ), patch(
        "app.pipeline.runner.DeviceIdempotency.has_any_sent",
        return_value=False,
    ), patch(
        "app.pipeline.runner.NotificationFinalizer.finalize"
    ) as finalize, patch(
        "app.pipeline.runner.NotificationFinalizer.deactivate_tokens"
    ):
        session = MagicMock()
        scope.return_value.__enter__.return_value = session
        scope.return_value.__exit__.return_value = False
        result = runner.process_job(job)

    assert result.notification_status == PushStatus.SENT
    assert mark_sent.call_count == 2
    sender.send_push.assert_called_once()
    finalize.assert_called()
    assert finalize.call_args.kwargs["status"] == PushStatus.SENT


def test_execute_job_all_failed_marks_failed():
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=uuid4(),
    )
    target = SimpleNamespace(
        device_token_id=uuid4(),
        push_token="ExponentPushToken[x]",
        user_id=uuid4(),
        notification_priority=0,
    )
    sender = MagicMock()
    sender.send_push.return_value = (False, "fail", [])
    runner = DeliveryRunner(sender=sender)

    with patch("app.pipeline.runner.session_scope") as scope, patch(
        "app.pipeline.runner.RecipientResolver.resolve",
        return_value=[target],
    ), patch("app.pipeline.runner.DeviceIdempotency.try_claim", return_value=True), patch(
        "app.pipeline.runner.DeviceIdempotency.release_claim"
    ) as release, patch(
        "app.pipeline.runner.DeviceIdempotency.seal_open_sending",
        return_value=0,
    ), patch(
        "app.pipeline.runner.DeviceIdempotency.has_any_sent",
        return_value=False,
    ), patch(
        "app.pipeline.runner.NotificationFinalizer.finalize"
    ) as finalize, patch(
        "app.pipeline.runner.NotificationFinalizer.deactivate_tokens"
    ):
        session = MagicMock()
        scope.return_value.__enter__.return_value = session
        scope.return_value.__exit__.return_value = False
        result = runner.process_job(job)

    assert result.notification_status == PushStatus.FAILED
    assert release.call_count == 1
    assert finalize.call_args.kwargs["status"] == PushStatus.FAILED
