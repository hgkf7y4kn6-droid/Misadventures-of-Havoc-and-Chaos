/** Signed calls from the edge to the Python engine (service binding / Container, or a URL). */
import { signHeaders } from "./signing";
import type { Env } from "./env";

export async function engine(env: Env, method: string, path: string, body?: unknown, query = ""): Promise<Response> {
  const bytes = body === undefined ? new Uint8Array() : new TextEncoder().encode(JSON.stringify(body));
  const headers = { "content-type": "application/json", ...(await signHeaders(env.EDGE_SECRET, method, path, bytes)) };
  const init: RequestInit = { method, headers, body: method === "GET" ? undefined : bytes };
  const url = `${env.ENGINE_URL.replace(/\/$/, "")}${path}${query}`;
  return env.ENGINE ? env.ENGINE.fetch(url, init) : fetch(url, init);
}

export async function engineJson<T>(env: Env, method: string, path: string, body?: unknown): Promise<{ status: number; data: T }> {
  const r = await engine(env, method, path, body);
  return { status: r.status, data: (await r.json().catch(() => ({}))) as T };
}
