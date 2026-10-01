"""FastAPI application for The Misadventures of Havoc and Chaos."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import GAME_TITLE, get_settings
from .llm.services import LLMService, build_provider
from .persistence.db import Repository
from .realtime.hub import Hub
from .registry import GameRegistry
from .tts.service import TTSService, build_tts_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repo = Repository(settings.database_url)
    await repo.init()
    hub = Hub(settings.redis_url)
    await hub.start()
    llm = LLMService(build_provider(settings), settings)
    tts = TTSService(build_tts_provider(settings), settings)
    app.state.registry = GameRegistry(settings, llm, tts, hub, repo)
    logging.getLogger("havoc").info("%s ready (llm=%s, tts=%s)", GAME_TITLE, llm.name, tts.provider.name)
    yield
    await app.state.registry.shutdown()
    await llm.aclose()
    await tts.aclose()
    await hub.stop()
    await repo.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=GAME_TITLE, description=f"{GAME_TITLE} — a real-time, multiplayer, AI-narrated cooperative misadventure.",
                  version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)

    # Single-container deployments: serve the built frontend if present.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            target = dist / path
            if path and target.is_file() and dist in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
