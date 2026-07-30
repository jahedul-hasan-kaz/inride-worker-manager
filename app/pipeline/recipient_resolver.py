from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db.models import DeviceTokenInDB, UserInDB
from app.domain.models import DeliveryJob, DeliveryTarget

PLATFORM_ADMIN = "platform_admin"


class RecipientResolver:
    """Resolve Expo targets ordered by user.notification_priority DESC."""

    @staticmethod
    def resolve(session: Session, job: DeliveryJob) -> List[DeliveryTarget]:
        if job.tenant_id is None:
            return []

        query = (
            session.query(
                DeviceTokenInDB,
                UserInDB.notification_priority,
                UserInDB.id,
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

        targets: List[DeliveryTarget] = []
        for device, priority, user_id in rows:
            token = (device.token or "").strip()
            if not token:
                continue
            targets.append(
                DeliveryTarget(
                    device_token_id=device.id,
                    push_token=token,
                    user_id=user_id,
                    notification_priority=int(priority or 0),
                )
            )
        return targets
