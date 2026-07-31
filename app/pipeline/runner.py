from __future__ import annotations

from typing import List, Optional
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
from app.pipeline.push_message_builder import build_push_item
from app.pipeline.push_types import PreparedPush
from app.pipeline.recipient_resolver import RecipientResolver


class DeliveryRunner:
    """Orchestrates eligibility, tracking, and batched Expo delivery via agent-management."""

    def __init__(
        self,
        *,
        policy: Optional[DeliveryPolicy] = None,
        sender: Optional[AgentManagementClient] = None,
    ) -> None:
        self.policy = policy or ImmediateSingleNotificationPolicy()
        self.sender = sender or AgentManagementClient()

    def handle_notification_id(self, notification_id: UUID) -> HandleResult:
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

            results = self.process_batch([job])
            delivery = results[0] if results else None
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
        results = self.process_batch([job])
        return results[0]

    def process_batch(self, jobs: List[DeliveryJob]) -> List[DeliveryResult]:
        prepared: List[PreparedPush] = []
        early_results: dict[UUID, DeliveryResult] = {}

        for job in jobs:
            if not self.policy.should_deliver(job):
                skip = self.policy.skip_status(job) or PushStatus.FAILED
                with session_scope() as session:
                    NotificationFinalizer.finalize(
                        session,
                        job,
                        status=skip,
                        error="skipped_by_policy",
                    )
                early_results[job.notification_id] = DeliveryResult(
                    job=job,
                    notification_status=skip,
                    error="skipped_by_policy",
                )
                continue

            with session_scope() as session:
                targets = RecipientResolver.resolve(session, job)

            if not targets:
                with session_scope() as session:
                    NotificationFinalizer.finalize(session, job, status=PushStatus.SENT)
                early_results[job.notification_id] = DeliveryResult(
                    job=job,
                    notification_status=PushStatus.SENT,
                    device_outcomes=[],
                )
                continue

            for target in targets:
                with session_scope() as session:
                    claimed = DeviceIdempotency.try_claim(session, job, target)

                if not claimed:
                    push_metrics.device_attempted("skipped")
                    early_results.setdefault(
                        job.notification_id,
                        DeliveryResult(job=job, notification_status=PushStatus.SENT),
                    )
                    early_results[job.notification_id].device_outcomes.append(
                        DeviceSendOutcome(device_token_id=target.device_token_id, result="skipped")
                    )
                    continue

                prepared.append(
                    PreparedPush(
                        job=job,
                        target=target,
                        payload=build_push_item(job, target),
                    )
                )

        batch_response = self.sender.send_push_batch([item.payload for item in prepared])
        results_by_device = batch_response.results_by_device

        outcomes_by_job: dict[UUID, list[DeviceSendOutcome]] = {}
        for job_id, early in early_results.items():
            outcomes_by_job[job_id] = list(early.device_outcomes)

        deactivated_ids: list[UUID] = []

        for item in prepared:
            job_id = item.job.notification_id
            outcomes_by_job.setdefault(job_id, [])
            send_result = results_by_device.get(item.target.device_token_id)

            with session_scope() as session:
                if send_result and send_result.status == "sent":
                    DeviceIdempotency.mark_sent(session, item.job, item.target.device_token_id)
                    push_metrics.device_attempted("sent")
                    outcomes_by_job[job_id].append(
                        DeviceSendOutcome(
                            device_token_id=item.target.device_token_id,
                            result="sent",
                        )
                    )
                else:
                    DeviceIdempotency.release_claim(session, item.job, item.target.device_token_id)
                    push_metrics.device_attempted("failed")
                    error = send_result.error if send_result else "agent_push_batch_failed"
                    outcomes_by_job[job_id].append(
                        DeviceSendOutcome(
                            device_token_id=item.target.device_token_id,
                            result="failed",
                            error=error,
                        )
                    )
                    if send_result and send_result.deactivate:
                        deactivated_ids.append(item.target.device_token_id)

        delivery_results: List[DeliveryResult] = []

        for job in jobs:
            if job.notification_id in early_results and not any(
                p.job.notification_id == job.notification_id for p in prepared
            ):
                early = early_results[job.notification_id]
                if early.notification_status == PushStatus.SENT and not any(
                    o.result == "sent" for o in early.device_outcomes
                ):
                    with session_scope() as session:
                        DeviceIdempotency.seal_open_sending(session, job)
                        NotificationFinalizer.finalize(session, job, status=PushStatus.SENT)
                delivery_results.append(early)
                continue

            outcomes = outcomes_by_job.get(job.notification_id, [])
            job_deactivated = [
                token_id
                for token_id in deactivated_ids
                if any(o.device_token_id == token_id and o.result == "failed" for o in outcomes)
            ]

            with session_scope() as session:
                if job_deactivated:
                    NotificationFinalizer.deactivate_tokens(session, job_deactivated)

                DeviceIdempotency.seal_open_sending(session, job)
                any_sent = any(o.result == "sent" for o in outcomes) or DeviceIdempotency.has_any_sent(
                    session, job
                )
                status = PushStatus.SENT if any_sent else PushStatus.FAILED
                finalize_error = None if any_sent else "all_device_sends_failed"
                NotificationFinalizer.finalize(session, job, status=status, error=finalize_error)

            if status == PushStatus.FAILED and outcomes:
                logger.warning(
                    "Notification {} finalized failed after {} device attempt(s)",
                    job.notification_id,
                    len(outcomes),
                )

            delivery_results.append(
                DeliveryResult(
                    job=job,
                    notification_status=status,
                    device_outcomes=outcomes,
                    error=finalize_error,
                )
            )

        return delivery_results
