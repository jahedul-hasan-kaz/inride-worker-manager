from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from app.domain.models import DeliveryJob, JobKind
from app.eligibility.notification_config_resolver import resolve_effective_config_for_job


def _global_row(**kwargs):
    defaults = {
        "id": uuid4(),
        "user_id": uuid4(),
        "is_enable": True,
        "is_all_tenants": True,
        "is_in_flagged": False,
        "is_manual_sms": True,
        "is_manual_email": True,
        "is_sms_enable": True,
        "is_email_enable": True,
        "platform_os": "both",
        "created_at": datetime(2026, 8, 1),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _tenant_row(**kwargs):
    defaults = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "is_in_flagged": True,
        "is_manual_sms": False,
        "is_manual_email": False,
        "is_sms_enable": True,
        "is_email_enable": False,
        "platform_os": "both",
        "priority": "high",
        "is_block": False,
        "created_at": datetime(2026, 8, 2),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_resolve_effective_config_uses_latest_global_row(monkeypatch):
    tenant_id = uuid4()
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=tenant_id,
    )
    latest = _global_row(is_in_flagged=True, created_at=datetime(2026, 8, 3))

    monkeypatch.setattr(
        "app.eligibility.notification_config_resolver.load_latest_global_notification_config",
        lambda session: latest,
    )

    config, source = resolve_effective_config_for_job(None, job)
    assert source == "global"
    assert config.is_in_flagged is True


def test_resolve_effective_config_uses_latest_tenant_row_when_not_all_tenants(monkeypatch):
    tenant_id = uuid4()
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=tenant_id,
    )
    global_row = _global_row(is_all_tenants=False, is_in_flagged=False)
    tenant_row = _tenant_row(tenant_id=tenant_id, is_in_flagged=True, is_manual_sms=True)

    monkeypatch.setattr(
        "app.eligibility.notification_config_resolver.load_latest_global_notification_config",
        lambda session: global_row,
    )
    monkeypatch.setattr(
        "app.eligibility.notification_config_resolver.load_latest_tenant_notification_config",
        lambda session, tid: tenant_row if tid == tenant_id else None,
    )

    config, source = resolve_effective_config_for_job(None, job)
    assert source == "tenant"
    assert config.is_in_flagged is True
    assert config.is_manual_sms is True
    assert config.is_all_tenants is False


def test_resolve_effective_config_global_disabled(monkeypatch):
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=uuid4(),
    )
    monkeypatch.setattr(
        "app.eligibility.notification_config_resolver.load_latest_global_notification_config",
        lambda session: _global_row(is_enable=False),
    )

    config, source = resolve_effective_config_for_job(None, job)
    assert source == "global_disabled"
    assert config.is_enable is False


def test_resolve_effective_config_tenant_blocked(monkeypatch):
    tenant_id = uuid4()
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=tenant_id,
    )
    monkeypatch.setattr(
        "app.eligibility.notification_config_resolver.load_latest_global_notification_config",
        lambda session: _global_row(is_all_tenants=False),
    )
    monkeypatch.setattr(
        "app.eligibility.notification_config_resolver.load_latest_tenant_notification_config",
        lambda session, tid: _tenant_row(tenant_id=tenant_id, is_block=True),
    )

    config, source = resolve_effective_config_for_job(None, job)
    assert source == "tenant_blocked"
    assert config.is_block is True
