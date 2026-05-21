from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from config.settings import settings


engine: AsyncEngine = create_async_engine(
    settings.mysql_async_url,
    pool_pre_ping=True,
    pool_recycle=1800,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def close_engine() -> None:
    """Dispose the shared SQLAlchemy async engine on application shutdown."""
    await engine.dispose()
