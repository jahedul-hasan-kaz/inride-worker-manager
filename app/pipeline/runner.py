from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger

from app.db.session import session_scope
from app.domain.handle_result import HandleDisposition, HandleResult
from app.domain.models import DeliveryJob, DeliveryResult, DeviceSendOutcome, PushStatus
from app.domain.policies import DeliveryPolicy, ImmediateSingleNotificationPolicy
from app.monitoring.metrics import push_metrics
from app.pipeline.agent_management_client import AgentManagementClient
from app.pipeline.claimer import NotificationClaimer
from app.pipeline.device_idempotency import DeviceIdempotency
from app.pipeline.finalizer import NotificationFinalizer
from app.pipeline.recipient_resolver import RecipientResolver


class DeliveryRunner:
    """Orchestrates policy → claim → recipients → agent push API → finalize."""

    def __init__(
        self,
        *,
        policy: Optional[DeliveryPolicy] = None,
        sender: Optional[AgentManagementClient] = None,
    ) -> None:
        self.policy = policy or ImmediateSingleNotificationPolicy()
        self.sender = sender or AgentManagementClient()

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

        claimed_targets = []
        skipped_outcomes: list[DeviceSendOutcome] = []

        for target in targets:
            with session_scope() as session:
                claimed = DeviceIdempotency.try_claim(session, job, target)

            if not claimed:
                push_metrics.device_attempted("skipped")
                skipped_outcomes.append(
                    DeviceSendOutcome(device_token_id=target.device_token_id, result="skipped")
                )
                continue
            claimed_targets.append(target)

        if not claimed_targets:
            with session_scope() as session:
                DeviceIdempotency.seal_open_sending(session, job)
                any_sent = DeviceIdempotency.has_any_sent(session, job)
                status = PushStatus.SENT if any_sent else PushStatus.SENT
                NotificationFinalizer.finalize(session, job, status=status)
            return DeliveryResult(
                job=job,
                notification_status=PushStatus.SENT,
                device_outcomes=skipped_outcomes,
            )

        ok, error, deactivated_ids = self.sender.send_push(job, claimed_targets)
        outcomes: list[DeviceSendOutcome] = list(skipped_outcomes)

        with session_scope() as session:
            if ok:
                for target in claimed_targets:
                    DeviceIdempotency.mark_sent(session, job, target.device_token_id)
                    push_metrics.device_attempted("sent")
                    outcomes.append(
                        DeviceSendOutcome(device_token_id=target.device_token_id, result="sent")
                    )
            else:
                for target in claimed_targets:
                    DeviceIdempotency.release_claim(session, job, target.device_token_id)
                    push_metrics.device_attempted("failed")
                    outcomes.append(
                        DeviceSendOutcome(
                            device_token_id=target.device_token_id,
                            result="failed",
                            error=error,
                        )
                    )

            if deactivated_ids:
                NotificationFinalizer.deactivate_tokens(session, deactivated_ids)

            DeviceIdempotency.seal_open_sending(session, job)
            any_sent = ok or DeviceIdempotency.has_any_sent(session, job)
            status = PushStatus.SENT if any_sent else PushStatus.FAILED
            finalize_error = None if any_sent else (error or "all_device_sends_failed")
            NotificationFinalizer.finalize(session, job, status=status, error=finalize_error)

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
            error=finalize_error,
        )
