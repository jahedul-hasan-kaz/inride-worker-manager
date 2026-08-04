"""Shared notification string constants and enums."""
from enum import Enum
from typing import Optional


class NotificationType(str, Enum):
    SMS = "sms"
    EMAIL = "email"


class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class PlatformOs(str, Enum):
    ANDROID = "android"
    IOS = "ios"
    BOTH = "both"


class TenantPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AggregationType(str, Enum):
    NONE = "none"
    SMS = "sms"
    EMAIL = "email"
    BOTH = "both"


AGGREGATION_TYPES = {item.value for item in AggregationType}


PRIORITY_WEIGHT = {
    TenantPriority.HIGH.value: 3,
    TenantPriority.MEDIUM.value: 2,
    TenantPriority.LOW.value: 1,
}


DEFAULT_TENANT_CONFIG = {
    "is_in_flagged": False,
    "is_manual_sms": False,
    "is_manual_email": False,
    "is_sms_enable": False,
    "is_email_enable": False,
    "platform_os": PlatformOs.BOTH.value,
    "priority": TenantPriority.HIGH.value,
    "is_block": False,
}

DEFAULT_GLOBAL_DELIVERY_CONFIG = {
    "ttl_sec": 60,
    "aggregation_type": AggregationType.NONE.value,
    "aggregation_sec": None,
}


def priority_to_weight(priority: Optional[str]) -> int:
    if not priority:
        return PRIORITY_WEIGHT[TenantPriority.HIGH.value]
    return PRIORITY_WEIGHT.get(priority.strip().lower(), PRIORITY_WEIGHT[TenantPriority.HIGH.value])


class PushStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    AGGREGATED_PROCESSING = "aggregated_processing"
    SENT = "sent"
    FAILED = "failed"
    WINDOW_SKIPPED = "window_skipped"


_DIRECTION_ALIASES = {
    "received": MessageDirection.INBOUND.value,
    "sent": MessageDirection.OUTBOUND.value,
    "inbound": MessageDirection.INBOUND.value,
    "outbound": MessageDirection.OUTBOUND.value,
}


def normalize_message_direction(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    return _DIRECTION_ALIASES.get(normalized)
