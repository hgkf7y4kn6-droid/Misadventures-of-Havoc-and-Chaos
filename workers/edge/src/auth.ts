/**
 * Identity at the edge — same credential formats as backend/app/identity.py:
 *   Clerk session JWT · guest session `g.<b64url json>.<b64url hmac>` · socket ticket `t.…`
 */
import { verifyToken } from "@clerk/backend";
import type { Env } from "./env";

export interface Identity { userId: string; kind: "clerk" | "guest"; name?: string }

const enc = new TextEncoder();
const b64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64url = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4)), (c) => c.charCodeAt(0));

async function hmacKey(secret: string) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  return key;
}

export async function signToken(secret: string, prefix: "g" | "t", claims: Record<string, unknown>) {
  const payload = b64url(enc.encode(JSON.stringify(claims)));
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(`${prefix}.${payload}`)));
  return `${prefix}.${payload}.${b64url(sig)}`;
}

export async function readToken(secret: string, prefix: "g" | "t", token: string): Promise<Record<string, any>> {
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== prefix) throw new AuthError("wrong token type");
  const ok = await crypto.subtle.verify("HMAC", await hmacKey(secret), unb64url(parts[2]), enc.encode(`${prefix}.${parts[1]}`));
  if (!ok) throw new AuthError("bad signature");
  const claims = JSON.parse(new TextDecoder().decode(unb64url(parts[1])));
  if (claims.exp && claims.exp < Date.now() / 1000) throw new AuthError("token expired");
  return claims;
}

export class AuthError extends Error {}

export async function issueGuest(env: Env, name: string) {
  const exp = Math.floor(Date.now() / 1000) + 30 * 86400;
  const userId = `guest_${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
  const token = await signToken(env.SESSION_SECRET, "g", { sub: userId, name: name.slice(0, 32), exp });
  return { token, user_id: userId, kind: "guest" as const, expires_at: exp };
}

export async function issueTicket(env: Env, userId: string, code: string, playerId: string) {
  return signToken(env.SESSION_SECRET, "t", { sub: userId, code, pid: playerId, exp: Math.floor(Date.now() / 1000) + 60 });
}

export async function readTicket(env: Env, ticket: string, code: string) {
  const c = await readToken(env.SESSION_SECRET, "t", ticket);
  if (c.code !== code) throw new AuthError("ticket is for another game");
  return { userId: String(c.sub), playerId: String(c.pid) };
}

export async function identify(env: Env, request: Request): Promise<Identity> {
  const header = request.headers.get("authorization") ?? "";
  const token = header.toLowerCase().startsWith("bearer ") ? header.slice(7).trim() : "";
  if (!token) throw new AuthError("missing credentials");
  if (token.startsWith("g.")) {
    const c = await readToken(env.SESSION_SECRET, "g", token);
    return { userId: String(c.sub), kind: "guest", name: c.name };
  }
  if (!env.CLERK_JWT_KEY && !env.CLERK_SECRET_KEY) throw new AuthError("Clerk is not configured");
  try {
    const claims = await verifyToken(token, {
      jwtKey: env.CLERK_JWT_KEY?.replace(/\\n/g, "\n"), // networkless when the PEM key is pinned
      secretKey: env.CLERK_SECRET_KEY,
      authorizedParties: env.CLERK_AUTHORIZED_PARTIES?.split(",").map((s) => s.trim()).filter(Boolean),
    });
    return { userId: claims.sub, kind: "clerk" };
  } catch (e) {
    throw new AuthError(`invalid session: ${(e as Error).message}`);
  }
}
