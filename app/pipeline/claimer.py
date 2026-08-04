from __future__ import annotations

from typing import List, Optional, Sequence, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import NotificationInDB
from app.domain.models import DeliveryJob, JobKind, PushStatus
from app.eligibility.context import EffectiveConfig
from app.monitoring.metrics import push_metrics
from app.pipeline.delivery_timing import (
    aggregation_enabled,
    aggregation_window_sec,
    eligible_aggregation_types,
)


def _row_to_job(row: NotificationInDB) -> DeliveryJob:
    return DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=row.id,
        tenant_id=row.tenant_id,
        actor_user_id=row.user_id,
        notification_type=(row.type or "").lower(),
        preview_text=row.preview_text,
        preview_title=row.preview_title,
        sender=row.from_,
        to=row.to,
        thread_id=row.thread_id,
        direction=row.direction,
        email_log_id=row.email_log_id,
        sms_log_id=int(row.sms_log_id) if row.sms_log_id is not None else None,
        created_at=row.created_at,
        source_notification_ids=[row.id],
    )


class NotificationClaimer:
    @staticmethod
    def get_push_status(session: Session, notification_id: UUID) -> Optional[str]:
        row = (
            session.query(NotificationInDB.push_status)
            .filter(NotificationInDB.id == notification_id)
            .first()
        )
        if row is None:
            return None
        return row[0]

    @staticmethod
    def _load_jobs(session: Session, ids: Sequence[UUID]) -> List[DeliveryJob]:
        if not ids:
            return []
        rows = (
            session.query(NotificationInDB)
            .filter(NotificationInDB.id.in_(list(ids)))
            .order_by(NotificationInDB.created_at.asc())
            .all()
        )
        return [_row_to_job(row) for row in rows]

    @staticmethod
    def _mark_window_skipped(
        session: Session,
        agg_sec: int,
        eligible_types: Sequence[str],
    ) -> int:
        if not eligible_types:
            return 0
        window_sec = aggregation_window_sec(agg_sec)
        result = session.execute(
            text(
                """
                UPDATE public.notifications
                SET push_status = :skipped_status,
                    push_error = 'window_skipped',
                    updated_at = NOW()
                WHERE push_status = :pending_status
                  AND thread_id IS NOT NULL
                  AND btrim(thread_id) <> ''
                  AND type = ANY(:eligible_types)
                  AND created_at < NOW() - make_interval(secs => :window_sec)
                """
            ),
            {
                "skipped_status": PushStatus.WINDOW_SKIPPED.value,
                "pending_status": PushStatus.PENDING.value,
                "eligible_types": list(eligible_types),
                "window_sec": int(window_sec),
            },
        )
        count = int(result.rowcount or 0)
        if count:
            push_metrics.notification_finalized(PushStatus.WINDOW_SKIPPED.value)
            logger.warning(
                "Marked {} notification(s) window_skipped "
                "(older than aggregation window_sec={} agg_sec={})",
                count,
                window_sec,
                agg_sec,
            )
        return count

    @staticmethod
    def _claim_digest_threads(
        session: Session,
        thread_limit: int,
        agg_sec: int,
        eligible_types: Sequence[str],
    ) -> List[UUID]:
        if thread_limit <= 0 or not eligible_types:
            return []
        window_sec = aggregation_window_sec(agg_sec)
        result = session.execute(
            text(
                """
                WITH windowed_threads AS (
                    SELECT thread_id
                    FROM public.notifications
                    WHERE push_status = :pending_status
                      AND thread_id IS NOT NULL
                      AND btrim(thread_id) <> ''
                      AND created_at >= NOW() - make_interval(secs => :window_sec)
                      AND created_at < NOW()
                      AND type = ANY(:eligible_types)
                    GROUP BY thread_id
                    ORDER BY MIN(created_at)
                    LIMIT :thread_limit
                ),
                claimed AS (
                    SELECT n.id
                    FROM public.notifications n
                    JOIN windowed_threads w ON n.thread_id = w.thread_id
                    WHERE n.push_status = :pending_status
                      AND n.type = ANY(:eligible_types)
                      AND n.created_at >= NOW() - make_interval(secs => :window_sec)
                      AND n.created_at < NOW()
                    FOR UPDATE OF n SKIP LOCKED
                )
                UPDATE public.notifications n
                SET push_status = :agg_status,
                    updated_at = NOW(),
                    push_error = NULL
                FROM claimed
                WHERE n.id = claimed.id
                RETURNING n.id
                """
            ),
            {
                "pending_status": PushStatus.PENDING.value,
                "agg_status": PushStatus.AGGREGATED_PROCESSING.value,
                "window_sec": int(window_sec),
                "eligible_types": list(eligible_types),
                "thread_limit": int(thread_limit),
            },
        )
        ids = [row[0] for row in result.fetchall()]
        if ids:
            push_metrics.claim("aggregated_processing", amount=len(ids))
        return ids

    @staticmethod
    def _claim_singles_batch(
        session: Session,
        limit: int,
        *,
        exclude_types: Optional[Sequence[str]] = None,
        require_null_thread: bool = False,
    ) -> List[UUID]:
        if limit <= 0:
            return []
        params: dict = {
            "pending_status": PushStatus.PENDING.value,
            "processing_status": PushStatus.PROCESSING.value,
            "limit": int(limit),
        }
        type_filter = ""
        if exclude_types:
            type_filter = "AND type <> ALL(:exclude_types)"
            params["exclude_types"] = list(exclude_types)
        thread_filter = ""
        if require_null_thread:
            thread_filter = "AND (thread_id IS NULL OR btrim(thread_id) = '')"

        result = session.execute(
            text(
                f"""
                WITH picked AS (
                    SELECT id
                    FROM public.notifications
                    WHERE push_status = :pending_status
                      {type_filter}
                      {thread_filter}
                    ORDER BY created_at ASC
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE public.notifications n
                SET push_status = :processing_status,
                    updated_at = NOW(),
                    push_error = NULL
                FROM picked
                WHERE n.id = picked.id
                RETURNING n.id
                """
            ),
            params,
        )
        ids = [row[0] for row in result.fetchall()]
        if ids:
            push_metrics.claim("won", amount=len(ids))
        return ids

    @staticmethod
    def claim_by_id(
        session: Session,
        notification_id: UUID,
        *,
        effective_config: Optional[EffectiveConfig] = None,
    ) -> Tuple[Optional[DeliveryJob], Optional[str]]:
        """Returns (job, miss_status). effective_config is accepted for API compatibility."""
        del effective_config
        result = session.execute(
            text(
                """
                UPDATE public.notifications
                SET push_status = :processing_status,
                    updated_at = NOW(),
                    push_error = NULL
                WHERE id = :id AND push_status = :pending_status
                RETURNING id
                """
            ),
            {
                "id": str(notification_id),
                "processing_status": PushStatus.PROCESSING.value,
                "pending_status": PushStatus.PENDING.value,
            },
        )
        claimed_id = result.scalar_one_or_none()
        if claimed_id is None:
            status = NotificationClaimer.get_push_status(session, notification_id)
            session.commit()
            if status is None:
                push_metrics.claim("miss_missing")
            elif status in (
                PushStatus.PROCESSING.value,
                PushStatus.AGGREGATED_PROCESSING.value,
            ):
                push_metrics.claim("miss_in_flight")
            elif status in (PushStatus.SENT.value, PushStatus.FAILED.value):
                push_metrics.claim("miss_done")
            else:
                push_metrics.claim("miss")
            return None, status

        session.commit()
        push_metrics.claim("won")
        row = session.query(NotificationInDB).filter(NotificationInDB.id == notification_id).one()
        return _row_to_job(row), None

    @staticmethod
    def claim_pending_batch(
        session: Session,
        limit: int,
        *,
        config: Optional[EffectiveConfig] = None,
    ) -> List[DeliveryJob]:
        """Claim pending notifications for processing."""
        if limit <= 0:
            return []

        claimed_ids: List[UUID] = []

        if config is None or not aggregation_enabled(config):
            claimed_ids.extend(NotificationClaimer._claim_singles_batch(session, limit))
        else:
            agg_sec = int(config.aggregation_sec)
            eligible_types = eligible_aggregation_types(config)
            # Claim the current window first, then terminalize anything still
            # pending that aged out — avoids skipping a row we could claim now.
            digest_ids = NotificationClaimer._claim_digest_threads(
                session,
                thread_limit=limit,
                agg_sec=agg_sec,
                eligible_types=eligible_types,
            )
            claimed_ids.extend(digest_ids)
            ineligible_ids = NotificationClaimer._claim_singles_batch(
                session,
                limit,
                exclude_types=eligible_types,
            )
            claimed_ids.extend(ineligible_ids)
            null_thread_ids = NotificationClaimer._claim_singles_batch(
                session,
                limit,
                require_null_thread=True,
            )
            claimed_ids.extend(null_thread_ids)
            skipped = NotificationClaimer._mark_window_skipped(
                session, agg_sec, eligible_types
            )
            logger.info(
                "Aggregation claim agg_sec={} window_sec={} eligible_types={} "
                "digest={} ineligible_singles={} null_thread_singles={} window_skipped={}",
                agg_sec,
                aggregation_window_sec(agg_sec),
                eligible_types,
                len(digest_ids),
                len(ineligible_ids),
                len(null_thread_ids),
                skipped,
            )

        session.commit()
        if not claimed_ids:
            logger.debug("Claim batch: no pending notifications")
            return []

        logger.info(
            "Claimed {} pending notification(s): {}",
            len(claimed_ids),
            [str(notification_id) for notification_id in claimed_ids],
        )
        return NotificationClaimer._load_jobs(session, claimed_ids)
