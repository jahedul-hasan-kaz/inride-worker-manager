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


class PushStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


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
