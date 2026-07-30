FROM python:3.10-slim

WORKDIR /app
COPY pyproject.toml readme.md ./
COPY app ./app

RUN pip install --no-cache-dir \
    "uvicorn>=0.34.0" \
    "fastapi>=0.115.0" \
    "pydantic[email]>=1.10.2,<2" \
    "python-dotenv>=0.10.1" \
    "loguru>=0.7.3" \
    "google-cloud-pubsub>=2.14.0" \
    "sqlalchemy>=2.0.0" \
    "psycopg2-binary>=2.9.0" \
    "requests>=2.32.3" \
    "exponent-server-sdk" \
    "opentelemetry-api>=1.29.0" \
    "opentelemetry-sdk>=1.29.0" \
    "opentelemetry-instrumentation-fastapi>=0.50b0"

ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
