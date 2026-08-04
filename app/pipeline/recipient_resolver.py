from __future__ import annotations

from typing import List
from uuid import UUID

from loguru import logger
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.constants.notification_constants import MessageDirection
from app.db.models import DeviceTokenInDB, NotificationConfigInDB, UserInDB
from app.domain.models import DeliveryJob, DeliveryTarget
from app.eligibility.context import EligibilityContext, StepOutcome, config_from_row
from app.eligibility.conversation_eligibility import (
    get_conversation_candidate_reasons,
    get_conversation_match_summary,
    resolve_lead_ids_for_notification,
)
from app.eligibility.pipeline import evaluate_user

# PLATFORM_ADMIN = "platform_admin"


class RecipientResolver:
    """Resolve Expo targets after per-user eligibility pipeline."""

    @staticmethod
    def resolve(session: Session, job: DeliveryJob) -> List[DeliveryTarget]:
        if job.tenant_id is None:
            return []

        flagged_users, manual_reply_user = get_conversation_match_summary(session, job)
        candidate_reasons = get_conversation_candidate_reasons(session, job)
        candidate_user_ids = set(candidate_reasons.keys())
        if not candidate_user_ids:
            lead_ids = resolve_lead_ids_for_notification(session, job)
            logger.info(
                "No conversation candidates notification={} tenant={} type={} thread_id={} lead_ids={} direction={}",
                job.notification_id,
                job.tenant_id,
                job.notification_type,
                job.thread_id,
                [str(lead_id) for lead_id in sorted(lead_ids, key=str)],
                job.direction,
            )
            return []

        logger.info(
            "Conversation matches notification={} tenant={} type={} thread_id={} "
            "flagged_users={} manual_reply_user={}",
            job.notification_id,
            job.tenant_id,
            job.notification_type,
            job.thread_id,
            [str(user_id) for user_id in sorted(flagged_users, key=str)],
            str(manual_reply_user) if manual_reply_user else None,
        )

        query = (
            session.query(
                DeviceTokenInDB,
                UserInDB.id,
                UserInDB.role,
            )
            .join(UserInDB, UserInDB.id == DeviceTokenInDB.user_id)
            .filter(
                DeviceTokenInDB.is_active.is_(True),
                UserInDB.id.in_(candidate_user_ids),
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

        exclude_user_id = None
        if (job.direction or "").lower() == MessageDirection.OUTBOUND.value:
            exclude_user_id = job.actor_user_id
        if exclude_user_id is not None:
            query = query.filter(DeviceTokenInDB.user_id != exclude_user_id)

        rows = query.order_by(DeviceTokenInDB.created_at.desc()).all()

        devices_by_user: dict[UUID, list[DeviceTokenInDB]] = {}
        user_roles: dict[UUID, str] = {}
        blank_token_rows = 0
        for device, user_id, role in rows:
            token = (device.token or "").strip()
            if not token:
                blank_token_rows += 1
                continue
            devices_by_user.setdefault(user_id, []).append(device)
            user_roles[user_id] = role or ""

        if not devices_by_user:
            logger.info(
                "No device tokens notification={} tenant={} candidates={} direction={} "
                "excluded_actor={} query_rows={} blank_token_rows={}",
                job.notification_id,
                job.tenant_id,
                [str(user_id) for user_id in sorted(candidate_user_ids, key=str)],
                job.direction,
                str(exclude_user_id) if exclude_user_id else None,
                len(rows),
                blank_token_rows,
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
            "Resolved targets notification={} tenant={} type={} conversation_candidates={} eligible_devices={}",
            job.notification_id,
            job.tenant_id,
            job.notification_type,
            len(candidate_user_ids),
            len(targets),
        )
        return targets
