from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.constants.notification_constants import priority_to_weight
from app.db.models import DeviceTokenInDB, NotificationConfigInDB, NotificationTenantConfigInDB, UserInDB
from app.domain.models import DeliveryJob, DeliveryTarget
from app.eligibility.context import EligibilityContext, StepOutcome, config_from_row
from app.eligibility.pipeline import evaluate_user

PLATFORM_ADMIN = "platform_admin"


class RecipientResolver:
    """Resolve Expo targets after per-user eligibility pipeline."""

    @staticmethod
    def resolve(session: Session, job: DeliveryJob) -> List[DeliveryTarget]:
        if job.tenant_id is None:
            return []

        query = (
            session.query(
                DeviceTokenInDB,
                UserInDB.id,
                UserInDB.role,
            )
            .join(UserInDB, UserInDB.id == DeviceTokenInDB.user_id)
            .filter(
                DeviceTokenInDB.is_active.is_(True),
                or_(
                    UserInDB.is_notify_mobile.is_(True),
                    UserInDB.is_notify_mobile.is_(None),
                ),
                or_(
                    UserInDB.is_disabled.is_(False),
                    UserInDB.is_disabled.is_(None),
                ),
            )
        )

        exclude_user_id = job.actor_user_id
        actor: Optional[UserInDB] = None
        if exclude_user_id is not None:
            actor = session.query(UserInDB).filter(UserInDB.id == exclude_user_id).first()

        if actor is None or actor.role != PLATFORM_ADMIN:
            query = query.filter(
                or_(
                    and_(
                        or_(
                            UserInDB.tenant_id == job.tenant_id,
                            UserInDB.scope_tenant_id == job.tenant_id,
                        ),
                        UserInDB.role != PLATFORM_ADMIN,
                    ),
                    UserInDB.role == PLATFORM_ADMIN,
                )
            )

        if exclude_user_id is not None:
            query = query.filter(DeviceTokenInDB.user_id != exclude_user_id)

        rows = query.order_by(DeviceTokenInDB.created_at.desc()).all()

        devices_by_user: dict[UUID, list[DeviceTokenInDB]] = {}
        user_roles: dict[UUID, str] = {}
        for device, user_id, role in rows:
            token = (device.token or "").strip()
            if not token:
                continue
            devices_by_user.setdefault(user_id, []).append(device)
            user_roles[user_id] = role or ""

        if not devices_by_user:
            logger.info(
                "No device tokens for notification={} tenant={}",
                job.notification_id,
                job.tenant_id,
            )
            return []

        config_rows = (
            session.query(NotificationConfigInDB)
            .filter(NotificationConfigInDB.user_id.in_(list(devices_by_user.keys())))
            .all()
        )
        config_by_user = {row.user_id: row for row in config_rows}

        tenant_config_by_user: dict[UUID, NotificationTenantConfigInDB] = {}
        users_with_per_tenant = [
            user_id
            for user_id in devices_by_user
            if config_by_user.get(user_id) is not None and not config_by_user[user_id].is_all_tenants
        ]
        if users_with_per_tenant:
            tenant_rows = (
                session.query(NotificationTenantConfigInDB)
                .filter(
                    NotificationTenantConfigInDB.tenant_id == job.tenant_id,
                    NotificationTenantConfigInDB.user_id.in_(users_with_per_tenant),
                )
                .all()
            )
            tenant_config_by_user = {row.user_id: row for row in tenant_rows}

        targets: List[DeliveryTarget] = []
        for user_id, devices in devices_by_user.items():
            role = user_roles.get(user_id, "")
            global_config = config_from_row(config_by_user.get(user_id))
            ctx = EligibilityContext(
                job=job,
                user_id=user_id,
                user_role=role,
                config=global_config,
                devices=devices,
                tenant_config_row=tenant_config_by_user.get(user_id),
                session=session,
            )
            result = evaluate_user(ctx)
            if result.outcome == StepOutcome.SKIP_USER:
                continue

            delivery_priority = (
                priority_to_weight(ctx.config.priority)
                if not global_config.is_all_tenants
                else 0
            )

            for device in ctx.eligible_devices:
                token = (device.token or "").strip()
                if not token:
                    continue
                targets.append(
                    DeliveryTarget(
                        device_token_id=device.id,
                        push_token=token,
                        user_id=user_id,
                        notification_priority=delivery_priority,
                    )
                )

        targets.sort(key=lambda target: target.notification_priority, reverse=True)
        logger.info(
            "Resolved targets notification={} tenant={} type={} candidates={} eligible_devices={}",
            job.notification_id,
            job.tenant_id,
            job.notification_type,
            len(devices_by_user),
            len(targets),
        )
        return targets
