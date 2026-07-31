from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.constants.notification_constants import PlatformOs
from app.db.models import DeviceTokenInDB, NotificationConfigInDB
from app.domain.models import DeliveryJob


class StepOutcome(str, Enum):
    CONTINUE = "continue"
    SKIP_USER = "skip_user"


@dataclass
class StepResult:
    outcome: StepOutcome
    reason: Optional[str] = None


@dataclass
class EffectiveConfig:
    is_enable: bool = True
    is_all_tenants: bool = True
    is_in_flagged: bool = False
    is_manual_sms: bool = True
    is_manual_email: bool = True
    is_sms_enable: bool = True
    is_email_enable: bool = True
    platform_os: str = PlatformOs.BOTH.value


@dataclass
class EligibilityContext:
    job: DeliveryJob
    user_id: UUID
    user_role: str
    config: EffectiveConfig
    devices: List[DeviceTokenInDB] = field(default_factory=list)
    eligible_devices: List[DeviceTokenInDB] = field(default_factory=list)
    skip_reason: Optional[str] = None
    session: Any = None

    @property
    def notification_type(self) -> str:
        return (self.job.notification_type or "").lower()

    @property
    def direction(self) -> Optional[str]:
        return (self.job.direction or "").lower() if self.job.direction else None


def default_effective_config() -> EffectiveConfig:
    return EffectiveConfig()


def config_from_row(row: Optional[NotificationConfigInDB]) -> EffectiveConfig:
    if row is None:
        return default_effective_config()
    return EffectiveConfig(
        is_enable=bool(row.is_enable),
        is_all_tenants=bool(row.is_all_tenants),
        is_in_flagged=bool(row.is_in_flagged),
        is_manual_sms=bool(row.is_manual_sms),
        is_manual_email=bool(row.is_manual_email),
        is_sms_enable=bool(row.is_sms_enable),
        is_email_enable=bool(row.is_email_enable),
        platform_os=(row.platform_os or PlatformOs.BOTH.value).lower(),
    )
