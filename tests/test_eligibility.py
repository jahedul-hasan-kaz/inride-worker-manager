from __future__ import annotations

from uuid import uuid4

from app.constants.notification_constants import MessageDirection, NotificationType, PlatformOs
from app.domain.models import DeliveryJob, JobKind
from app.eligibility.context import EffectiveConfig, EligibilityContext, StepOutcome
from app.eligibility.steps import (
    step_channel_enable,
    step_master_enable,
    step_platform_filter,
)


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


def test_platform_filter_continues_when_no_devices():
    ctx = _ctx(devices=[])
    result = step_platform_filter(ctx)
    assert result.outcome == StepOutcome.CONTINUE
    assert ctx.eligible_devices == []


def test_evaluate_user_job_gated_runs_platform_filter_only():
    from app.eligibility.pipeline import evaluate_user

    device = type("Device", (), {"id": uuid4(), "token": "tok", "platform": "ios"})()
    ctx = _ctx(
        config=EffectiveConfig(platform_os=PlatformOs.IOS.value),
        devices=[device],
        job_gated=True,
        include_reasons=["flagged"],
    )
    result = evaluate_user(ctx)
    assert result.outcome == StepOutcome.CONTINUE
    assert ctx.eligible_devices == [device]
