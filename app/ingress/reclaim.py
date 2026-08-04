from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import config
from app.monitoring.metrics import push_metrics


def reclaim_stale(session: Session) -> int:
    """Requeue stuck notifications without allowing duplicate Expo sends.

    Critical: do NOT delete ``sending`` delivery rows. Those rows are the
    idempotency lock. If Expo succeeded and the process died before
    ``mark_sent``, deleting ``sending`` would allow a second push.

    Instead, promote leftover ``sending`` → ``sent`` for reclaimed
    notifications (prefer no duplicate over retrying an uncertain device).
    """
    seconds = int(config.RECLAIM_AFTER_SECONDS)
    result = session.execute(
        text(
            """
            WITH reclaimed AS (
                UPDATE public.notifications
                SET push_status = 'pending',
                    updated_at = NOW(),
                    push_error = NULL
                WHERE push_status IN ('processing', 'aggregated_processing')
                  AND updated_at < NOW() - make_interval(secs => :seconds)
                RETURNING id
            ),
            sealed AS (
                UPDATE public.notification_push_deliveries d
                SET status = 'sent',
                    updated_at = NOW()
                FROM reclaimed r
                WHERE d.notification_id = r.id
                  AND d.status = 'sending'
                RETURNING d.id
            )
            SELECT
                (SELECT COUNT(*) FROM reclaimed) AS notifications_reclaimed,
                (SELECT COUNT(*) FROM sealed) AS deliveries_sealed
            """
        ),
        {"seconds": seconds},
    )
    row = result.first()
    notifications_reclaimed = int(row[0] or 0) if row else 0
    deliveries_sealed = int(row[1] or 0) if row else 0
    session.commit()

    push_metrics.reclaim(notifications_reclaimed)
    if deliveries_sealed:
        push_metrics.claim("sending_sealed_on_reclaim", amount=deliveries_sealed)
    return notifications_reclaimed
