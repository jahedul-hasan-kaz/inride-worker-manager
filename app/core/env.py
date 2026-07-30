from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOADED_ENV_FILE = ""


def _resolve_env_name() -> str:
    return (os.getenv("ENV") or "dev").strip().lower()


def load_env_files() -> str:
    """Load dev.env for local dev, .env for prod/deploy (ai-agent pattern)."""
    global LOADED_ENV_FILE

    env_name = _resolve_env_name()
    if env_name == "dev":
        dotenv_path = ROOT_DIR / "dev.env"
    else:
        dotenv_path = ROOT_DIR / ".env"

    if dotenv_path.is_file():
        load_dotenv(dotenv_path)
        LOADED_ENV_FILE = str(dotenv_path)
        logger.info("Loaded env file {} (ENV={})", dotenv_path.name, env_name)
    else:
        LOADED_ENV_FILE = ""
        logger.warning(
            "Env file {} not found (ENV={}); using process environment only",
            dotenv_path.name,
            env_name,
        )

    return env_name


ACTIVE_ENV = load_env_files()


def get_env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value.strip()


def get_env_int(key: str, default: int) -> int:
    return int(get_env(key, str(default)))


def get_env_float(key: str, default: float) -> float:
    return float(get_env(key, str(default)))


def get_env_bool(key: str, default: bool = False) -> bool:
    raw = get_env(key, "1" if default else "0")
    return bool(int(raw))


def get_project_id() -> str:
    return (
        get_env("PROJECT_ID")
        or get_env("GOOGLE_CLOUD_PROJECT")
        or get_env("GCLOUD_PROJECT")
    )
