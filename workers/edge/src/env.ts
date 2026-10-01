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
