"""FastAPI application for The Misadventures of Havoc and Chaos."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analytics import build_analytics
from .api.routes import play_router, router
from .config import GAME_TITLE, get_settings
from .identity import IdentityService
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
    analytics = build_analytics(settings)
    edge = None
    if settings.edge_secret and settings.edge_url:
        # Edge mode: a Cloudflare Durable Object owns sockets + timers for each game.
        from .edge.link import EdgeLink

        edge = EdgeLink(settings.edge_url, settings.edge_secret)
    registry = GameRegistry(settings, llm, tts, hub, repo, publisher=edge, scheduler=edge, analytics=analytics)
    registry.identity = IdentityService(settings)
    app.state.registry = registry
    logging.getLogger("havoc").info("%s ready (llm=%s, tts=%s, transport=%s)", GAME_TITLE, llm.name, tts.provider.name,
                                    "edge" if edge else "direct")
    yield
    await app.state.registry.shutdown()
    if edge:
        await edge.aclose()
    if hasattr(analytics, "aclose"):
        await analytics.aclose()
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
    if settings.edge_secret:
        from .edge.internal_api import router as internal_router

        app.include_router(internal_router)
    if not settings.edge_url:
        # In edge mode clients talk to the Worker; the engine only serves the signed internal API.
        app.include_router(play_router)

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
