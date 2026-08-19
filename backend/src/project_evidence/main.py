from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from .api import router
from .config import Settings, get_settings
from .database import Database
from .llm import FakeLLMProvider, LLMProvider
from .hosted_llm import build_provider
from .workflow import Workflow
from .embeddings import build_embedding_provider


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings)
        await database.setup()
        checkpoint_context = AsyncPostgresSaver.from_conn_string(settings.checkpoint_database_url)
        checkpointer = await checkpoint_context.__aenter__()
        await checkpointer.setup()
        app.state.database = database
        app.state.provider = provider or build_provider(settings)
        app.state.embedding_provider = build_embedding_provider(settings)
        app.state.workflow = Workflow(database, app.state.provider, settings, checkpointer, app.state.embedding_provider)
        yield
        await checkpoint_context.__aexit__(None, None, None)
        await database.close()

    app = FastAPI(title="Project Evidence Agent", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.get("/health")
    async def health():
        active_provider = app.state.provider
        return {"status": "ok", "provider": getattr(active_provider, "name", "fake"), "model": getattr(active_provider, "model", None)}

    return app


app = create_app()
