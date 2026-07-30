from __future__ import annotations

from typing import Optional

from app.core.env import get_env


def get_secret(
    key: str,
    *,
    name_key: Optional[str] = None,
    default: str = "",
) -> str:
    """Resolve a secret from local env or GCP Secret Manager.

    Resolution order:
    1. Non-empty env value (local .env or Cloud Run injected value).
    2. Secret Manager via ``name_key`` or default ``{key}_NAME`` env var.
    3. ``default``.
    """
    value = get_env(key)
    if value:
        return value

    secret_name = get_env(name_key or f"{key}_NAME")
    if not secret_name:
        return default

    from app.services.gcp.secret_manager_client import access_secret_version

    return access_secret_version(secret_name).strip() or default
