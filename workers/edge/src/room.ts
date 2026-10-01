/**
 * GameRoom — one Durable Object per game code.
 *
 * Owns everything that is per-connection or time-based at the edge:
 *   - player sockets (WebSocket Hibernation API; sockets are tagged with their player id),
 *   - the game's single pending timer (a DO alarm),
 *   - fan-out of the engine's per-player messages.
 * The Python engine stays authoritative over rules and state; this object never decides game outcomes.
 */
import { DurableObject } from "cloudflare:workers";
import { CLOSE } from "@havoc/protocol";
import { engine } from "./engine";
import type { Env } from "./env";

type Op =
  | { op: "send"; player_id: string; message: unknown }
  | { op: "kick"; player_id: string }
  | { op: "alarm"; at_ms: number; tag: string; token: string }
  | { op: "cancel_alarm" };

interface Attachment { playerId: string; window: number[] }
interface PendingAlarm { tag: string; token: string; at_ms: number }

export class GameRoom extends DurableObject<Env> {
  private async code(): Promise<string> {
    return (await this.ctx.storage.get<string>("code")) ?? "";
  }

  /** Socket upgrade. The Worker has already verified the ticket and passes the player id. */
  async fetch(request: Request): Promise<Response> {
    const code = request.headers.get("x-havoc-code")!;
    const playerId = request.headers.get("x-havoc-player")!;
    await this.ctx.storage.put("code", code);

    const pair = new WebSocketPair();
    const [client, server] = [pair[0], pair[1]];
    this.ctx.acceptWebSocket(server, [playerId]);
    server.serializeAttachment({ playerId, window: [] } satisfies Attachment);

    const r = await engine(this.env, "POST", `/internal/games/${code}/connect`, { player_id: playerId })
      .catch(() => new Response(null, { status: 503 }));
    if (r.ok) {
      const { state } = (await r.json()) as { state: unknown };
      server.send(JSON.stringify({ type: "state", state }));
    } else {
      server.send(JSON.stringify({ type: "error", message: "Could not reach the game.", fatal: true }));
      server.close(CLOSE.INVALID, "engine unavailable");
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, raw: string | ArrayBuffer) {
    const att = ws.deserializeAttachment() as Attachment;
    const now = Date.now();
    att.window = att.window.filter((t) => now - t < 10_000).concat(now);
    ws.serializeAttachment(att);
    if (att.window.length > Number(this.env.WS_MESSAGES_PER_10S ?? 40)) {
      ws.send(JSON.stringify({ type: "error", message: "Slow down! The narrator can only type so fast." }));
      return;
    }
    if (typeof raw !== "string" || raw.length > 4000) {
      ws.send(JSON.stringify({ type: "error", message: "Message too large." }));
      return;
    }
    let message: unknown;
    try { message = JSON.parse(raw); } catch {
      ws.send(JSON.stringify({ type: "error", message: "Invalid message." }));
      return;
    }
    if ((message as { action?: string }).action === "ping") return;
    // Identity is fixed by the socket's tag: a client can never act as another player.
    try {
      const r = await engine(this.env, "POST", `/internal/games/${await this.code()}/action`, { player_id: att.playerId, message });
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      if (!r.ok || body.error) ws.send(JSON.stringify({ type: "error", message: body.error ?? "The game is waking up — try that again." }));
    } catch {
      ws.send(JSON.stringify({ type: "error", message: "The game is waking up — try that again in a moment." }));
    }
  }

  async webSocketClose(ws: WebSocket) {
    await this.dropped(ws);
  }

  async webSocketError(ws: WebSocket) {
    await this.dropped(ws);
  }

  private async dropped(ws: WebSocket) {
    const { playerId } = ws.deserializeAttachment() as Attachment;
    const others = this.ctx.getWebSockets(playerId).filter((s) => s !== ws && s.readyState === WebSocket.OPEN);
    if (!others.length) {
      await engine(this.env, "POST", `/internal/games/${await this.code()}/disconnect`, { player_id: playerId }).catch(() => undefined);
    }
  }

  /** RPC from the Worker: an ordered batch of operations produced by the engine. */
  async applyOps(code: string, ops: Op[]) {
    await this.ctx.storage.put("code", code);
    for (const op of ops) {
      if (op.op === "send") {
        const text = JSON.stringify(op.message);
        for (const ws of this.ctx.getWebSockets(op.player_id)) {
          try { ws.send(text); } catch { /* closing socket */ }
        }
      } else if (op.op === "kick") {
        for (const ws of this.ctx.getWebSockets(op.player_id)) {
          try { ws.send(JSON.stringify({ type: "kicked" })); ws.close(CLOSE.KICKED, "removed by host"); } catch { /* gone */ }
        }
      } else if (op.op === "alarm") {
        await this.ctx.storage.put<PendingAlarm>("alarm", { tag: op.tag, token: op.token, at_ms: op.at_ms });
        await this.ctx.storage.setAlarm(op.at_ms);
      } else if (op.op === "cancel_alarm") {
        await this.ctx.storage.delete("alarm");
        await this.ctx.storage.deleteAlarm();
      }
    }
    return { applied: ops.length, sockets: this.ctx.getWebSockets().length };
  }

  /**
   * Phase timer fired. If the engine is unreachable (container waking, rolling out, or crashed) the room
   * re-arms itself with backoff instead of relying on platform retries; the engine ignores duplicate
   * and stale tokens, so retrying is always safe.
   */
  async alarm() {
    const pending = await this.ctx.storage.get<PendingAlarm & { attempts?: number }>("alarm");
    if (!pending) return;
    let ok = false;
    try {
      const r = await engine(this.env, "POST", `/internal/games/${await this.code()}/alarm`, { tag: pending.tag, token: pending.token });
      ok = r.status < 500;
    } catch (e) {
      console.warn("engine unreachable for alarm", (e as Error).message);
    }
    const current = await this.ctx.storage.get<PendingAlarm & { attempts?: number }>("alarm");
    if (current?.token !== pending.token) return; // the engine re-armed or cancelled meanwhile
    if (ok) {
      await this.ctx.storage.delete("alarm");
      return;
    }
    const attempts = (pending.attempts ?? 0) + 1;
    await this.ctx.storage.put("alarm", { ...pending, attempts });
    await this.ctx.storage.setAlarm(Date.now() + Math.min(30_000, 1000 * 2 ** Math.min(attempts, 5)));
  }
}
