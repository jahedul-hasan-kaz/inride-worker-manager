from __future__ import annotations

from app.constants.notification_constants import NotificationType
from app.eligibility.context import EffectiveConfig
from app.pipeline.delivery_timing import (
    AGGREGATION_WINDOW_GRACE_SEC,
    aggregation_applies,
    aggregation_enabled,
    aggregation_window_sec,
    eligible_aggregation_types,
)


def test_aggregation_enabled_requires_positive_sec():
    assert not aggregation_enabled(EffectiveConfig(aggregation_type="sms", aggregation_sec=0))
    assert not aggregation_enabled(EffectiveConfig(aggregation_type="none", aggregation_sec=30))
    assert aggregation_enabled(EffectiveConfig(aggregation_type="sms", aggregation_sec=30))


def test_aggregation_window_sec_adds_grace_threshold():
    assert aggregation_window_sec(10) == 10 + AGGREGATION_WINDOW_GRACE_SEC
    assert aggregation_window_sec(0) == AGGREGATION_WINDOW_GRACE_SEC
    assert aggregation_window_sec(-1) == AGGREGATION_WINDOW_GRACE_SEC


def test_eligible_aggregation_types_matrix():
    assert eligible_aggregation_types(EffectiveConfig(aggregation_type="none")) == []
    assert eligible_aggregation_types(EffectiveConfig(aggregation_type="sms")) == [
        NotificationType.SMS.value
    ]
    assert eligible_aggregation_types(EffectiveConfig(aggregation_type="email")) == [
        NotificationType.EMAIL.value
    ]
    assert eligible_aggregation_types(EffectiveConfig(aggregation_type="both")) == [
        NotificationType.SMS.value,
        NotificationType.EMAIL.value,
    ]


def test_aggregation_applies_for_each_mode():
    sms_cfg = EffectiveConfig(aggregation_type="sms", aggregation_sec=30)
    email_cfg = EffectiveConfig(aggregation_type="email", aggregation_sec=30)
    both_cfg = EffectiveConfig(aggregation_type="both", aggregation_sec=30)

    assert aggregation_applies(sms_cfg, NotificationType.SMS.value)
    assert not aggregation_applies(sms_cfg, NotificationType.EMAIL.value)

    assert aggregation_applies(email_cfg, NotificationType.EMAIL.value)
    assert not aggregation_applies(email_cfg, NotificationType.SMS.value)

    assert aggregation_applies(both_cfg, NotificationType.SMS.value)
    assert aggregation_applies(both_cfg, NotificationType.EMAIL.value)
