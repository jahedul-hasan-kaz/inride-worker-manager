from __future__ import annotations

from uuid import uuid4

from app.constants.notification_constants import MessageDirection, NotificationType, PlatformOs
from app.domain.models import DeliveryJob, JobKind
from app.eligibility.context import EffectiveConfig, EligibilityContext, StepOutcome
from app.eligibility.steps import (
    step_channel_enable,
    step_master_enable,
    step_platform_filter,
    step_tenant_scope,
)
from app.db.models import NotificationTenantConfigInDB


def _ctx(**kwargs) -> EligibilityContext:
    defaults = {
        "job": DeliveryJob(
            job_kind=JobKind.IMMEDIATE_SINGLE,
            notification_id=uuid4(),
            tenant_id=uuid4(),
            notification_type=NotificationType.SMS.value,
            direction=MessageDirection.INBOUND.value,
        ),
        "user_id": uuid4(),
        "user_role": "agent",
        "config": EffectiveConfig(),
        "devices": [],
        "session": None,
    }
    defaults.update(kwargs)
    return EligibilityContext(**defaults)


def test_master_enable_skips_when_disabled():
    result = step_master_enable(_ctx(config=EffectiveConfig(is_enable=False)))
    assert result.outcome == StepOutcome.SKIP_USER


def test_channel_enable_skips_when_sms_disabled():
    result = step_channel_enable(_ctx(config=EffectiveConfig(is_sms_enable=False)))
    assert result.outcome == StepOutcome.SKIP_USER


def test_platform_filter_keeps_matching_devices():
    device = type("Device", (), {"id": uuid4(), "token": "tok", "platform": "android"})()
    ctx = _ctx(
        config=EffectiveConfig(platform_os=PlatformOs.ANDROID.value),
        devices=[device],
    )
    result = step_platform_filter(ctx)
    assert result.outcome == StepOutcome.CONTINUE
    assert ctx.eligible_devices == [device]


def test_tenant_scope_blocks_when_is_block():
    tenant_row = NotificationTenantConfigInDB(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        is_block=True,
    )
    ctx = _ctx(
        config=EffectiveConfig(is_all_tenants=False),
        tenant_config_row=tenant_row,
    )
    result = step_tenant_scope(ctx)
    assert result.outcome == StepOutcome.SKIP_USER
    assert result.reason == "tenant_blocked"


def test_tenant_scope_applies_tenant_channel_flags():
    tenant_id = uuid4()
    user_id = uuid4()
    tenant_row = NotificationTenantConfigInDB(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        is_sms_enable=True,
        is_email_enable=False,
        is_manual_sms=True,
        is_in_flagged=True,
        platform_os=PlatformOs.IOS.value,
        priority="medium",
        is_block=False,
    )
    ctx = _ctx(
        config=EffectiveConfig(is_all_tenants=False, is_sms_enable=False),
        tenant_config_row=tenant_row,
        user_id=user_id,
        job=DeliveryJob(
            job_kind=JobKind.IMMEDIATE_SINGLE,
            notification_id=uuid4(),
            tenant_id=tenant_id,
            notification_type=NotificationType.SMS.value,
            direction=MessageDirection.INBOUND.value,
        ),
    )
    result = step_tenant_scope(ctx)
    assert result.outcome == StepOutcome.CONTINUE
    assert ctx.config.is_sms_enable is True
    assert ctx.config.is_email_enable is False
    assert ctx.config.is_in_flagged is True
    assert ctx.config.platform_os == PlatformOs.IOS.value
    assert ctx.config.priority == "medium"
