"""REST + WebSocket API. Every request that touches a game authenticates with the player's secret token."""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ..config import GAME_TITLE
from ..engine.game_manager import GameError, GameManager
from ..engine.visibility import project
from ..models.events import ClientMessage, RealtimeEvent
from ..models.game import Phase, Player
from ..registry import GameRegistry
from ..tts.base import TTSError
from ..tts.voices import VOICE_DESCRIPTIONS, VoiceStyle
from ..tts.service import voice_for

log = logging.getLogger("havoc.api")
router = APIRouter()
_client_message = TypeAdapter(ClientMessage)


def registry(request: Request) -> GameRegistry:
    return request.app.state.registry


Reg = Annotated[GameRegistry, Depends(registry)]


class CreateGame(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    settings: dict[str, Any] | None = None


class JoinGame(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class Preferences(BaseModel):
    audio_enabled: bool | None = None
    narration_volume: float | None = Field(default=None, ge=0, le=1)
    preferred_voice: str | None = Field(default=None, max_length=32)


async def authed(code: str, reg: GameRegistry, token: str | None) -> tuple[GameManager, Player]:
    if not token:
        raise HTTPException(401, "Missing player token")
    try:
        mgr = await reg.get(code)
        return mgr, mgr.authenticate(token)
    except GameError as exc:
        raise HTTPException(404 if "code" in str(exc) else 403, str(exc)) from exc


def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@router.get("/api/health")
async def health(reg: Reg) -> dict[str, Any]:
    return {"status": "ok", "title": GAME_TITLE, "llm": reg.llm.name, "tts": reg.tts.provider.name, "games": len(reg.games)}


@router.get("/api/config")
async def config(reg: Reg) -> dict[str, Any]:
    s = reg.settings
    return {
        "title": GAME_TITLE, "min_players": s.min_players, "max_players_limit": s.max_players_limit,
        "default_max_players": s.default_max_players, "llm": reg.llm.name,
        "tts": {"provider": reg.tts.provider.name, "server_side": reg.tts.server_side},
        "voices": [{"id": v.value, "description": VOICE_DESCRIPTIONS[v]} for v in VoiceStyle],
        "adventure_lengths": {"short": s.story_words_short, "medium": s.story_words_medium, "long": s.story_words_long},
    }


@router.post("/api/games")
async def create_game(body: CreateGame, reg: Reg) -> dict[str, str]:
    try:
        mgr, pid, token = await reg.create(body.name, body.settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not create game: {exc}") from exc
    return {"code": mgr.state.code, "game_id": mgr.state.game_id, "player_id": pid, "token": token}


@router.get("/api/games/{code}")
async def lobby_info(code: str, reg: Reg) -> dict[str, Any]:
    try:
        mgr = await reg.get(code)
    except GameError as exc:
        raise HTTPException(404, str(exc)) from exc
    s = mgr.state
    return {"code": s.code, "phase": s.phase, "players": len(s.active_players()), "max_players": s.settings.max_players,
            "joinable": s.phase == Phase.LOBBY and len(s.active_players()) < s.settings.max_players, "title": GAME_TITLE}


@router.post("/api/games/{code}/join")
async def join_game(code: str, body: JoinGame, reg: Reg) -> dict[str, str]:
    try:
        mgr = await reg.get(code)
        p = await mgr.join(body.name)
    except GameError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"code": mgr.state.code, "game_id": mgr.state.game_id, "player_id": p.id, "token": p.token}


@router.get("/api/games/{code}/state")
async def get_state(code: str, reg: Reg, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    mgr, p = await authed(code, reg, bearer(authorization))
    return project(mgr.state, p.id)


@router.put("/api/games/{code}/preferences")
async def put_preferences(code: str, body: Preferences, reg: Reg, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    mgr, p = await authed(code, reg, bearer(authorization))
    from ..models.events import SetPreferences

    await mgr.set_preferences(p, SetPreferences(action="set_preferences", **body.model_dump()))
    return p.preferences.model_dump()


def story_text(title: str, chapters: list[dict], epilogues: dict[str, str] | None = None) -> str:
    parts = [title.upper(), "=" * min(80, len(title)), ""]
    for ch in chapters:
        parts += [ch["title"], "-" * min(80, len(ch["title"])), ch["text"], ""]
    return "\n".join(parts) + f"\n— Generated by {GAME_TITLE}\n"


@router.get("/api/games/{code}/story.txt", response_class=PlainTextResponse)
async def download_story(code: str, reg: Reg, token: str | None = Query(default=None),
                         authorization: Annotated[str | None, Header()] = None) -> Response:
    mgr, _ = await authed(code, reg, bearer(authorization) or token)
    story = mgr.state.final_story
    if not story or mgr.state.phase != Phase.ENDED:
        raise HTTPException(409, "The story isn't finished yet.")
    body = story_text(story.title, [c.model_dump() for c in story.chapters])
    filename = f"havoc-and-chaos-{mgr.state.code.lower()}.txt"
    return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/games/{code}/audio/{chapter}")
async def chapter_audio(code: str, chapter: int, reg: Reg, token: str | None = Query(default=None),
                        voice: str | None = Query(default=None), speed: float = Query(default=1.0, ge=0.5, le=2.0),
                        authorization: Annotated[str | None, Header()] = None) -> Response:
    """Narration for one chapter. Audio elements can't send headers, so ?token= is accepted too."""
    mgr, p = await authed(code, reg, bearer(authorization) or token)
    story = mgr.state.final_story
    if not story or mgr.state.phase != Phase.ENDED:
        raise HTTPException(409, "The story isn't finished yet.")
    if not reg.tts.server_side:
        return Response(status_code=204, headers={"X-Narration": "browser"})
    vc = voice_for(voice or p.preferences.preferred_voice or mgr.state.settings.narrator_voice, speed)
    try:
        path = await reg.tts.chapter_audio(story, chapter, vc)
    except IndexError as exc:
        raise HTTPException(404, "No such chapter") from exc
    except TTSError as exc:
        raise HTTPException(503, f"Narration unavailable: {exc}") from exc
    return FileResponse(path, media_type=reg.tts.provider.media_type, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/share/{share_id}")
async def shared_story(share_id: str, reg: Reg) -> dict[str, Any]:
    """Read-only public record. Contains no tokens, ids, raw chronicle, or never_reveal content."""
    row = await reg.repo.get_by_share(share_id)
    if not row or not row.allow_share:
        raise HTTPException(404, "This adventure isn't shared.")
    return {
        "title": row.final_story.get("title", GAME_TITLE), "theme": row.theme,
        "objective": {"title": row.objective.get("title"), "description": row.objective.get("description")},
        "players": row.players, "outcome": row.final_outcome, "chapters": row.chapters,
        "epilogues": row.final_story.get("epilogues", {}), "achievements": {
            name: [{"title": a["title"], "emoji": a.get("emoji"), "description": a["description"]} for a in achs]
            for name, achs in row.achievements.items()},
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


@router.get("/api/share/{share_id}/story.txt", response_class=PlainTextResponse)
async def shared_story_txt(share_id: str, reg: Reg) -> Response:
    row = await reg.repo.get_by_share(share_id)
    if not row or not row.allow_share:
        raise HTTPException(404, "This adventure isn't shared.")
    return PlainTextResponse(story_text(row.final_story.get("title", GAME_TITLE), row.chapters),
                             headers={"Content-Disposition": f'attachment; filename="havoc-and-chaos-{share_id}.txt"'})


@router.websocket("/ws/{code}")
async def websocket(ws: WebSocket, code: str, token: str = Query(...)) -> None:
    reg: GameRegistry = ws.app.state.registry
    try:
        mgr = await reg.get(code)
        player = mgr.authenticate(token)
    except GameError as exc:
        await ws.accept()
        await ws.send_json({"type": "error", "message": str(exc), "fatal": True})
        await ws.close(code=4004)
        return
    await ws.accept()
    code = mgr.state.code
    reg.hub.add(code, player.id, ws)
    await mgr.set_connected(player.id, True)
    if mgr.state.phase == Phase.LOBBY:
        await mgr.emit(RealtimeEvent.PLAYER_JOINED, {"player_id": player.id, "name": player.name, "reconnect": True})
    limit = reg.settings.ws_messages_per_10s
    window: list[float] = []
    try:
        while True:
            raw = await ws.receive_text()
            t = time.monotonic()
            window = [x for x in window if t - x < 10] + [t]
            if len(window) > limit:
                await ws.send_json({"type": "error", "message": "Slow down! The narrator can only type so fast."})
                continue
            if len(raw) > 4000:
                await ws.send_json({"type": "error", "message": "Message too large."})
                continue
            try:
                msg = _client_message.validate_python(json.loads(raw))
            except (ValidationError, ValueError) as exc:
                await ws.send_json({"type": "error", "message": "Invalid message.", "detail": str(exc)[:300]})
                continue
            # The socket's identity is fixed at connect time: clients can never act as anyone else.
            mgr = await reg.get(code)
            try:
                await mgr.handle(player.id, msg)
            except GameError as exc:
                log.info("rejected %s from %s: %s", type(msg).__name__, player.id, exc)
                await ws.send_json({"type": "error", "message": str(exc)})
            except Exception:  # noqa: BLE001
                log.exception("action failed")
                await ws.send_json({"type": "error", "message": "Something went wrong. The chaos was not intentional."})
    except WebSocketDisconnect:
        pass
    finally:
        if reg.hub.remove(code, player.id, ws):
            try:
                await (await reg.get(code)).set_connected(player.id, False)
            except GameError:
                pass
