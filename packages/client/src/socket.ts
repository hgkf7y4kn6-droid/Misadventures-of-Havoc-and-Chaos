import { CLOSE, type ClientMessage, type ServerMessage } from "@havoc/protocol";
import { HavocApiError, type HavocClient } from "./client";

export type SocketStatus = "connecting" | "open" | "closed" | "kicked" | "invalid";

export interface GameSocketHandlers {
  onMessage(msg: ServerMessage): void;
  onStatus?(status: SocketStatus): void;
}

/**
 * Reconnecting game socket. Every (re)connect fetches a fresh 60-second ticket, so long-lived
 * credentials never appear in URLs and an expired Clerk session is refreshed transparently.
 */
export class GameSocket {
  private ws: WebSocket | null = null;
  private retries = 0;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private client: HavocClient,
    private code: string,
    private handlers: GameSocketHandlers,
    private WebSocketImpl: typeof WebSocket = WebSocket,
  ) {}

  start() {
    this.stopped = false;
    void this.connect();
    return this;
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }

  send(msg: ClientMessage): boolean {
    if (!this.ws || this.ws.readyState !== 1) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  private status(s: SocketStatus) {
    this.handlers.onStatus?.(s);
  }

  private async connect() {
    if (this.stopped) return;
    this.status("connecting");
    let ticket: string;
    try {
      ticket = (await this.client.ticket(this.code)).ticket;
    } catch (e) {
      if (e instanceof HavocApiError && (e.status === 403 || e.status === 404)) {
        this.stopped = true;
        this.status("invalid");
        return;
      }
      return this.retry();
    }
    if (this.stopped) return;
    const ws = new this.WebSocketImpl(this.client.socketUrl(this.code, ticket));
    this.ws = ws;
    ws.onopen = () => { this.retries = 0; this.status("open"); };
    ws.onmessage = (e: MessageEvent) => {
      let msg: ServerMessage;
      try { msg = JSON.parse(String(e.data)); } catch { return; }
      if (msg.type === "kicked") { this.stopped = true; this.status("kicked"); }
      if (msg.type === "error" && msg.fatal) { this.stopped = true; this.status("invalid"); }
      this.handlers.onMessage(msg);
    };
    ws.onclose = (e: CloseEvent) => {
      if (this.ws !== ws) return;
      if (e.code === CLOSE.KICKED) { this.stopped = true; this.status("kicked"); return; }
      if (e.code === CLOSE.INVALID) { this.stopped = true; this.status("invalid"); return; }
      if (!this.stopped) this.retry();
    };
  }

  private retry() {
    this.status("closed");
    const delay = Math.min(10_000, 500 * 2 ** this.retries++);
    this.timer = setTimeout(() => void this.connect(), delay);
  }
}
