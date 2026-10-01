"""Signed internal API the Cloudflare ``GameRoom`` Durable Object calls.

The edge has already authenticated the user (Clerk or guest) and owns the
sockets; it tells the engine *which player* is acting. The engine trusts that
only because every request is HMAC-signed with ``HAVOC_EDGE_SECRET``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ..engine.game_manager import GameError
from ..engine.visibility import project
from ..models.events import ClientMessage
from ..models.game import Phase
from .signing import verify

_client_message = TypeAdapter(ClientMessage)


async def require_signature(request: Request) -> None:
    secret = request.app.state.registry.settings.edge_secret
    body = await request.body()
    if not secret or not verify(secret, request.method, request.url.path, body,
                                request.headers.get("x-havoc-ts"), request.headers.get("x-havoc-sig")):
        raise HTTPException(401, "bad signature")


router = APIRouter(prefix="/internal", dependencies=[Depends(require_signature)], include_in_schema=False)


class CreateBody(BaseModel):
    code: str = Field(pattern=r"^[A-Z]{4,8}$")
    user_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=32)
    settings: dict[str, Any] | None = None


class JoinBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=32)


class PlayerBody(BaseModel):
    player_id: str


class ActionBody(BaseModel):
    player_id: str
    message: dict[str, Any]


class AlarmBody(BaseModel):
    tag: str
    token: str


def reg(request: Request):
    return request.app.state.registry


async def manager(request: Request, code: str):
    try:
        return await reg(request).get(code)
    except GameError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/games")
async def create(body: CreateBody, request: Request) -> dict[str, Any]:
    try:
        mgr, pid, _ = await reg(request).create(body.name, body.settings, user_id=body.user_id, code=body.code)
    except GameError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"code": mgr.state.code, "game_id": mgr.state.game_id, "player_id": pid}


@router.get("/games/{code}")
async def info(code: str, request: Request) -> dict[str, Any]:
    s = (await manager(request, code)).state
    return {"code": s.code, "phase": s.phase, "players": len(s.active_players()), "max_players": s.settings.max_players,
            "joinable": s.phase == Phase.LOBBY and len(s.active_players()) < s.settings.max_players}


@router.post("/games/{code}/join")
async def join(code: str, body: JoinBody, request: Request) -> dict[str, Any]:
    mgr = await manager(request, code)
    try:
        p = await mgr.join(body.name, user_id=body.user_id)
    except GameError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"code": mgr.state.code, "game_id": mgr.state.game_id, "player_id": p.id}


@router.get("/games/{code}/seat")
async def seat(code: str, user_id: str, request: Request) -> dict[str, Any]:
    p = (await manager(request, code)).state.player_by_user(user_id)
    if not p or user_id in (await manager(request, code)).state.kicked_user_ids:
        raise HTTPException(404, "no seat")
    return {"player_id": p.id}


@router.post("/games/{code}/connect")
async def connect(code: str, body: PlayerBody, request: Request) -> dict[str, Any]:
    mgr = await manager(request, code)
    if body.player_id not in mgr.state.players:
        raise HTTPException(404, "no such player")
    await mgr.set_connected(body.player_id, True)
    return {"state": project(mgr.state, body.player_id)}


@router.post("/games/{code}/disconnect")
async def disconnect(code: str, body: PlayerBody, request: Request) -> dict[str, bool]:
    await (await manager(request, code)).set_connected(body.player_id, False)
    return {"ok": True}


@router.post("/games/{code}/action")
async def action(code: str, body: ActionBody, request: Request) -> dict[str, Any]:
    mgr = await manager(request, code)
    try:
        msg = _client_message.validate_python(body.message)
    except ValidationError as exc:
        return {"error": "Invalid message.", "detail": str(exc)[:300]}
    if body.player_id not in mgr.state.players:
        raise HTTPException(404, "no such player")

    async def run() -> None:
        try:
            await mgr.handle(body.player_id, msg)
        except GameError as exc:
            await mgr.publisher.to_player(mgr.state.code, body.player_id, {"type": "error", "message": str(exc)})

    # Acknowledge now; resolution may wait on the LLM. Results and errors arrive via /ops.
    mgr._spawn(run())
    return {"accepted": True}


@router.post("/games/{code}/alarm")
async def alarm(code: str, body: AlarmBody, request: Request) -> dict[str, bool]:
    mgr = await manager(request, code)
    t = mgr.state.timers
    current = body.token == t.armed_token and body.tag == t.armed_tag
    if current:
        mgr._spawn(mgr.fire_timer(body.tag, body.token))  # fire_timer re-checks the token: duplicates are no-ops
    return {"accepted": current}


@router.get("/games/{code}/players/{player_id}/story.txt")
async def story_txt(code: str, player_id: str, request: Request):
    from ..api.routes import story_download

    mgr = await manager(request, code)
    if player_id not in mgr.state.players:
        raise HTTPException(404, "no such player")
    return story_download(mgr)


@router.get("/games/{code}/players/{player_id}/audio/{chapter}")
async def audio(code: str, player_id: str, chapter: int, request: Request, voice: str | None = None, speed: float = 1.0):
    from ..api.routes import audio_response

    mgr = await manager(request, code)
    p = mgr.state.players.get(player_id)
    if not p:
        raise HTTPException(404, "no such player")
    return await audio_response(reg(request), mgr, p, chapter, voice, max(0.5, min(2.0, speed)))
