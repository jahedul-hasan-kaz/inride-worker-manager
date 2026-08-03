from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.domain.models import DeliveryJob, JobKind
from app.pipeline.runner import DeliveryRunner


def test_execute_job_closes_session_before_pubsub_publish():
    """Regression: Pub/Sub publish must not run while a session_scope is entered."""
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

    open_scopes = {"count": 0}
    publish_saw_open_scope = {"value": False}

    class FakeScope:
        def __enter__(self):
            open_scopes["count"] += 1
            return MagicMock()

        def __exit__(self, *args):
            open_scopes["count"] -= 1
            return False

    def fake_send_push_batch(items):
        publish_saw_open_scope["value"] = open_scopes["count"] > 0
        return SimpleNamespace(
            sent_count=len(items),
            failed_count=0,
            results_by_device={
                target.device_token_id: SimpleNamespace(
                    device_token_id=target.device_token_id,
                    status="sent",
                    deactivate=False,
                    error=None,
                )
            },
        )

    sender = MagicMock()
    sender.send_push_batch.side_effect = fake_send_push_batch
    runner = DeliveryRunner(sender=sender)

    with patch("app.pipeline.runner.session_scope", side_effect=lambda: FakeScope()), patch(
        "app.pipeline.runner.RecipientResolver.resolve",
        return_value=[target],
    ), patch("app.pipeline.runner.DeviceIdempotency.try_claim", return_value=True), patch(
        "app.pipeline.runner.DeviceIdempotency.mark_sent"
    ), patch(
        "app.pipeline.runner.DeviceIdempotency.seal_open_sending",
        return_value=0,
    ), patch(
        "app.pipeline.runner.DeviceIdempotency.has_any_sent",
        return_value=False,
    ), patch(
        "app.pipeline.runner.NotificationFinalizer.finalize"
    ), patch(
        "app.pipeline.runner.NotificationFinalizer.deactivate_tokens"
    ):
        runner.process_job(job)

    assert publish_saw_open_scope["value"] is False


def test_reclaim_does_not_delete_sending_sql():
    """Guard against regressing the duplicate-Expo reclaim bug."""
    import inspect
    from app.ingress import reclaim as reclaim_mod

    source = inspect.getsource(reclaim_mod.reclaim_stale)
    assert "DELETE FROM public.notification_push_deliveries" not in source
    assert "status = 'sent'" in source
    assert "sending" in source
