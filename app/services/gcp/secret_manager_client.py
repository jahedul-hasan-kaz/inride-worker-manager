from __future__ import annotations

from typing import Optional

from loguru import logger

from app.core.env import get_env

_client = None
_cache: dict[str, str] = {}


def _get_client():
    global _client
    if _client is None:
        from google.cloud import secretmanager

        _client = secretmanager.SecretManagerServiceClient()
    return _client


def _project_number() -> str:
    return get_env("PROJECT_NUMBER")


def access_secret_version(secret_id: Optional[str], version_id: str = "latest") -> str:
    """Fetch a secret payload from GCP Secret Manager (cached per secret_id)."""
    if not secret_id:
        raise ValueError("No secret id provided")

    secret_id = secret_id.strip()
    if not secret_id:
        raise ValueError("No secret id provided")

    if secret_id in _cache:
        return _cache[secret_id]

    project_number = _project_number()
    if not project_number:
        raise ValueError(
            "PROJECT_NUMBER is required to resolve secrets from GCP Secret Manager"
        )

    name = f"projects/{project_number}/secrets/{secret_id}/versions/{version_id}"
    response = _get_client().access_secret_version(request={"name": name})
    value = response.payload.data.decode("UTF-8")
    _cache[secret_id] = value
    logger.debug("Loaded secret from GCP Secret Manager secret_id={}", secret_id)
    return value
