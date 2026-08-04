from __future__ import annotations

from typing import List

from app.constants.notification_constants import AggregationType, NotificationType
from app.eligibility.context import EffectiveConfig

# Extra seconds on claim/skip cutoffs so poll jitter does not drop rows that
# landed exactly at aggregation_sec (e.g. a message sent ~10s ago).
AGGREGATION_WINDOW_GRACE_SEC = 2


def aggregation_enabled(config: EffectiveConfig) -> bool:
    agg_type = (config.aggregation_type or AggregationType.NONE.value).lower()
    if agg_type == AggregationType.NONE.value:
        return False
    return config.aggregation_sec is not None and int(config.aggregation_sec) > 0


def aggregation_window_sec(agg_sec: int) -> int:
    """Lookback used for digest claim and window_skipped (agg_sec + grace)."""
    return max(0, int(agg_sec)) + AGGREGATION_WINDOW_GRACE_SEC


def eligible_aggregation_types(config: EffectiveConfig) -> List[str]:
    agg_type = (config.aggregation_type or AggregationType.NONE.value).lower()
    if agg_type == AggregationType.SMS.value:
        return [NotificationType.SMS.value]
    if agg_type == AggregationType.EMAIL.value:
        return [NotificationType.EMAIL.value]
    if agg_type == AggregationType.BOTH.value:
        return [NotificationType.SMS.value, NotificationType.EMAIL.value]
    return []


def aggregation_applies(config: EffectiveConfig, notification_type: str) -> bool:
    if not aggregation_enabled(config):
        return False
    normalized = (notification_type or "").lower()
    return normalized in eligible_aggregation_types(config)
