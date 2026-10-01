/** HTTP contract served identically by the standalone engine and the Cloudflare edge Worker. */

export interface GuestSession { token: string; user_id: string; kind: "guest"; expires_at: number }
export interface Seat { code: string; game_id: string; player_id: string; token?: string /* v1 seat token */ }
export interface Ticket { ticket: string; expires_in: number; player_id: string }
export interface LobbyInfo { code: string; phase: string; players: number; max_players: number; joinable: boolean }

export interface ServerConfig {
  title: string; min_players: number; max_players_limit: number; default_max_players: number; llm: string;
  tts: { provider: string; server_side: boolean }; voices: { id: string; description: string }[];
  transport?: "direct" | "edge"; auth?: { guests: boolean; clerk: boolean };
}

export interface SharedStory {
  title: string; theme: string; objective: { title: string; description: string };
  players: { name: string; player: string; archetype: string; avatar: string; color: string }[];
  outcome: { kind: string; headline: string; group_summary: string; personal: Record<string, string> };
  chapters: { index: number; title: string; text: string; word_count: number }[];
  epilogues: Record<string, string>;
  achievements: Record<string, { title: string; emoji: string; description: string }[]>;
  completed_at: string | null;
}

export const ROUTES = {
  config: "/api/config",
  guest: "/api/session/guest",
  session: "/api/session",
  games: "/api/games",
  game: (code: string) => `/api/games/${encodeURIComponent(code)}`,
  join: (code: string) => `/api/games/${encodeURIComponent(code)}/join`,
  ticket: (code: string) => `/api/games/${encodeURIComponent(code)}/ticket`,
  state: (code: string) => `/api/games/${encodeURIComponent(code)}/state`,
  storyTxt: (code: string) => `/api/games/${encodeURIComponent(code)}/story.txt`,
  audio: (code: string, chapter: number) => `/api/games/${encodeURIComponent(code)}/audio/${chapter}`,
  share: (id: string) => `/api/share/${encodeURIComponent(id)}`,
  socket: (code: string) => `/ws/${encodeURIComponent(code)}`,
} as const;
