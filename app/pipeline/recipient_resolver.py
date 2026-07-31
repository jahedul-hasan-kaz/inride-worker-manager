from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db.models import DeviceTokenInDB, NotificationConfigInDB, UserInDB
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
                UserInDB.notification_priority,
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

        rows = query.order_by(
            UserInDB.notification_priority.desc(),
            DeviceTokenInDB.created_at.desc(),
        ).all()

        devices_by_user: dict[UUID, list[DeviceTokenInDB]] = {}
        user_meta: dict[UUID, tuple[int, str]] = {}
        for device, priority, user_id, role in rows:
            token = (device.token or "").strip()
            if not token:
                continue
            devices_by_user.setdefault(user_id, []).append(device)
            user_meta[user_id] = (int(priority or 0), role or "")

        if not devices_by_user:
            return []

        config_rows = (
            session.query(NotificationConfigInDB)
            .filter(NotificationConfigInDB.user_id.in_(list(devices_by_user.keys())))
            .all()
        )
        config_by_user = {row.user_id: row for row in config_rows}

        targets: List[DeliveryTarget] = []
        for user_id, devices in devices_by_user.items():
            priority, role = user_meta.get(user_id, (0, ""))
            ctx = EligibilityContext(
                job=job,
                user_id=user_id,
                user_role=role,
                config=config_from_row(config_by_user.get(user_id)),
                devices=devices,
                session=session,
            )
            result = evaluate_user(ctx)
            if result.outcome == StepOutcome.SKIP_USER:
                logger.debug(
                    "Skipping user {} for notification {}: {}",
                    user_id,
                    job.notification_id,
                    ctx.skip_reason,
                )
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
                        notification_priority=priority,
                    )
                )

        return targets
