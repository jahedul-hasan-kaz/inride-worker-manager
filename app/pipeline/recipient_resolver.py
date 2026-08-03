from __future__ import annotations

from typing import List
from uuid import UUID

from loguru import logger
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import DeviceTokenInDB, NotificationConfigInDB, UserInDB
from app.domain.models import DeliveryJob, DeliveryTarget
from app.eligibility.context import EligibilityContext, StepOutcome, config_from_row
from app.eligibility.pipeline import evaluate_user

# PLATFORM_ADMIN = "platform_admin"


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
                UserInDB.tenant_id == job.tenant_id,
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

        # Hierarchy — disabled: scope_tenant_id / platform_admin branching
        # exclude_user_id = job.actor_user_id
        # actor: Optional[UserInDB] = None
        # if exclude_user_id is not None:
        #     actor = session.query(UserInDB).filter(UserInDB.id == exclude_user_id).first()
        # if actor is None or actor.role != PLATFORM_ADMIN:
        #     query = query.filter(...)

        exclude_user_id = job.actor_user_id
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

        # Hierarchy — disabled: notification_tenants_config per-tenant overrides
        # tenant_config_by_user: dict[UUID, NotificationTenantConfigInDB] = {}
        # users_with_per_tenant = [...]
        # if users_with_per_tenant:
        #     tenant_rows = session.query(NotificationTenantConfigInDB)...

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
                tenant_config_row=None,
                session=session,
            )
            result = evaluate_user(ctx)
            if result.outcome == StepOutcome.SKIP_USER:
                continue

            for device in ctx.eligible_devices:
                token = (device.token or "").strip()
                if not token:
                    continue
                targets.append(
                    DeliveryTarget(
                        device_token_id=device.id,
                        push_token=token,
                        user_id=user_id,
                        notification_priority=0,
                    )
                )

        logger.info(
            "Resolved targets notification={} tenant={} type={} candidates={} eligible_devices={}",
            job.notification_id,
            job.tenant_id,
            job.notification_type,
            len(devices_by_user),
            len(targets),
        )
        return targets
