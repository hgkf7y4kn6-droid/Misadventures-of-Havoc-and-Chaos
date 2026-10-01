import type { EngineContainer } from "./engine-container";
import type { GameRoom } from "./room";

export interface Env {
  GAME_ROOM: DurableObjectNamespace<GameRoom>;
  /** Built web client (Workers Static Assets). */
  ASSETS?: Fetcher;
  /**
   * Where the Python engine runs. Either a service binding / Cloudflare Container (ENGINE) or a URL.
   * Every call is HMAC-signed with EDGE_SECRET; the engine rejects anything unsigned.
   */
  ENGINE?: Fetcher;
  ENGINE_URL: string;
  /** "url" (local dev / external host) or "container" (Cloudflare Containers via ENGINE_CONTAINER). */
  ENGINE_MODE?: "url" | "container";
  ENGINE_CONTAINER?: DurableObjectNamespace<EngineContainer>;
  /** Number of engine instances game codes are hashed onto. Change only when no games are live. */
  ENGINE_SHARDS?: string;
  /** This Worker's public origin; the engine posts room ops back here. */
  PUBLIC_URL?: string;
  // Engine configuration forwarded into the container (secrets via `wrangler secret put`).
  DATABASE_URL?: string;
  LLM_PROVIDER?: string;
  LLM_MODEL?: string;
  LLM_API_KEY?: string;
  TTS_PROVIDER?: string;
  TTS_API_KEY?: string;
  POSTHOG_API_KEY?: string;
  EDGE_SECRET: string;
  /** Signs guest sessions and socket tickets (same token format as the standalone engine). */
  SESSION_SECRET: string;
  CLERK_SECRET_KEY?: string;
  CLERK_JWT_KEY?: string;
  /** Comma-separated origins allowed as the Clerk `azp` claim (web origins + native app scheme). */
  CLERK_AUTHORIZED_PARTIES?: string;
  /** Comma-separated origins allowed to call /api cross-origin (Expo web, preview deploys). */
  CORS_ORIGINS?: string;
  POSTHOG_HOST?: string;
  POSTHOG_ASSETS_HOST?: string;
  WS_MESSAGES_PER_10S?: string;
}
