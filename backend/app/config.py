"""Runtime configuration for The Misadventures of Havoc and Chaos.

All values can be overridden with environment variables prefixed ``HAVOC_``
(see ``.env.example`` at the repository root).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

GAME_TITLE = "The Misadventures of Havoc and Chaos"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HAVOC_", env_file=".env", extra="ignore")

    app_name: str = GAME_TITLE
    environment: str = "development"

    # Persistence / realtime
    database_url: str = "sqlite+aiosqlite:///./havoc.db"
    redis_url: str | None = None  # e.g. redis://localhost:6379/0 ; None => in-memory bus

    # CORS for the Vite dev server
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    public_base_url: str = "http://localhost:5173"

    # LLM provider: "offline" (procedural, no network), "anthropic", "openai"
    llm_provider: str = "offline"
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None  # OpenAI-compatible endpoints
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 1

    # Text-to-speech: "browser" (client speechSynthesis), "openai", "elevenlabs", "google", "polly"
    tts_provider: str = "browser"
    tts_api_key: str | None = None
    tts_cache_dir: str = "./audio_cache"
    tts_max_chars_per_request: int = 3800

    # Game defaults (hosts can change most per lobby)
    min_players: int = 2
    max_players_limit: int = 12
    default_max_players: int = 8
    theme_submission_seconds: int = 90
    theme_voting_seconds: int = 60
    character_creation_seconds: int = 180
    decision_seconds: int = 120
    objective_reveal_seconds: int = 12
    resolution_pause_seconds: int = 8

    # Final story length targets (words) per adventure length
    story_words_short: tuple[int, int] = (1000, 2000)
    story_words_medium: tuple[int, int] = (2000, 4000)
    story_words_long: tuple[int, int] = (4000, 7000)

    # Rate limiting for websocket messages (per connection)
    ws_messages_per_10s: int = 40

    # Identity: guest sessions + socket tickets are HMAC-signed with this secret (pin it in production)
    session_secret: str | None = None
    guest_session_days: int = 30
    # Clerk (optional). Either a JWKS URL (https://<your-clerk-domain>/.well-known/jwks.json) or the
    # PEM public key from the Clerk dashboard for networkless verification.
    clerk_jwks_url: str | None = None
    clerk_jwt_key: str | None = None
    clerk_issuer: str | None = None
    clerk_authorized_parties: list[str] = Field(default_factory=list)

    # PostHog (optional) — server-side product analytics; never receives player-written text
    posthog_api_key: str | None = None
    posthog_host: str = "https://us.i.posthog.com"

    # Cloudflare edge mode: the engine runs behind a Durable Object per game.
    # edge_secret signs engine<->edge traffic; edge_url is where the engine pushes socket messages + alarms.
    edge_secret: str | None = None
    edge_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
