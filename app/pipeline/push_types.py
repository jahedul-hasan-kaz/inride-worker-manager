from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from uuid import UUID

from app.domain.models import DeliveryJob, DeliveryTarget


@dataclass
class PreparedPush:
    job: DeliveryJob
    target: DeliveryTarget
    payload: dict


@dataclass
class PushSendResult:
    device_token_id: UUID
    status: str
    deactivate: bool = False
    error: Optional[str] = None


@dataclass
class BatchPushResponse:
    sent_count: int = 0
    failed_count: int = 0
    results: List[PushSendResult] = field(default_factory=list)

    @property
    def results_by_device(self) -> Dict[UUID, PushSendResult]:
        return {result.device_token_id: result for result in self.results}
