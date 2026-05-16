from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings

engine = create_async_engine(settings.postgres_auth_url, pool_pre_ping=True, pool_size=5)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
