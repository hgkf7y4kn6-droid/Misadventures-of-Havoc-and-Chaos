/**
 * Realtime contract. Mirrors backend/app/models/events.py — tests/test_protocol_contract.py
 * fails if the two drift apart.
 */

export const REALTIME_EVENTS = [
  "PLAYER_JOINED", "PLAYER_LEFT", "PLAYER_READY", "PLAYER_KICKED", "SETTINGS_CHANGED", "GAME_STARTED",
  "THEME_SUBMITTED", "THEME_SUBMISSIONS_CLOSED", "THEME_VOTING_STARTED", "THEME_VOTE_CAST", "THEME_SELECTED",
  "OBJECTIVE_REVEALED", "CHARACTER_CREATION_STARTED", "CHARACTER_UPDATED", "SCENE_STARTED", "DECISION_STARTED",
  "DECISION_SUBMITTED", "DECISION_RESOLVED", "GROUP_FORMED", "GROUP_DECISION_STARTED", "RESOURCE_CHANGED",
  "RANDOM_EVENT", "NPC_UPDATED", "PRIVATE_INFORMATION", "SCENE_RESOLVED", "FINAL_CHALLENGE_STARTED", "GAME_WON",
  "GAME_LOST", "FINAL_STORY_GENERATION_STARTED", "FINAL_STORY_GENERATED", "FINAL_AUDIO_GENERATION_STARTED",
  "FINAL_AUDIO_CHAPTER_READY", "FINAL_AUDIO_GENERATION_COMPLETE", "GAME_ENDED", "GAME_RESTARTED", "CHAT_MESSAGE", "ERROR",
] as const;
export type RealtimeEventName = (typeof REALTIME_EVENTS)[number];

/** Every action a client may send. Anything else is rejected by the engine. */
export const CLIENT_ACTIONS = [
  "set_ready", "update_profile", "update_settings", "start_game", "kick_player", "restart_game", "submit_theme",
  "close_phase", "cast_vote", "save_character", "suggest_character", "submit_decision", "chat", "set_preferences", "ping",
] as const;
export type ClientActionName = (typeof CLIENT_ACTIONS)[number];

export type ClientMessage =
  | { action: "set_ready"; ready: boolean }
  | { action: "update_profile"; name?: string; avatar?: string; color?: string; archetype?: string }
  | { action: "update_settings"; settings: Record<string, unknown> }
  | { action: "start_game" | "restart_game" | "close_phase" | "suggest_character" | "ping" }
  | { action: "kick_player"; player_id: string }
  | { action: "submit_theme"; text: string }
  | { action: "cast_vote"; option_id: string }
  | {
      action: "save_character"; name: string; archetype?: string; personality?: string; special_ability?: string;
      weakness?: string; secret_motivation?: string; starting_item?: string; humorous_trait?: string; avatar?: string;
      stats?: Record<string, number>;
    }
  | {
      action: "submit_decision"; group_id: string; choice_id?: string | null; freeform?: string | null;
      push_luck?: boolean; use_ability?: boolean; item_id?: string | null;
    }
  | { action: "chat"; channel: string; text: string; to?: string[] }
  | { action: "set_preferences"; audio_enabled?: boolean; narration_volume?: number; preferred_voice?: string };

import type { Character, GameView } from "./state";

/** Messages the server (engine or Durable Object) pushes on the game socket. */
export type ServerMessage =
  | { type: "state"; state: GameView }
  | { type: "event"; event: RealtimeEventName; data: Record<string, unknown>; ts: number }
  | { type: "error"; message: string; fatal?: boolean; detail?: string }
  | { type: "character_suggestion"; character: Character }
  | { type: "kicked" };

/** WebSocket close codes. */
export const CLOSE = { KICKED: 4003, INVALID: 4004 } as const;
