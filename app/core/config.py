from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger


_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / ".env")
load_dotenv()


class Config:
    def __init__(self) -> None:
        self.ENV = os.getenv("ENV", "dev").strip().lower()
        self.SERVICE_NAME = os.getenv("SERVICE_NAME", "ai-agent-notification")
        self.APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
        self.APP_PORT = int(os.getenv("PORT", "8080"))
        self.PROJECT_ID = (
            os.getenv("PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCLOUD_PROJECT")
            or ""
        )
        self.PG_DB_URL = os.getenv("PG_DB_URL", "")
        self.SQLALCHEMY_ECHO = bool(int(os.getenv("SQLALCHEMY_ECHO", "0")))

        self.EXPO_PUSH_PUBSUB_TOPIC_NAME = os.getenv(
            "EXPO_PUSH_PUBSUB_TOPIC_NAME",
            "expo-push-notifications",
        )
        self.EXPO_PUSH_PUBSUB_SUBSCRIPTION = os.getenv(
            "EXPO_PUSH_PUBSUB_SUBSCRIPTION",
            "expo-push-notifications-sub",
        )
        self.EXPO_ACCESS_TOKEN_KEY = os.getenv("EXPO_ACCESS_TOKEN_KEY", "").strip()

        self.POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "60"))
        self.BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
        self.RECLAIM_AFTER_SECONDS = int(os.getenv("RECLAIM_AFTER_SECONDS", "900"))
        self.MAX_CONCURRENT_NOTIFICATIONS = int(os.getenv("MAX_CONCURRENT_NOTIFICATIONS", "5"))
        self.PUBSUB_MAX_MESSAGES = int(os.getenv("PUBSUB_MAX_MESSAGES", "5"))
        # When delivery_attempt reaches this, ack and stop retrying (no DLQ).
        self.PUBSUB_MAX_DELIVERY_ATTEMPTS = int(os.getenv("PUBSUB_MAX_DELIVERY_ATTEMPTS", "5"))
        # Extend ack deadline while processing (long Expo fan-outs).
        self.PUBSUB_ACK_EXTENSION_SECONDS = int(os.getenv("PUBSUB_ACK_EXTENSION_SECONDS", "600"))
        self.SUMMARY_LOG_INTERVAL_SECONDS = float(
            os.getenv("SUMMARY_LOG_INTERVAL_SECONDS", "300")
        )

        # Prefer NullPool on transaction pooler (:6543). Tiny QueuePool if forced.
        self.PG_USE_NULL_POOL = bool(int(os.getenv("PG_USE_NULL_POOL", "1")))
        self.PG_POOL_SIZE = int(os.getenv("PG_POOL_SIZE", "3"))
        self.PG_MAX_OVERFLOW = int(os.getenv("PG_MAX_OVERFLOW", "2"))
        self.PG_POOL_RECYCLE = int(os.getenv("PG_POOL_RECYCLE", "300"))

        self.LOG_LEVEL = logging.DEBUG if self.ENV == "dev" else logging.INFO


config = Config()
logger.info(
    "Worker config loaded env={} poll={}s concurrency={}",
    config.ENV,
    config.POLL_INTERVAL_SECONDS,
    config.MAX_CONCURRENT_NOTIFICATIONS,
)
