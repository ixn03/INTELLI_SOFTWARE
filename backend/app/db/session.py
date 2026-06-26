"""Database engine and session management."""

from __future__ import annotations

import os
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "sqlite:///./intelli_registry.db"


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def get_database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


@lru_cache(maxsize=1)
def get_engine():
    url = get_database_url()
    if url.startswith("sqlite"):
        if url in ("sqlite://", "sqlite:///:memory:"):
            # Shared in-memory DB for tests and local dev (single process).
            return create_engine(
                "sqlite://",
                future=True,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        return create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
        )
    return create_engine(url, future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    # Import models so metadata is populated before create_all.
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())


def reset_engine_cache() -> None:
    """Clear cached engine/session (for tests)."""
    try:
        get_engine().dispose()
    except Exception:
        pass
    get_database_url.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
