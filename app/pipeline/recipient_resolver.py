from __future__ import annotations

from typing import Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.constants.notification_constants import MessageDirection, NotificationType
from app.db.models import DeviceTokenInDB, UserInDB
from app.domain.models import DeliveryJob, DeliveryTarget
from app.eligibility.context import EffectiveConfig, EligibilityContext, StepOutcome
from app.eligibility.conversation_eligibility import (
    ConversationMatchContext,
    build_candidate_reasons_from_match,
    channel_enabled_for_job,
    conversation_paths_enabled,
    evaluate_conversation_eligibility_cached,
    load_conversation_match_context,
    resolve_lead_ids_for_notification,
)
from app.eligibility.notification_config_resolver import resolve_effective_config_for_job
from app.eligibility.pipeline import evaluate_user
from app.pipeline.device_token_lookup import list_active_by_tenant
from app.pipeline.push_trace import PushTrace

TENANT_HIERARCHY_REASON = "tenant_hierarchy"


class RecipientResolver:
    """Resolve Expo targets after per-user eligibility pipeline."""

    @staticmethod
    def resolve(
        session: Session,
        job: DeliveryJob,
        *,
        trace: Optional[PushTrace] = None,
    ) -> List[DeliveryTarget]:
        if job.tenant_id is None:
            if trace is not None:
                trace.skipped(reason="missing_tenant_id")
            return []

        if trace is not None:
            trace.resolve_config()

        effective_config, config_source = resolve_effective_config_for_job(session, job)
        if trace is not None:
            trace.config_resolved(
                source=config_source,
                is_enable=effective_config.is_enable,
                is_all_tenants=effective_config.is_all_tenants,
                ttl_sec=effective_config.ttl_sec,
                aggregation_type=effective_config.aggregation_type,
                aggregation_sec=effective_config.aggregation_sec,
            )

        if not effective_config.is_enable:
            if trace is not None:
                trace.skipped(reason="notifications_disabled", config_source=config_source)
            return []

        if effective_config.is_block:
            if trace is not None:
                trace.skipped(reason="tenant_blocked", config_source=config_source)
            return []

        if not channel_enabled_for_job(effective_config, job):
            notification_type = (job.notification_type or "").lower()
            reason = (
                "sms_disabled"
                if notification_type == NotificationType.SMS.value
                else "email_disabled"
            )
            if trace is not None:
                trace.skipped(reason=reason, config_source=config_source)
            return []

        # Channel on + manual/flagged off → broadcast via tenant hierarchy
        # (same rule as agent DeviceTokenCRUD.list_active_by_tenant), inbound or outbound.
        if not conversation_paths_enabled(effective_config, job):
            return RecipientResolver._resolve_tenant_hierarchy(
                session,
                job,
                effective_config,
                trace=trace,
            )

        return RecipientResolver._resolve_conversation_paths(
            session,
            job,
            effective_config,
            trace=trace,
        )

    @staticmethod
    def _exclude_actor_user_id(job: DeliveryJob) -> Optional[UUID]:
        if (job.direction or "").lower() == MessageDirection.OUTBOUND.value:
            return job.actor_user_id
        return None

    @staticmethod
    def _resolve_tenant_hierarchy(
        session: Session,
        job: DeliveryJob,
        effective_config: EffectiveConfig,
        *,
        trace: Optional[PushTrace],
    ) -> List[DeliveryTarget]:
        if trace is not None:
            trace.resolve_recipients()

        assert job.tenant_id is not None
        rows = list_active_by_tenant(
            session,
            job.tenant_id,
            exclude_user_id=RecipientResolver._exclude_actor_user_id(job),
        )

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

        if trace is not None:
            trace.match(
                candidates=len(devices_by_user),
                flagged=0,
                manual_reply=0,
                thread_id=job.thread_id,
            )
            trace.evaluate_eligibility()

        if not devices_by_user:
            if trace is not None:
                trace.skipped(reason="no_tenant_hierarchy_devices")
            return []

        qualifying_users = {
            user_id: [TENANT_HIERARCHY_REASON] for user_id in devices_by_user
        }
        return RecipientResolver._targets_from_qualifying_users(
            session,
            job,
            effective_config,
            qualifying_users=qualifying_users,
            devices_by_user=devices_by_user,
            user_roles=user_roles,
            conversation_match=None,
            candidate_count=len(devices_by_user),
            query_rows=len(rows),
            blank_token_rows=blank_token_rows,
            trace=trace,
        )

    @staticmethod
    def _resolve_conversation_paths(
        session: Session,
        job: DeliveryJob,
        effective_config: EffectiveConfig,
        *,
        trace: Optional[PushTrace],
    ) -> List[DeliveryTarget]:
        if trace is not None:
            trace.resolve_recipients()

        conversation_match = load_conversation_match_context(session, job)
        candidate_reasons = build_candidate_reasons_from_match(conversation_match)
        candidate_user_ids: Set[UUID] = set(candidate_reasons.keys())

        if not candidate_user_ids:
            lead_ids = resolve_lead_ids_for_notification(session, job)
            if trace is not None:
                trace.no_targets(
                    lead_ids=[str(lead_id) for lead_id in sorted(lead_ids, key=str)],
                )
            return []

        if trace is not None:
            trace.match(
                candidates=len(candidate_user_ids),
                flagged=len(conversation_match.flagged_user_ids),
                manual_reply=1 if conversation_match.manual_user_id else 0,
                thread_id=job.thread_id,
            )
            trace.evaluate_eligibility()

        qualifying_users: Dict[UUID, List[str]] = {}
        for user_id in sorted(candidate_user_ids, key=str):
            passed, reason, include_reasons = evaluate_conversation_eligibility_cached(
                session,
                job,
                user_id,
                effective_config,
                conversation_match,
            )
            if passed:
                qualifying_users[user_id] = include_reasons
                continue

            if trace is not None:
                trace.ineligible_user(
                    user_id=user_id,
                    devices=0,
                    reason=reason or "conversation_not_eligible",
                )

        if not qualifying_users:
            if trace is not None:
                trace.no_eligible_devices(candidates=len(candidate_user_ids))
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
                UserInDB.id.in_(qualifying_users.keys()),
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

        exclude_user_id = RecipientResolver._exclude_actor_user_id(job)
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

        return RecipientResolver._targets_from_qualifying_users(
            session,
            job,
            effective_config,
            qualifying_users=qualifying_users,
            devices_by_user=devices_by_user,
            user_roles=user_roles,
            conversation_match=conversation_match,
            candidate_count=len(candidate_user_ids),
            query_rows=len(rows),
            blank_token_rows=blank_token_rows,
            trace=trace,
        )

    @staticmethod
    def _targets_from_qualifying_users(
        session: Session,
        job: DeliveryJob,
        effective_config: EffectiveConfig,
        *,
        qualifying_users: Dict[UUID, List[str]],
        devices_by_user: dict[UUID, list[DeviceTokenInDB]],
        user_roles: dict[UUID, str],
        conversation_match: Optional[ConversationMatchContext],
        candidate_count: int,
        query_rows: int,
        blank_token_rows: int,
        trace: Optional[PushTrace],
    ) -> List[DeliveryTarget]:
        targets: List[DeliveryTarget] = []
        for user_id, include_reasons in qualifying_users.items():
            devices = devices_by_user.get(user_id, [])
            role = user_roles.get(user_id, "")
            ctx = EligibilityContext(
                job=job,
                user_id=user_id,
                user_role=role,
                config=effective_config,
                devices=devices,
                eligible_devices=[],
                tenant_config_row=None,
                session=session,
                trace=trace,
                conversation_match=conversation_match,
                job_gated=True,
                include_reasons=list(include_reasons),
            )
            result = evaluate_user(ctx)
            if result.outcome == StepOutcome.SKIP_USER:
                continue

            if not ctx.eligible_devices:
                if trace is not None:
                    trace.no_devices_user(user_id=user_id)
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

        if not targets:
            if trace is not None:
                trace.no_eligible_devices(
                    candidates=candidate_count,
                    query_rows=query_rows,
                    blank_tokens=blank_token_rows,
                )
            return []

        if trace is not None:
            trace.resolve_devices(resolved=len(targets))

        return targets
