FROM python:3.10.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install poetry
RUN poetry config virtualenvs.create false

COPY pyproject.toml .

RUN poetry install --no-interaction --no-ansi --only main --no-root

COPY . /

EXPOSE 8000
ENV ENV=prod

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0" ]

