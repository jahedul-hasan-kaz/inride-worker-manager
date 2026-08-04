from __future__ import annotations

from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import DeviceTokenInDB, UserInDB


def list_active_by_tenant(
    session: Session,
    tenant_id: UUID,
    *,
    exclude_user_id: Optional[UUID] = None,
) -> List[Tuple[DeviceTokenInDB, UUID, str]]:
    """Active Expo tokens for users in the tenant hierarchy.

    Matches agent DeviceTokenCRUD.list_active_by_tenant: users whose tenant_id or
    scope_tenant_id equals the notification tenant (inbound and outbound).
    """
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
                UserInDB.tenant_id == tenant_id,
                UserInDB.scope_tenant_id == tenant_id,
            ),
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
    if exclude_user_id is not None:
        query = query.filter(DeviceTokenInDB.user_id != exclude_user_id)
    return query.order_by(DeviceTokenInDB.created_at.desc()).all()
