from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from app.constants.notification_constants import DEFAULT_TENANT_CONFIG, PlatformOs, priority_to_weight
from app.db.models import DeviceTokenInDB, NotificationConfigInDB, NotificationTenantConfigInDB
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
    priority: str = DEFAULT_TENANT_CONFIG["priority"]
    is_block: bool = False


@dataclass
class EligibilityContext:
    job: DeliveryJob
    user_id: UUID
    user_role: str
    config: EffectiveConfig
    devices: List[DeviceTokenInDB] = field(default_factory=list)
    eligible_devices: List[DeviceTokenInDB] = field(default_factory=list)
    tenant_config_row: Optional[NotificationTenantConfigInDB] = None
    skip_reason: Optional[str] = None
    include_reasons: List[str] = field(default_factory=list)
    session: Any = None

    @property
    def notification_type(self) -> str:
        return (self.job.notification_type or "").lower()

    @property
    def direction(self) -> Optional[str]:
        return (self.job.direction or "").lower() if self.job.direction else None

    @property
    def delivery_priority(self) -> int:
        if self.config.is_all_tenants:
            return 0
        return priority_to_weight(self.config.priority)


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


def tenant_config_from_row(row: Optional[NotificationTenantConfigInDB]) -> EffectiveConfig:
    defaults = DEFAULT_TENANT_CONFIG
    if row is None:
        return EffectiveConfig(
            is_enable=True,
            is_all_tenants=False,
            is_in_flagged=defaults["is_in_flagged"],
            is_manual_sms=defaults["is_manual_sms"],
            is_manual_email=defaults["is_manual_email"],
            is_sms_enable=defaults["is_sms_enable"],
            is_email_enable=defaults["is_email_enable"],
            platform_os=defaults["platform_os"],
            priority=defaults["priority"],
            is_block=defaults["is_block"],
        )
    return EffectiveConfig(
        is_enable=True,
        is_all_tenants=False,
        is_in_flagged=bool(row.is_in_flagged),
        is_manual_sms=bool(row.is_manual_sms),
        is_manual_email=bool(row.is_manual_email),
        is_sms_enable=bool(row.is_sms_enable),
        is_email_enable=bool(row.is_email_enable),
        platform_os=(row.platform_os or PlatformOs.BOTH.value).lower(),
        priority=(row.priority or defaults["priority"]).lower(),
        is_block=bool(row.is_block),
    )
