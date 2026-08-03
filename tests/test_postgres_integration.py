"""
Postgres integration tests for claim + per-device idempotency.

Run:
  set INTEGRATION_TEST=1
  set PG_DB_URL=postgresql://...
  pytest tests/test_postgres_integration.py -q

Requires schema from:
  ai-agent-management/scripts/2026_07_29_expo_push_outbox_and_status.sql
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.core.config import config  # noqa: E402
from app.core.env import get_env  # noqa: E402

# Skip entire module unless explicitly enabled with a real DB URL.
if get_env("INTEGRATION_TEST") != "1" or not config.PG_DB_URL:
    pytest.skip(
        "Set INTEGRATION_TEST=1 and PG_DB_URL to run Postgres integration tests",
        allow_module_level=True,
    )
from app.db.postgres import PostgresClient  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.domain.models import DeliveryJob, DeliveryTarget, JobKind  # noqa: E402
from app.pipeline.claimer import NotificationClaimer  # noqa: E402
from app.pipeline.device_idempotency import DeviceIdempotency  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    assert config.PG_DB_URL, "PG_DB_URL required"
    PostgresClient.initialize()
    yield
    PostgresClient.close()


def _insert_notification(session, *, notification_id, tenant_id, push_status="pending"):
    session.execute(
        text(
            """
            INSERT INTO public.notifications (
                id, type, tenant_id, push_status, is_read, created_at, updated_at
            ) VALUES (
                :id, 'sms', :tenant_id, :push_status, false, NOW(), NOW()
            )
            """
        ),
        {
            "id": str(notification_id),
            "tenant_id": str(tenant_id),
            "push_status": push_status,
        },
    )
    session.commit()


def _cleanup(session, notification_id, device_token_id=None, user_id=None):
    session.execute(
        text("DELETE FROM public.notification_push_deliveries WHERE notification_id = :id"),
        {"id": str(notification_id)},
    )
    session.execute(
        text("DELETE FROM public.notifications WHERE id = :id"),
        {"id": str(notification_id)},
    )
    if device_token_id is not None:
        session.execute(
            text("DELETE FROM public.device_tokens WHERE id = :id"),
            {"id": str(device_token_id)},
        )
    if user_id is not None:
        session.execute(
            text("DELETE FROM public.users WHERE id = :id AND email LIKE 'itest-%'"),
            {"id": str(user_id)},
        )
    session.commit()


def test_claim_by_id_second_caller_sees_processing():
    notification_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    with session_scope() as session:
        _cleanup(session, notification_id)
        _insert_notification(session, notification_id=notification_id, tenant_id=tenant_id)

    try:
        with session_scope() as session:
            job, miss = NotificationClaimer.claim_by_id(session, notification_id)
        assert job is not None
        assert miss is None
        assert job.notification_id == notification_id

        with session_scope() as session:
            job2, miss2 = NotificationClaimer.claim_by_id(session, notification_id)
        assert job2 is None
        assert miss2 == "processing"
    finally:
        with session_scope() as session:
            _cleanup(session, notification_id)


def test_device_idempotency_second_claim_is_rejected():
    notification_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    device_token_id = uuid.uuid4()
    email = f"itest-{notification_id.hex[:12]}@example.com"

    with session_scope() as session:
        _cleanup(session, notification_id, device_token_id=device_token_id, user_id=user_id)
        _insert_notification(session, notification_id=notification_id, tenant_id=tenant_id)
        session.execute(
            text(
                """
                INSERT INTO public.users (
                    id, email, first_name, last_name, role, tenant_id,
                    is_disabled, is_notify_mobile,
                    created_at, updated_at
                ) VALUES (
                    :id, :email, 'I', 'Test', 'user', :tenant_id,
                    false, true, NOW(), NOW()
                )
                """
            ),
            {"id": str(user_id), "email": email, "tenant_id": str(tenant_id)},
        )
        session.execute(
            text(
                """
                INSERT INTO public.device_tokens (
                    id, user_id, token, platform, is_active, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :token, 'ios', true, NOW(), NOW()
                )
                """
            ),
            {
                "id": str(device_token_id),
                "user_id": str(user_id),
                "token": f"ExponentPushToken[itest-{device_token_id.hex[:8]}]",
            },
        )
        session.commit()

    job = DeliveryJob(job_kind=JobKind.IMMEDIATE_SINGLE, notification_id=notification_id)
    target = DeliveryTarget(
        device_token_id=device_token_id,
        push_token="ExponentPushToken[itest]",
        user_id=user_id,
        notification_priority=0,
    )

    try:
        with session_scope() as session:
            assert DeviceIdempotency.try_claim(session, job, target) is True

        with session_scope() as session:
            assert DeviceIdempotency.try_claim(session, job, target) is False

        with session_scope() as session:
            DeviceIdempotency.mark_sent(session, job, device_token_id)
            assert DeviceIdempotency.has_any_sent(session, job) is True

        with session_scope() as session:
            assert DeviceIdempotency.try_claim(session, job, target) is False
    finally:
        with session_scope() as session:
            _cleanup(session, notification_id, device_token_id=device_token_id, user_id=user_id)
