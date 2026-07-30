from __future__ import annotations

from typing import Any, Generator, Optional

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import config


def _is_transaction_pooler(url: str) -> bool:
    return ":6543" in (url or "")


class PostgresClient:
    _engine: Optional[Engine] = None
    _session_factory: Optional[sessionmaker] = None

    @classmethod
    def initialize(cls) -> None:
        if cls._engine is not None:
            return
        url = config.PG_DB_URL
        if not url:
            raise RuntimeError("PG_DB_URL is required")

        use_null = config.PG_USE_NULL_POOL or _is_transaction_pooler(url)
        if use_null:
            pool_kwargs: dict[str, Any] = {
                "poolclass": NullPool,
                "pool_pre_ping": True,
            }
            mode = "NullPool"
        else:
            pool_kwargs = {
                "poolclass": QueuePool,
                "pool_size": config.PG_POOL_SIZE,
                "max_overflow": config.PG_MAX_OVERFLOW,
                "pool_pre_ping": True,
                "pool_recycle": config.PG_POOL_RECYCLE,
                "pool_timeout": 30,
            }
            mode = f"QueuePool(size={config.PG_POOL_SIZE})"

        cls._engine = create_engine(
            url,
            echo=config.SQLALCHEMY_ECHO,
            future=True,
            **pool_kwargs,
        )
        cls._session_factory = sessionmaker(
            bind=cls._engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        logger.info("Postgres initialized mode={} transaction_pooler={}", mode, _is_transaction_pooler(url))

    @classmethod
    def session(cls) -> Session:
        if cls._session_factory is None:
            raise RuntimeError("PostgresClient not initialized")
        return cls._session_factory()

    @classmethod
    def get_db(cls) -> Generator[Session, None, None]:
        db = cls.session()
        try:
            yield db
        finally:
            db.close()

    @classmethod
    def close(cls) -> None:
        if cls._engine is not None:
            cls._engine.dispose()
            cls._engine = None
            cls._session_factory = None
