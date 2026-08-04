from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from loguru import logger

from app.db.session import session_scope
from app.domain.handle_result import HandleDisposition, HandleResult
from app.domain.models import DeliveryJob, DeliveryResult, DeviceSendOutcome, PushStatus
from app.domain.policies import DeliveryPolicy, ImmediateSingleNotificationPolicy
from app.monitoring.metrics import push_metrics
from app.pipeline.notification_pubsub_client import NotificationPubSubClient
from app.pipeline.claimer import NotificationClaimer
from app.pipeline.device_idempotency import DeviceIdempotency
from app.pipeline.finalizer import NotificationFinalizer
from app.pipeline.push_message_builder import build_push_item
from app.pipeline.push_types import PreparedPush
from app.pipeline.push_trace import PushTrace
from app.pipeline.recipient_resolver import RecipientResolver


class DeliveryRunner:
    """Orchestrates eligibility, tracking, and push delivery via notification Pub/Sub."""

    def __init__(
        self,
        *,
        policy: Optional[DeliveryPolicy] = None,
        sender: Optional[NotificationPubSubClient] = None,
    ) -> None:
        self.policy = policy or ImmediateSingleNotificationPolicy()
        self.sender = sender or NotificationPubSubClient()

    def handle_notification_id(self, notification_id: UUID) -> HandleResult:
        try:
            with session_scope() as session:
                job, miss_status = NotificationClaimer.claim_by_id(session, notification_id)

            if job is None:
                if miss_status in (PushStatus.SENT.value, PushStatus.FAILED.value):
                    logger.info(
                        "Notification {} already done status={}",
                        notification_id,
                        miss_status,
                    )
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
        logger.info("Processing push batch size={}", len(jobs))
        prepared: List[PreparedPush] = []
        early_results: dict[UUID, DeliveryResult] = {}
        traces: dict[UUID, PushTrace] = {}

        for job in jobs:
            try:
                self._prepare_job(job, prepared, early_results, traces)
            except Exception as exc:
                logger.exception(
                    "Failed preparing notification={} tenant={}: {}",
                    job.notification_id,
                    job.tenant_id,
                    exc,
                )
                with session_scope() as session:
                    NotificationFinalizer.finalize(
                        session,
                        job,
                        status=PushStatus.FAILED,
                        error=str(exc)[:500],
                    )
                early_results[job.notification_id] = DeliveryResult(
                    job=job,
                    notification_status=PushStatus.FAILED,
                    error=str(exc),
                )

        if not prepared:
            logger.info(
                "Push batch has no items to send (jobs={} early_results={})",
                len(jobs),
                len(early_results),
            )
            return self._finalize_early_only(jobs, early_results)

        logger.info(
            "Publishing push batch to notification Pub/Sub items={} notifications={}",
            len(prepared),
            len({item.job.notification_id for item in prepared}),
        )
        batch_response = self.sender.send_push_batch([item.payload for item in prepared])
        results_by_device = batch_response.results_by_device
        logger.info(
            "Notification Pub/Sub batch result sent={} failed={}",
            batch_response.sent_count,
            batch_response.failed_count,
        )

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
            trace = traces.get(job.notification_id)
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
            job_prepared = [p for p in prepared if p.job.notification_id == job.notification_id]
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

            sent = sum(1 for o in outcomes if o.result == "sent")
            failed = sum(1 for o in outcomes if o.result == "failed")
            skipped = sum(1 for o in outcomes if o.result == "skipped")

            if trace is not None and job_prepared:
                trace.publish(count=len(job_prepared))
                trace.complete(
                    publish_count=len(job_prepared),
                    resolved_devices=len(outcomes) or len(job_prepared),
                    sent=sent,
                    failed=failed,
                    skipped=skipped,
                    status=status.value,
                )

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

    def _prepare_job(
        self,
        job: DeliveryJob,
        prepared: List[PreparedPush],
        early_results: dict[UUID, DeliveryResult],
        traces: dict[UUID, PushTrace],
    ) -> None:
        trace = PushTrace(job.notification_id)
        traces[job.notification_id] = trace
        trace.start(
            notification_type=job.notification_type,
            direction=job.direction,
            tenant_id=job.tenant_id,
        )

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
            trace.policy_skip(status=skip.value)
            trace.complete(publish_count=0, resolved_devices=0, status=skip.value)
            return

        with session_scope() as session:
            targets = RecipientResolver.resolve(session, job, trace=trace)

        if not targets:
            with session_scope() as session:
                NotificationFinalizer.finalize(session, job, status=PushStatus.SENT)
            early_results[job.notification_id] = DeliveryResult(
                job=job,
                notification_status=PushStatus.SENT,
                device_outcomes=[],
            )
            trace.complete(publish_count=0, resolved_devices=0, status=PushStatus.SENT.value)
            return

        trace.idempotency()
        skipped_targets = 0
        prepared_before = len(prepared)
        for target in targets:
            with session_scope() as session:
                claimed = DeviceIdempotency.try_claim(session, job, target)

            trace.idempotency_claim(device_token_id=target.device_token_id, claimed=claimed)

            if not claimed:
                skipped_targets += 1
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

        prepared_for_job = len(prepared) - prepared_before
        trace.idempotency_skip(
            prepared=prepared_for_job,
            skipped=skipped_targets,
            total=len(targets),
        )

        if prepared_for_job == 0:
            trace.complete(
                publish_count=0,
                resolved_devices=len(targets),
                skipped=skipped_targets,
                status=PushStatus.SENT.value,
            )

    def _finalize_early_only(
        self,
        jobs: List[DeliveryJob],
        early_results: dict[UUID, DeliveryResult],
    ) -> List[DeliveryResult]:
        delivery_results: List[DeliveryResult] = []
        for job in jobs:
            early = early_results.get(job.notification_id)
            if early is None:
                continue
            if early.notification_status == PushStatus.SENT and not any(
                o.result == "sent" for o in early.device_outcomes
            ):
                with session_scope() as session:
                    DeviceIdempotency.seal_open_sending(session, job)
            delivery_results.append(early)
        return delivery_results
