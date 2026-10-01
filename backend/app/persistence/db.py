"""SQLAlchemy models and repository. PostgreSQL in production, SQLite for local dev/tests."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models.game import GameState, Phase


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class GameRow(Base):
    """Live game snapshots (the full authoritative state, server-side only)."""

    __tablename__ = "games"
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(32), index=True)
    phase: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CompletedAdventureRow(Base):
    __tablename__ = "completed_adventures"
    game_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    share_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(8))
    theme: Mapped[str] = mapped_column(Text, default="")
    objective: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    players: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    final_outcome: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    adventure_chronicle: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    final_story: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    audio_assets: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    achievements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    allow_share: Mapped[bool] = mapped_column(Boolean, default=True)
    completed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Repository:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url, future=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    # -- live games --------------------------------------------------------

    async def save(self, state: GameState) -> None:
        async with self.sessions() as s, s.begin():
            row = await s.get(GameRow, state.code)
            payload = state.model_dump_json()
            if row is None:
                s.add(GameRow(code=state.code, game_id=state.game_id, phase=state.phase.value, version=state.version, state_json=payload))
            else:
                row.game_id, row.phase, row.version, row.state_json = state.game_id, state.phase.value, state.version, payload

    async def load(self, code: str) -> GameState | None:
        async with self.sessions() as s:
            row = await s.get(GameRow, code)
            return GameState.model_validate_json(row.state_json) if row else None

    async def active_codes(self) -> list[str]:
        async with self.sessions() as s:
            rows = await s.execute(select(GameRow.code).where(GameRow.phase != Phase.ENDED.value))
            return [r[0] for r in rows]

    # -- archive -------------------------------------------------------------

    async def archive(self, state: GameState) -> None:
        if not state.final_story or not state.share_id:
            return
        names = {p.id: p.display for p in state.players.values()}
        async with self.sessions() as s, s.begin():
            row = await s.get(CompletedAdventureRow, state.game_id) or CompletedAdventureRow(game_id=state.game_id)
            row.share_id = state.share_id
            row.code = state.code
            row.theme = state.theme or ""
            row.objective = state.objective.model_dump() if state.objective else {}
            row.players = [{"name": p.display, "player": p.name, "archetype": p.character.archetype, "avatar": p.character.avatar,
                            "color": p.character.color} for p in state.active_players()]
            row.final_outcome = {
                "kind": state.outcome.kind, "headline": state.outcome.headline, "group_summary": state.outcome.group_summary,
                "personal": {names.get(k, k): v for k, v in state.outcome.personal.items()},
            } if state.outcome else {}
            # the archived chronicle drops never_reveal events entirely
            row.adventure_chronicle = [e.model_dump() for e in state.adventure_chronicle if e.visibility != "never_reveal"]
            row.final_story = state.final_story.model_dump()
            row.chapters = [c.model_dump() for c in state.final_story.chapters]
            row.audio_assets = state.audio_status
            row.achievements = {names.get(k, k): [a.model_dump() for a in v] for k, v in (state.outcome.achievements if state.outcome else {}).items()}
            row.allow_share = state.settings.allow_public_share
            row.completed_at = utcnow()
            s.add(row)

    async def get_by_share(self, share_id: str) -> CompletedAdventureRow | None:
        async with self.sessions() as s:
            rows = await s.execute(select(CompletedAdventureRow).where(CompletedAdventureRow.share_id == share_id))
            return rows.scalar_one_or_none()

    async def recent(self, limit: int = 20) -> list[CompletedAdventureRow]:
        async with self.sessions() as s:
            rows = await s.execute(select(CompletedAdventureRow).where(CompletedAdventureRow.allow_share.is_(True))
                                   .order_by(CompletedAdventureRow.completed_at.desc()).limit(limit))
            return list(rows.scalars())
