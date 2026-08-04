from __future__ import annotations

from typing import Any, Dict, Optional

from google.cloud import pubsub_v1
from loguru import logger


class PubSubPublish:
    def __init__(self, project_id: str, topic_name: str) -> None:
        self.topic_name = f"projects/{project_id}/topics/{topic_name}"
        self.publisher = pubsub_v1.PublisherClient()
        logger.info("Notification Pub/Sub publisher configured topic={}", self.topic_name)

    def publish_message(self, data: str, attr: Optional[Dict[str, Any]] = None) -> Any:
        try:
            if attr:
                return self.publisher.publish(
                    self.topic_name,
                    data.encode("utf-8"),
                    **attr,
                )
            return self.publisher.publish(self.topic_name, data.encode("utf-8"))
        except Exception as exc:
            logger.error("Error publishing message to {}: {}", self.topic_name, exc)
            raise


class PubSub:
    publisher: Optional[PubSubPublish] = None


notification_pubsub = PubSub()


def setup_pubsub_publisher(pubsub: PubSub, project_id: str, topic_name: str) -> None:
    if not project_id or not topic_name:
        logger.warning(
            "Notification Pub/Sub publisher not configured project_id={} topic_name={}",
            project_id or "(missing)",
            topic_name or "(missing)",
        )
        return
    pubsub.publisher = PubSubPublish(project_id, topic_name)
