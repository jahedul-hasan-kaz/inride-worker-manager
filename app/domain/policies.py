from __future__ import annotations

from typing import Optional, Protocol

from app.domain.models import DeliveryJob, PushStatus


class DeliveryPolicy(Protocol):
    """Pluggable gate before claim/send. v1 = always deliver; future TTL/aggregate."""

    def should_deliver(self, job: DeliveryJob) -> bool:
        ...

    def skip_status(self, job: DeliveryJob) -> Optional[PushStatus]:
        """If should_deliver is False, optional terminal status (e.g. expired)."""
        ...


class ImmediateSingleNotificationPolicy:
    """v1: deliver every claimed job immediately."""

    def should_deliver(self, job: DeliveryJob) -> bool:
        return True

    def skip_status(self, job: DeliveryJob) -> Optional[PushStatus]:
        return None


# Future stubs (not wired):
#
# class TtlDeliveryPolicy:
#     def should_deliver(self, job: DeliveryJob) -> bool:
#         return job.expires_at is None or job.expires_at > datetime.utcnow()
#     def skip_status(self, job: DeliveryJob) -> Optional[PushStatus]:
#         return PushStatus.EXPIRED
