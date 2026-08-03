from __future__ import annotations

import sys

from loguru import logger

from app.core.env import (
    get_env,
    get_env_bool,
    get_env_float,
    get_env_int,
    get_project_id,
)
from app.core.secrets import get_secret


def configure_logging(level_name: str) -> None:
    """Apply log level to loguru (default stderr handler)."""
    level = level_name.upper()
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )


class Config:
    def __init__(self) -> None:
        self.ENV = get_env("ENV", "dev").lower()
        self.SERVICE_NAME = get_env("SERVICE_NAME", "ai-agent-notification")
        self.APP_HOST = get_env("APP_HOST", "0.0.0.0")
        self.APP_PORT = get_env_int("PORT", 8080)
        self.PROJECT_ID = get_project_id()
        self.PROJECT_NUMBER = get_env("PROJECT_NUMBER")
        self.PG_DB_URL = get_secret("PG_DB_URL")
        self.SQLALCHEMY_ECHO = get_env_bool("SQLALCHEMY_ECHO")

        self.EXPO_PUSH_PUBSUB_TOPIC_NAME = get_env(
            "EXPO_PUSH_PUBSUB_TOPIC_NAME",
            "expo-push-notifications",
        )
        self.EXPO_PUSH_PUBSUB_SUBSCRIPTION = get_env(
            "EXPO_PUSH_PUBSUB_SUBSCRIPTION",
            "expo-push-notifications-sub",
        )
        self.EXPO_ACCESS_TOKEN_KEY = get_secret("EXPO_ACCESS_TOKEN_KEY")

        self.POLL_INTERVAL_SECONDS = get_env_float("POLL_INTERVAL_SECONDS", 60)
        self.BATCH_SIZE = get_env_int("BATCH_SIZE", 50)
        self.RECLAIM_AFTER_SECONDS = get_env_int("RECLAIM_AFTER_SECONDS", 900)
        self.MAX_CONCURRENT_NOTIFICATIONS = get_env_int("MAX_CONCURRENT_NOTIFICATIONS", 5)
        self.PUBSUB_MAX_MESSAGES = get_env_int("PUBSUB_MAX_MESSAGES", 5)
        self.PUBSUB_MAX_DELIVERY_ATTEMPTS = get_env_int("PUBSUB_MAX_DELIVERY_ATTEMPTS", 5)
        self.PUBSUB_ACK_EXTENSION_SECONDS = get_env_int("PUBSUB_ACK_EXTENSION_SECONDS", 600)
        self.SUMMARY_LOG_INTERVAL_SECONDS = get_env_float("SUMMARY_LOG_INTERVAL_SECONDS", 300)

        self.PUSH_INGRESS_MODE = get_env("PUSH_INGRESS_MODE", "poll").lower()
        self.AGENT_MANAGEMENT_BASE_URL = get_secret("AGENT_MANAGEMENT_BASE_URL")
        self.AGENT_MANAGEMENT_SERVICE_TOKEN = get_secret("AGENT_MANAGEMENT_SERVICE_TOKEN")

        self.PG_USE_NULL_POOL = get_env_bool("PG_USE_NULL_POOL", True)
        self.PG_POOL_SIZE = get_env_int("PG_POOL_SIZE", 3)
        self.PG_MAX_OVERFLOW = get_env_int("PG_MAX_OVERFLOW", 2)
        self.PG_POOL_RECYCLE = get_env_int("PG_POOL_RECYCLE", 300)

        default_level = "DEBUG" if self.ENV == "dev" else "INFO"
        self.LOG_LEVEL = get_env("LOG_LEVEL", default_level).upper()


config = Config()
configure_logging(config.LOG_LEVEL)
logger.info(
    "Worker config loaded env={} poll={}s concurrency={}",
    config.ENV,
    config.POLL_INTERVAL_SECONDS,
    config.MAX_CONCURRENT_NOTIFICATIONS,
)
