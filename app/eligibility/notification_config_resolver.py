from __future__ import annotations

from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import NotificationConfigInDB, NotificationTenantConfigInDB
from app.domain.models import DeliveryJob
from app.eligibility.context import (
    EffectiveConfig,
    config_from_row,
    default_effective_config,
    tenant_config_from_row,
)


def load_latest_global_notification_config(
    session: Session,
) -> Optional[NotificationConfigInDB]:
    """Latest global row; user_id is tracking only and is not used for lookup."""
    return (
        session.query(NotificationConfigInDB)
        .order_by(NotificationConfigInDB.created_at.desc(), NotificationConfigInDB.id.desc())
        .first()
    )


def load_global_effective_config(session: Session) -> EffectiveConfig:
    global_row = load_latest_global_notification_config(session)
    if global_row is None:
        return default_effective_config()
    return config_from_row(global_row)


def load_latest_tenant_notification_config(
    session: Session,
    tenant_id: UUID,
) -> Optional[NotificationTenantConfigInDB]:
    """Latest tenant row; user_id is tracking only and is not used for lookup."""
    return (
        session.query(NotificationTenantConfigInDB)
        .filter(NotificationTenantConfigInDB.tenant_id == tenant_id)
        .order_by(
            NotificationTenantConfigInDB.created_at.desc(),
            NotificationTenantConfigInDB.id.desc(),
        )
        .first()
    )


def resolve_effective_config_for_job(
    session: Session,
    job: DeliveryJob,
) -> Tuple[EffectiveConfig, str]:
    """
    Resolve push config for a notification job.

    - Global master switch and mode come from the latest notification_config row.
    - When is_all_tenants is false, channel/conversation flags come from the latest
      notification_tenants_config row for job.tenant_id (user_id ignored).
    """
    global_row = load_latest_global_notification_config(session)
    if global_row is None:
        return default_effective_config(), "default"

    global_config = config_from_row(global_row)
    if not global_config.is_enable:
        return global_config, "global_disabled"

    if global_config.is_all_tenants:
        return global_config, "global"

    if job.tenant_id is None:
        return global_config, "tenant_mode_missing_tenant_id"

    tenant_row = load_latest_tenant_notification_config(session, job.tenant_id)
    tenant_config = tenant_config_from_row(tenant_row)
    if tenant_config.is_block:
        return EffectiveConfig(
            is_enable=global_config.is_enable,
            is_all_tenants=False,
            is_block=True,
            ttl_sec=global_config.ttl_sec,
            aggregation_type=global_config.aggregation_type,
            aggregation_sec=global_config.aggregation_sec,
        ), "tenant_blocked"

    return EffectiveConfig(
        is_enable=global_config.is_enable,
        is_all_tenants=False,
        is_in_flagged=tenant_config.is_in_flagged,
        is_manual_sms=tenant_config.is_manual_sms,
        is_manual_email=tenant_config.is_manual_email,
        is_sms_enable=tenant_config.is_sms_enable,
        is_email_enable=tenant_config.is_email_enable,
        platform_os=tenant_config.platform_os,
        priority=tenant_config.priority,
        is_block=False,
        ttl_sec=global_config.ttl_sec,
        aggregation_type=global_config.aggregation_type,
        aggregation_sec=global_config.aggregation_sec,
    ), "tenant"
