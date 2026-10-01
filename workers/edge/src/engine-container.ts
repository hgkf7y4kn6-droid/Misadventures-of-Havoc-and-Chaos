/**
 * The Python engine running on Cloudflare Containers.
 *
 * Each instance is a Durable Object-managed container built from backend/Dockerfile. Game codes are
 * hashed onto ENGINE_SHARDS instances so every game has exactly one authoritative engine process.
 * Instances sleep when idle; game state lives in Postgres and phase timers live in GameRoom alarms,
 * so a woken instance rehydrates the game and carries on.
 */
import { Container } from "@cloudflare/containers";
import type { DurableObject } from "cloudflare:workers";
import type { Env } from "./env";

export function engineEnv(env: Env): Record<string, string> {
  if (!env.DATABASE_URL) {
    // Container disks are ephemeral: without Postgres a sleeping engine would forget live games.
    throw new Error("DATABASE_URL secret is required when the engine runs on Cloudflare Containers");
  }
  if (!env.PUBLIC_URL) throw new Error("PUBLIC_URL var is required (the engine posts socket messages + alarms back to it)");
  const vars: Record<string, string | undefined> = {
    HAVOC_EDGE_SECRET: env.EDGE_SECRET,
    HAVOC_EDGE_URL: env.PUBLIC_URL,
    HAVOC_DATABASE_URL: env.DATABASE_URL,
    HAVOC_SESSION_SECRET: env.SESSION_SECRET,
    HAVOC_LLM_PROVIDER: env.LLM_PROVIDER,
    HAVOC_LLM_MODEL: env.LLM_MODEL,
    HAVOC_LLM_API_KEY: env.LLM_API_KEY,
    HAVOC_TTS_PROVIDER: env.TTS_PROVIDER,
    HAVOC_TTS_API_KEY: env.TTS_API_KEY,
    HAVOC_POSTHOG_API_KEY: env.POSTHOG_API_KEY,
    HAVOC_POSTHOG_HOST: env.POSTHOG_HOST,
  };
  return Object.fromEntries(Object.entries(vars).filter((e): e is [string, string] => Boolean(e[1])));
}

export class EngineContainer extends Container<Env> {
  defaultPort = 8000;
  sleepAfter = "15m"; // long enough for final-story generation to finish after the last request
  enableInternet = true; // Postgres, LLM/TTS providers, PostHog, and callbacks to PUBLIC_URL

  constructor(ctx: DurableObject["ctx"], env: Env) {
    super(ctx, env);
    this.envVars = engineEnv(env);
  }

  override onError(error: unknown) {
    console.error("engine container error", error);
    throw error;
  }
}

/** FNV-1a: stable, fast, identical across Worker and DO isolates. */
export function engineShard(env: Env, key: string): string {
  const shards = Math.max(1, Number(env.ENGINE_SHARDS ?? 1));
  let h = 0x811c9dc5;
  for (const ch of key.toUpperCase()) h = Math.imul(h ^ ch.charCodeAt(0), 0x01000193) >>> 0;
  return `engine-${h % shards}`;
}
