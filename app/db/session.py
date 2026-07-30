from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app.db.postgres import PostgresClient


@contextmanager
def session_scope() -> Iterator[Session]:
    session = PostgresClient.session()
    try:
        yield session
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        session.close()
