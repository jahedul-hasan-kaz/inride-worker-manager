from __future__ import annotations

from typing import Optional, Protocol

from app.domain.models import DeliveryJob, PushStatus


class DeliveryPolicy(Protocol):
    """Pluggable gate before claim/send."""

    def should_deliver(self, job: DeliveryJob) -> bool:
        ...

    def skip_status(self, job: DeliveryJob) -> Optional[PushStatus]:
        """If should_deliver is False, optional terminal status (e.g. expired)."""
        ...


class ImmediateSingleNotificationPolicy:
    """Deliver all claimed jobs; TTL is payload-only, not a delivery gate."""

    def should_deliver(self, job: DeliveryJob) -> bool:
        return True

    def skip_status(self, job: DeliveryJob) -> Optional[PushStatus]:
        return None

    def skip_reason(self, job: DeliveryJob) -> str:
        return "skipped_by_policy"
