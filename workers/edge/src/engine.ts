/** Signed calls from the edge to the Python engine: a Cloudflare Container shard, a service binding, or a URL. */
import { getContainer } from "@cloudflare/containers";
import { engineShard } from "./engine-container";
import { signHeaders } from "./signing";
import type { Env } from "./env";

/** Game code a request belongs to, so it reaches the one engine instance that owns that game. */
function shardKey(path: string, body: unknown): string {
  const m = path.match(/^\/internal\/games\/([A-Za-z]{4,8})/);
  if (m) return m[1];
  const code = (body as { code?: unknown } | undefined)?.code;
  return typeof code === "string" ? code : ""; // non-game calls (health, config, share) → shard 0
}

export async function engine(env: Env, method: string, path: string, body?: unknown, query = ""): Promise<Response> {
  const bytes = body === undefined ? new Uint8Array() : new TextEncoder().encode(JSON.stringify(body));
  const headers = { "content-type": "application/json", ...(await signHeaders(env.EDGE_SECRET, method, path, bytes)) };
  const init: RequestInit = { method, headers, body: method === "GET" ? undefined : bytes };
  if (env.ENGINE_MODE === "container") {
    if (!env.ENGINE_CONTAINER) throw new Error("ENGINE_MODE=container but no ENGINE_CONTAINER binding");
    // Container.fetch starts the instance if needed and forwards to its defaultPort.
    const stub = getContainer(env.ENGINE_CONTAINER, engineShard(env, shardKey(path, body)));
    return stub.fetch(new Request(`http://engine${path}${query}`, init));
  }
  const url = `${env.ENGINE_URL.replace(/\/$/, "")}${path}${query}`;
  return env.ENGINE ? env.ENGINE.fetch(url, init) : fetch(url, init);
}

export async function engineJson<T>(env: Env, method: string, path: string, body?: unknown): Promise<{ status: number; data: T }> {
  const r = await engine(env, method, path, body);
  return { status: r.status, data: (await r.json().catch(() => ({}))) as T };
}
