import {
  ROUTES, type ClientMessage, type GuestSession, type LobbyInfo, type Seat, type ServerConfig,
  type SharedStory, type Ticket,
} from "@havoc/protocol";

/** Async key/value storage: localStorage on web, expo-secure-store / AsyncStorage on native. */
export interface KeyValueStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
}

export class HavocApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export interface HavocClientOptions {
  /** "" for same-origin web; the Worker URL (e.g. https://play.example.com) for native. */
  baseUrl: string;
  storage: KeyValueStore;
  /** Returns a Clerk session JWT when the user is signed in, else null → guest session is used. */
  getAuthToken?: () => Promise<string | null>;
  fetchImpl?: typeof fetch;
}

const GUEST_KEY = "havoc.guest";
const SEATS_KEY = "havoc.seats";

/**
 * Identity → seat → ticket → socket. Identical against the standalone engine and the Cloudflare edge.
 * Signed-in users are identified by Clerk; everyone else gets a durable guest session, so party guests
 * never need an account and can rejoin from the same device.
 */
export class HavocClient {
  private opts: HavocClientOptions;

  constructor(opts: HavocClientOptions) {
    this.opts = opts;
  }

  setAuthTokenGetter(getter: (() => Promise<string | null>) | undefined) {
    this.opts = { ...this.opts, getAuthToken: getter };
  }

  get baseUrl() {
    return this.opts.baseUrl.replace(/\/$/, "");
  }

  private async request<T>(path: string, init: RequestInit = {}, auth = false): Promise<T> {
    const headers: Record<string, string> = { "Content-Type": "application/json", ...(init.headers as Record<string, string>) };
    if (auth) headers.Authorization = `Bearer ${await this.credential()}`;
    const r = await (this.opts.fetchImpl ?? fetch)(this.baseUrl + path, { ...init, headers });
    if (!r.ok) {
      let detail: unknown = r.statusText;
      try { detail = ((await r.json()) as { detail?: unknown }).detail ?? detail; } catch { /* not json */ }
      throw new HavocApiError(typeof detail === "string" ? detail : "Request failed", r.status);
    }
    return (await r.json()) as T;
  }

  // -- identity ------------------------------------------------------------------

  /** Clerk JWT if signed in, else a (cached) guest session token. */
  async credential(nameHint = "Guest"): Promise<string> {
    const clerk = await this.opts.getAuthToken?.();
    if (clerk) return clerk;
    const raw = await this.opts.storage.get(GUEST_KEY);
    if (raw) {
      const g = JSON.parse(raw) as GuestSession;
      if (g.expires_at * 1000 > Date.now() + 60_000) return g.token;
    }
    const g = await this.request<GuestSession>(ROUTES.guest, { method: "POST", body: JSON.stringify({ name: nameHint.slice(0, 32) || "Guest" }) });
    await this.opts.storage.set(GUEST_KEY, JSON.stringify(g));
    return g.token;
  }

  async signedIn(): Promise<boolean> {
    return Boolean(await this.opts.getAuthToken?.());
  }

  // -- seats ---------------------------------------------------------------------

  async seats(): Promise<Record<string, string>> {
    try { return JSON.parse((await this.opts.storage.get(SEATS_KEY)) || "{}"); } catch { return {}; }
  }

  async hasSeat(code: string): Promise<boolean> {
    return Boolean((await this.seats())[code.toUpperCase()]);
  }

  async forgetSeat(code: string) {
    const all = await this.seats();
    delete all[code.toUpperCase()];
    await this.opts.storage.set(SEATS_KEY, JSON.stringify(all));
  }

  private async rememberSeat(seat: Seat) {
    const all = await this.seats();
    all[seat.code.toUpperCase()] = seat.player_id;
    await this.opts.storage.set(SEATS_KEY, JSON.stringify(all));
    return seat;
  }

  // -- API -----------------------------------------------------------------------

  config() { return this.request<ServerConfig>(ROUTES.config); }
  lobby(code: string) { return this.request<LobbyInfo>(ROUTES.game(code)); }
  shared(id: string) { return this.request<SharedStory>(ROUTES.share(id)); }

  async createGame(name: string, settings?: Record<string, unknown>): Promise<Seat> {
    await this.credential(name);
    return this.rememberSeat(await this.request<Seat>(ROUTES.games, { method: "POST", body: JSON.stringify({ name, settings }) }, true));
  }

  async joinGame(code: string, name: string): Promise<Seat> {
    await this.credential(name);
    return this.rememberSeat(await this.request<Seat>(ROUTES.join(code.toUpperCase()), { method: "POST", body: JSON.stringify({ name }) }, true));
  }

  /** 60-second credential for one game — used for the socket and for audio/story URLs. */
  ticket(code: string) {
    return this.request<Ticket>(ROUTES.ticket(code.toUpperCase()), { method: "POST" }, true);
  }

  socketUrl(code: string, ticket: string) {
    const base = this.baseUrl || (typeof location !== "undefined" ? location.origin : "");
    return `${base.replace(/^http/, "ws")}${ROUTES.socket(code.toUpperCase())}?ticket=${encodeURIComponent(ticket)}`;
  }

  async audioUrl(code: string, chapter: number, voice: string) {
    const { ticket } = await this.ticket(code);
    return `${this.baseUrl}${ROUTES.audio(code, chapter)}?ticket=${encodeURIComponent(ticket)}&voice=${encodeURIComponent(voice)}`;
  }

  async storyTxtUrl(code: string) {
    const { ticket } = await this.ticket(code);
    return `${this.baseUrl}${ROUTES.storyTxt(code)}?ticket=${encodeURIComponent(ticket)}`;
  }
}

export type { ClientMessage };
