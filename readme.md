# About

## Requirements

- **Python** `>=3.10` (`^3.10` in `pyproject.toml`; Docker uses `3.10-slim`)
- [Poetry](https://python-poetry.org/docs/#installation) for local dependency management
- Postgres (notifications DB) and GCP Pub/Sub topic + subscription

## Setup

1. Run SQL (see ai-agent-management migration scripts).
2. Copy `dev.env.example` → `dev.env` and fill in local values (plaintext secrets).
3. Prod/deploy uses committed `.env` (secret **names** + tunables); `get_secret()` fetches values from GCP Secret Manager.

### Install packages

```bash
poetry install
```

Dev dependencies (pytest) are included. To install only runtime deps:

```bash
poetry install --only main
```

### Run

```bash
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Or with a Poetry shell:

```bash
poetry shell
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Port defaults to `8080` (`PORT` in `dev.env`). Health: `http://localhost:8080/health`.


## Notification Architecture
Polls / consumes pending notification delivery work and sends Expo pushes.

- **Ingress:** GCP Pub/Sub (primary) + DB poll fallback (`SKIP LOCKED`)
- **Claim:** `notifications.push_status` pending → processing (short TX)
- **Idempotency:** `notification_push_deliveries` unique `(notification_id, device_token_id)`
- **Send:** Expo HTTP outside DB transactions
- **Finalize:** `sent` if ≥1 device succeeds; else `failed`
- **Extensibility:** `DeliveryJob` + `DeliveryPolicy` for future TTL / aggregation


## Environment files (ai-agent pattern)

| File | Git | Purpose |
| --- | --- | --- |
| `dev.env` | ignored | Local dev: plaintext secrets (`PG_DB_URL`, `EXPO_ACCESS_TOKEN_KEY`) and overrides |
| `dev.env.example` | committed | Template for `dev.env` |
| `.env` | committed | Prod/deploy: GCP secret **ids** (`*_NAME`) and non-secret tunables |

`app/core/env.py` loads `dev.env` when `ENV=dev` (default), else `.env`.

Sensitive values are resolved via `get_secret()` in [`app/core/secrets.py`](app/core/secrets.py). All settings are exposed on `config` in [`app/core/config.py`](app/core/config.py). Application code should read from `config`, not `os.getenv` directly.

`get_secret()` resolution:

1. Plaintext env var if set (from `dev.env`, or Cloud Run injected value).
2. Else GCP Secret Manager via `{KEY}_NAME` from `.env` (e.g. `PG_DB_URL_NAME=pg-db-url`).
3. Requires `PROJECT_NUMBER` when using the `*_NAME` path.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENV` | `dev` | Runtime environment. `dev` uses debug logging and relaxes readiness (Pub/Sub not required). Non-dev requires Pub/Sub config for `/ready`. |
| `PORT` | `8080` | HTTP port for the FastAPI health/ready server. |
| `PROJECT_ID` | — | GCP project ID used to resolve the Pub/Sub subscription path (`projects/{PROJECT_ID}/subscriptions/...`). |
| `PROJECT_NUMBER` | — | GCP project number; required when resolving secrets via `*_NAME` from Secret Manager. |
| `PG_DB_URL` | — | Postgres connection string (notifications DB). Required for the worker and integration tests. Prefer the transaction pooler (`:6543`) with NullPool. |
| `PG_DB_URL_NAME` | — | GCP Secret Manager secret id for `PG_DB_URL` (prod alternative to plaintext). |
| `EXPO_PUSH_PUBSUB_TOPIC_NAME` | `expo-push-notifications` | Pub/Sub topic the outbox relay publishes to (management side). Documented here so topic/subscription stay aligned. |
| `EXPO_PUSH_PUBSUB_SUBSCRIPTION` | `expo-push-notifications-sub` | Pub/Sub subscription this worker pulls from (primary ingress). |
| `EXPO_ACCESS_TOKEN_KEY` | — | Expo push access token for authenticated Expo HTTP API sends. |
| `EXPO_ACCESS_TOKEN_KEY_NAME` | — | GCP Secret Manager secret id for the Expo token (prod alternative to plaintext). |
| `POLL_INTERVAL_SECONDS` | `60` | How often the DB fallback poller wakes to claim pending notifications (`SKIP LOCKED`) when Pub/Sub is quiet or messages were dropped. |
| `BATCH_SIZE` | `50` | Max pending notification rows claimed per DB poll cycle. |
| `RECLAIM_AFTER_SECONDS` | `900` | Age after which a stuck `processing` notification is reclaimed (worker crash / lease expiry recovery). Default 15 minutes. |
| `MAX_CONCURRENT_NOTIFICATIONS` | `5` | Semaphore limit: how many notifications may be processed (Expo fan-out) in parallel. |
| `PUBSUB_MAX_MESSAGES` | `5` | Pub/Sub flow-control: max outstanding unacked messages pulled at once. |
| `PUBSUB_MAX_DELIVERY_ATTEMPTS` | `5` | When `message.delivery_attempt` reaches this, ack and drop (no infinite nack / no DLQ). DB poller can still recover the row. |
| `PUBSUB_ACK_EXTENSION_SECONDS` | `600` | Extends the Pub/Sub ack deadline while a long Expo fan-out runs so the message is not redelivered mid-send. |
| `SUMMARY_LOG_INTERVAL_SECONDS` | `300` | How often the worker emits a summary metrics log line. Default 5 minutes. |

Optional (not in `dev.env.example`, have code defaults):

| Variable | Default | Purpose |
| --- | --- | --- |
| `SERVICE_NAME` | `ai-agent-notification` | Service name for logs/metrics. |
| `APP_HOST` | `0.0.0.0` | Bind address for the HTTP server. |
| `SQLALCHEMY_ECHO` | `0` | Set `1` to log SQL statements. |
| `PG_USE_NULL_POOL` | `1` | Use NullPool (recommended with Supabase transaction pooler). |
| `PG_POOL_SIZE` / `PG_MAX_OVERFLOW` / `PG_POOL_RECYCLE` | `3` / `2` / `300` | Used only when NullPool is disabled. |

## Health

- `GET /health` — liveness
- `GET /ready` — DB `SELECT 1` (+ Pub/Sub config required when `ENV != dev`)

## Pub/Sub retry bounds (no DLQ)

- `PUBSUB_MAX_DELIVERY_ATTEMPTS` — default `5`; when `message.delivery_attempt` reaches this, **ack and drop** (log + metric)
- Poison payloads are acked immediately (no infinite nack)
- `PUBSUB_ACK_EXTENSION_SECONDS` — default `600` (extends lease during Expo fan-out)
- Pending rows are still recovered by the **DB poller** even if a Pub/Sub message is given up

**Idempotency:** reclaim never deletes `sending` delivery rows (avoids duplicate Expo after crash).
Stale `sending` is sealed to `sent` when the parent notification is reclaimed.

## Integration tests

```bash
export INTEGRATION_TEST=1
export PG_DB_URL='postgresql://...'
pytest tests/test_postgres_integration.py -q
```
