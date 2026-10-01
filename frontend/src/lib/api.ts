import type { ServerConfig } from "./types";

export interface Session { code: string; player_id: string; token: string; game_id: string }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return r.json() as Promise<T>;
}

export const api = {
  config: () => req<ServerConfig>("/api/config"),
  create: (name: string, settings?: Record<string, unknown>) =>
    req<Session>("/api/games", { method: "POST", body: JSON.stringify({ name, settings }) }),
  lobby: (code: string) => req<{ code: string; phase: string; players: number; max_players: number; joinable: boolean }>(`/api/games/${encodeURIComponent(code)}`),
  join: (code: string, name: string) =>
    req<Session>(`/api/games/${encodeURIComponent(code)}/join`, { method: "POST", body: JSON.stringify({ name }) }),
  shared: (shareId: string) => req<SharedStory>(`/api/share/${encodeURIComponent(shareId)}`),
  audioUrl: (code: string, token: string, chapter: number, voice: string) =>
    `/api/games/${encodeURIComponent(code)}/audio/${chapter}?token=${encodeURIComponent(token)}&voice=${encodeURIComponent(voice)}`,
  storyTxtUrl: (code: string, token: string) => `/api/games/${encodeURIComponent(code)}/story.txt?token=${encodeURIComponent(token)}`,
};

export interface SharedStory {
  title: string; theme: string; objective: { title: string; description: string };
  players: { name: string; player: string; archetype: string; avatar: string; color: string }[];
  outcome: { kind: string; headline: string; group_summary: string; personal: Record<string, string> };
  chapters: { index: number; title: string; text: string; word_count: number }[];
  epilogues: Record<string, string>;
  achievements: Record<string, { title: string; emoji: string; description: string }[]>;
  completed_at: string | null;
}

// Sessions are per game code, so one browser can rejoin several games.
const KEY = "havoc.sessions";
export const sessions = {
  all(): Record<string, Session> {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
  },
  get(code: string): Session | null { return this.all()[code.toUpperCase()] ?? null; },
  set(s: Session) {
    try {
      const all = this.all();
      all[s.code.toUpperCase()] = s;
      localStorage.setItem(KEY, JSON.stringify(all));
    } catch { /* storage unavailable: session lives in memory only */ }
  },
  remove(code: string) {
    try {
      const all = this.all();
      delete all[code.toUpperCase()];
      localStorage.setItem(KEY, JSON.stringify(all));
    } catch { /* ignore */ }
  },
};
