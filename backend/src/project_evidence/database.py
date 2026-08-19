from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings
from .models import Base


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def setup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE reports ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0"))
            await conn.execute(text("ALTER TABLE corpora ADD COLUMN IF NOT EXISTS ruleset JSONB NOT NULL DEFAULT '{\"name\": \"Default project rules\", \"rules\": []}'::jsonb"))
            await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS classification VARCHAR(40)"))
            await conn.execute(text("ALTER TABLE claims ADD COLUMN IF NOT EXISTS embedding vector(64)"))
            await conn.execute(text("ALTER TABLE claims ADD COLUMN IF NOT EXISTS canonical_entity_id VARCHAR(200)"))
            await conn.execute(text("ALTER TABLE claims ADD COLUMN IF NOT EXISTS relationship VARCHAR(40)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_claims_canonical_entity_id ON claims (canonical_entity_id)"))

    @asynccontextmanager
    async def session(self):
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncSession:
    async with request.app.state.database.sessions() as session:
        yield session
