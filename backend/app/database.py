"""Database connection (async SQLAlchemy + asyncpg).

Lazy initialization — the engine and session factory are created on first use,
not at import time. This prevents connections from being opened during testing
or CLI usage where the database may not be available.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

_engine = None
_session_factory = None


def get_engine():
    """Return the async engine, creating it lazily on first call."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory():
    """Return the async session factory, creating it lazily on first call."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False,
        )
    return _session_factory


# Module-level accessor for backward compatibility.
# Code can use `async_session()` as before; it now triggers lazy init.
def __getattr__(name):
    if name == "async_session":
        return get_session_factory()
    if name == "engine":
        return get_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with get_session_factory()() as session:
        try:
            yield session
        finally:
            await session.close()
