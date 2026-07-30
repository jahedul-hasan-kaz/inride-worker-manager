from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger

from app.db.session import session_scope
from app.domain.handle_result import HandleDisposition, HandleResult
from app.domain.models import DeliveryJob, DeliveryResult, DeviceSendOutcome, PushStatus
from app.domain.policies import DeliveryPolicy, ImmediateSingleNotificationPolicy
from app.monitoring.metrics import push_metrics
from app.pipeline.claimer import NotificationClaimer
from app.pipeline.device_idempotency import DeviceIdempotency
from app.pipeline.expo_sender import ExpoSender
from app.pipeline.finalizer import NotificationFinalizer
from app.pipeline.recipient_resolver import RecipientResolver


class DeliveryRunner:
    """Orchestrates policy → claim → recipients → per-device send → finalize."""

    def __init__(
        self,
        *,
        policy: Optional[DeliveryPolicy] = None,
        sender: Optional[ExpoSender] = None,
    ) -> None:
        self.policy = policy or ImmediateSingleNotificationPolicy()
        self.sender = sender or ExpoSender()

    def handle_notification_id(self, notification_id: UUID) -> HandleResult:
        """Ingress entrypoint with explicit ack/nack disposition."""
        try:
            with session_scope() as session:
                job, miss_status = NotificationClaimer.claim_by_id(session, notification_id)

            if job is None:
                if miss_status in (PushStatus.SENT.value, PushStatus.FAILED.value):
                    return HandleResult(
                        disposition=HandleDisposition.ACK_ALREADY_DONE,
                        detail=miss_status,
                    )
                if miss_status == PushStatus.PROCESSING.value:
                    return HandleResult(
                        disposition=HandleDisposition.ACK_IN_FLIGHT,
                        detail=miss_status,
                    )
                return HandleResult(
                    disposition=HandleDisposition.NACK_RETRY,
                    detail=miss_status or "missing",
                )

            delivery = self._execute_job(job)
            return HandleResult(
                disposition=HandleDisposition.ACK_PROCESSED,
                delivery=delivery,
            )
        except Exception as exc:
            logger.exception("Unhandled error processing notification {}: {}", notification_id, exc)
            return HandleResult(
                disposition=HandleDisposition.NACK_ERROR,
                detail=str(exc),
            )

    def process_job(self, job: DeliveryJob) -> DeliveryResult:
        return self._execute_job(job)

    def _execute_job(self, job: DeliveryJob) -> DeliveryResult:
        if not self.policy.should_deliver(job):
            skip = self.policy.skip_status(job) or PushStatus.FAILED
            with session_scope() as session:
                NotificationFinalizer.finalize(
                    session,
                    job,
                    status=skip,
                    error="skipped_by_policy",
                )
            return DeliveryResult(job=job, notification_status=skip, error="skipped_by_policy")

        with session_scope() as session:
            targets = RecipientResolver.resolve(session, job)

        if not targets:
            with session_scope() as session:
                NotificationFinalizer.finalize(session, job, status=PushStatus.SENT)
            return DeliveryResult(job=job, notification_status=PushStatus.SENT, device_outcomes=[])

        outcomes: list[DeviceSendOutcome] = []
        invalid_token_ids: list[UUID] = []

        for target in targets:
            # Short DB TX only — connection released before Expo HTTP.
            with session_scope() as session:
                claimed = DeviceIdempotency.try_claim(session, job, target)

            if not claimed:
                push_metrics.device_attempted("skipped")
                outcomes.append(
                    DeviceSendOutcome(device_token_id=target.device_token_id, result="skipped")
                )
                continue

            ok, error, deactivate = self.sender.send(job, target)

            with session_scope() as session:
                if ok:
                    DeviceIdempotency.mark_sent(session, job, target.device_token_id)
                    push_metrics.device_attempted("sent")
                    outcomes.append(
                        DeviceSendOutcome(device_token_id=target.device_token_id, result="sent")
                    )
                else:
                    DeviceIdempotency.release_claim(session, job, target.device_token_id)
                    push_metrics.device_attempted("failed")
                    outcomes.append(
                        DeviceSendOutcome(
                            device_token_id=target.device_token_id,
                            result="failed",
                            error=error,
                        )
                    )
                    if deactivate:
                        invalid_token_ids.append(target.device_token_id)

        with session_scope() as session:
            if invalid_token_ids:
                NotificationFinalizer.deactivate_tokens(session, invalid_token_ids)

            # Prior crash may have left ``sending`` rows; never Expo again — seal them.
            DeviceIdempotency.seal_open_sending(session, job)

            any_sent = any(o.result == "sent" for o in outcomes)
            if not any_sent:
                any_sent = DeviceIdempotency.has_any_sent(session, job)

            status = PushStatus.SENT if any_sent else PushStatus.FAILED
            error = None if any_sent else "all_device_sends_failed"
            NotificationFinalizer.finalize(session, job, status=status, error=error)

        if status == PushStatus.FAILED:
            logger.warning(
                "Notification {} finalized failed after {} device attempt(s)",
                job.notification_id,
                len(outcomes),
            )

        return DeliveryResult(
            job=job,
            notification_status=status,
            device_outcomes=outcomes,
            error=error,
        )
