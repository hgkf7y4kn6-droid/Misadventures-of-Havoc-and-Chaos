/**
 * The Misadventures of Havoc and Chaos — Cloudflare edge Worker.
 *
 * Serves the same public contract as the standalone engine (see @havoc/protocol ROUTES), so the web
 * and Expo clients don't know which one they talk to:
 *
 *   POST /api/session/guest            guest session (no account needed)
 *   GET  /api/session                  who am I (Clerk or guest)
 *   POST /api/games                    create  → engine (code chosen here; one GameRoom DO per code)
 *   GET  /api/games/:code              lobby info
 *   POST /api/games/:code/join         join / rejoin (same identity → same seat)
 *   POST /api/games/:code/ticket       60s socket ticket
 *   GET  /api/games/:code/state        my projection
 *   GET  /api/games/:code/story.txt    ?ticket=   (proxied to the engine)
 *   GET  /api/games/:code/audio/:n     ?ticket=   (proxied to the engine; cacheable per voice)
 *   GET  /ws/:code?ticket=             → GameRoom Durable Object
 *   POST /internal/rooms/:code/ops     engine → room (HMAC-signed)
 *   ANY  /ingest/*                     PostHog reverse proxy (first-party analytics domain)
 *   *                                  the web client (Workers Static Assets, SPA fallback)
 */
import { AuthError, identify, issueGuest, issueTicket, readTicket } from "./auth";
import { engine, engineJson } from "./engine";
import type { Env } from "./env";
import { verifySignature } from "./signing";

export { GameRoom } from "./room";

const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const json = (data: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", ...headers } });
const fail = (status: number, detail: string) => json({ detail }, status);

function newCode() {
  const bytes = crypto.getRandomValues(new Uint8Array(5));
  return [...bytes].map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
}

function cors(env: Env, request: Request): Record<string, string> {
  const origin = request.headers.get("origin");
  const allowed = (env.CORS_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!origin || !allowed.includes(origin)) return {};
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-headers": "authorization, content-type",
    "access-control-allow-methods": "GET, POST, PUT, OPTIONS",
    "access-control-max-age": "86400",
    vary: "origin",
  };
}

async function seatFor(env: Env, code: string, userId: string): Promise<string | null> {
  const r = await engine(env, "GET", `/internal/games/${code}/seat`, undefined, `?user_id=${encodeURIComponent(userId)}`);
  return r.ok ? ((await r.json()) as { player_id: string }).player_id : null;
}

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const parts = path.split("/").filter(Boolean);
  const method = request.method;

  // --- engine → room ---------------------------------------------------------------
  if (parts[0] === "internal" && parts[1] === "rooms" && parts[3] === "ops" && method === "POST") {
    const body = new Uint8Array(await request.arrayBuffer());
    if (!(await verifySignature(env.EDGE_SECRET, method, path, body, request.headers.get("x-havoc-ts"), request.headers.get("x-havoc-sig")))) {
      return fail(401, "bad signature");
    }
    const code = parts[2].toUpperCase();
    const { ops } = JSON.parse(new TextDecoder().decode(body));
    const result = await env.GAME_ROOM.get(env.GAME_ROOM.idFromName(code)).applyOps(code, ops);
    return json(result);
  }

  // --- analytics proxy ----------------------------------------------------------------
  if (parts[0] === "ingest") {
    const host = path.startsWith("/ingest/static/") ? (env.POSTHOG_ASSETS_HOST ?? "https://us-assets.i.posthog.com") : (env.POSTHOG_HOST ?? "https://us.i.posthog.com");
    const target = new URL(path.replace(/^\/ingest/, "") + url.search, host);
    const headers = new Headers(request.headers);
    headers.delete("cookie");
    return fetch(new Request(target, { method, headers, body: method === "GET" || method === "HEAD" ? undefined : request.body }));
  }

  // --- sockets --------------------------------------------------------------------------
  if (parts[0] === "ws" && parts[1]) {
    const code = parts[1].toUpperCase();
    if (request.headers.get("upgrade")?.toLowerCase() !== "websocket") return fail(426, "expected a websocket");
    let seat: { userId: string; playerId: string };
    try {
      seat = await readTicket(env, url.searchParams.get("ticket") ?? "", code);
    } catch (e) {
      // Accept, explain, close — browsers can't read HTTP errors on a failed upgrade.
      const pair = new WebSocketPair();
      pair[1].accept();
      pair[1].send(JSON.stringify({ type: "error", message: (e as Error).message, fatal: true }));
      pair[1].close(4004, "invalid ticket");
      return new Response(null, { status: 101, webSocket: pair[0] });
    }
    const headers = new Headers(request.headers);
    headers.set("x-havoc-code", code);
    headers.set("x-havoc-player", seat.playerId);
    return env.GAME_ROOM.get(env.GAME_ROOM.idFromName(code)).fetch(new Request(request.url, { headers }));
  }

  if (parts[0] !== "api") return env.ASSETS ? env.ASSETS.fetch(request) : fail(404, "not found");

  // --- public API -------------------------------------------------------------------------
  if (path === "/api/health") {
    const e = await engine(env, "GET", "/api/health").then((r) => r.json()).catch(() => null);
    return json({ status: e ? "ok" : "degraded", edge: "cloudflare", engine: e });
  }
  if (path === "/api/config") {
    const cfg = (await (await engine(env, "GET", "/api/config")).json()) as Record<string, unknown>;
    return json({ ...cfg, transport: "edge", auth: { guests: true, clerk: Boolean(env.CLERK_JWT_KEY || env.CLERK_SECRET_KEY) } });
  }
  if (parts[1] === "share") {
    const r = await engine(env, "GET", path);
    return new Response(r.body, { status: r.status, headers: r.headers });
  }
  if (path === "/api/session/guest" && method === "POST") {
    const { name } = (await request.json().catch(() => ({}))) as { name?: string };
    return json(await issueGuest(env, (name ?? "Guest").trim() || "Guest"));
  }

  const code = parts[1] === "games" && parts[2] ? parts[2].toUpperCase() : null;

  // Ticket-authenticated downloads (audio elements and links can't send headers).
  if (code && method === "GET" && (parts[3] === "story.txt" || parts[3] === "audio")) {
    const { playerId } = await readTicket(env, url.searchParams.get("ticket") ?? "", code);
    const sub = parts[3] === "audio" ? `audio/${Number(parts[4])}` : "story.txt";
    const q = parts[3] === "audio" ? `?voice=${encodeURIComponent(url.searchParams.get("voice") ?? "")}&speed=${Number(url.searchParams.get("speed") ?? 1)}` : "";
    const r = await engine(env, "GET", `/internal/games/${code}/players/${playerId}/${sub}`, undefined, q);
    return new Response(r.body, { status: r.status, headers: r.headers });
  }
  if (code && parts.length === 3 && method === "GET") {
    const { status, data } = await engineJson(env, "GET", `/internal/games/${code}`);
    return json(status === 200 ? { ...(data as object), title: "The Misadventures of Havoc and Chaos" } : data, status);
  }

  // Everything below needs an identity (Clerk session or guest session).
  const who = await identify(env, request);

  if (path === "/api/session" && method === "GET") return json({ user_id: who.userId, kind: who.kind, name: who.name ?? null });

  if (path === "/api/games" && method === "POST") {
    const { name, settings } = (await request.json()) as { name: string; settings?: Record<string, unknown> };
    for (let attempt = 0; attempt < 5; attempt++) {
      const { status, data } = await engineJson(env, "POST", "/internal/games", { code: newCode(), user_id: who.userId, name, settings });
      if (status !== 409) return json(data, status);
    }
    return fail(503, "Couldn't find a free game code. Try again.");
  }
  if (!code) return fail(404, "not found");

  if (parts[3] === "join" && method === "POST") {
    const { name } = (await request.json()) as { name: string };
    const { status, data } = await engineJson(env, "POST", `/internal/games/${code}/join`, { user_id: who.userId, name });
    return json(data, status);
  }

  const playerId = await seatFor(env, code, who.userId);
  if (!playerId) return fail(403, "You don't have a seat in this game.");

  if (parts[3] === "ticket" && method === "POST") {
    return json({ ticket: await issueTicket(env, who.userId, code, playerId), expires_in: 60, player_id: playerId });
  }
  if (parts[3] === "state" && method === "GET") {
    const { status, data } = await engineJson(env, "GET", `/internal/games/${code}/players/${playerId}/state`);
    return json(data, status);
  }
  return fail(404, "not found");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const corsHeaders = cors(env, request);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders });
    let response: Response;
    try {
      response = await route(request, env);
    } catch (e) {
      response = e instanceof AuthError ? fail(401, e.message) : fail(500, "The chaos was not intentional.");
      if (!(e instanceof AuthError)) console.error(e);
    }
    if (response.status === 101 || !Object.keys(corsHeaders).length) return response;
    const r = new Response(response.body, response);
    for (const [k, v] of Object.entries(corsHeaders)) r.headers.set(k, v);
    return r;
  },
} satisfies ExportedHandler<Env>;
