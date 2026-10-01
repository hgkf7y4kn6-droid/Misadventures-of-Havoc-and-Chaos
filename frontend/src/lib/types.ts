export const GAME_TITLE = "The Misadventures of Havoc and Chaos";

export type Phase =
  | "lobby" | "theme_submission" | "theme_voting" | "objective_reveal" | "character_creation"
  | "adventure" | "final_challenge" | "story_generation" | "ended";
export type Visibility = "public" | "private" | "group" | "endgame_reveal" | "never_reveal";
export type CommMode = "open" | "restricted" | "private_only" | "disabled";
export type Risk = "safe" | "risky" | "wild";

export interface Character {
  name: string; archetype: string; personality: string; special_ability: string; weakness: string;
  secret_motivation?: string; starting_item: string; humorous_trait: string; avatar: string; color: string;
  stats?: Record<string, number>; complete: boolean;
}
export interface Item { id: string; name: string; description: string; tags: string[]; hidden: boolean }
export interface PublicPlayer {
  id: string; name: string; is_host: boolean; ready: boolean; connected: boolean; status: string;
  character: Character; health: number; inventory: Item[];
}
export interface Me extends PublicPlayer {
  resources: { health: number; luck: number; trust: number; ability_charges: number };
  location_id: string | null;
  preferences: AudioPreferences;
  stats: Record<string, unknown>;
}
export interface AudioPreferences { audio_enabled: boolean; narration_volume: number; preferred_voice: string }
export interface ResourceTrack { key: string; label: string; emoji: string; value: number; max: number; description: string }
export interface Objective {
  title: string; description: string; tagline: string; success_conditions: string[]; failure_conditions: string[];
  world_rules: string[]; progress_target: number; final_challenge: string; antagonist: string; macguffin: string;
}
export interface Choice { id: string; label: string; description: string; tags: string[]; risk: Risk; stat: string; cost: Record<string, number>; advances_objective: boolean }
export interface InfoItem { id: string; visibility: Visibility; audience: string[]; kind: string; title: string; text: string; round: number }
export interface Decision {
  group_id: string; round: number; kind: "individual" | "group" | "final"; title: string; player_ids: string[];
  shared_context: string; visible_information: InfoItem[]; available_choices: Choice[]; allow_freeform: boolean;
  deadline: number | null; comm_mode: CommMode; submitted: Record<string, boolean>;
  my_submission: { choice_id: string | null; freeform: string | null } | null;
}
export interface StoryEntry { id: string; round: number; kind: string; title: string; text: string; visibility: Visibility; created_at: number }
export interface ChatMessage { id: string; channel: string; sender_id: string | null; sender_name: string; text: string; audience: string[]; created_at: number }
export interface Achievement { key: string; title: string; description: string; emoji: string }
export interface Outcome {
  kind: string; headline: string; group_summary: string; final_roll: number; final_target: number;
  personal: Record<string, string>; achievements: Record<string, Achievement[]>;
}
export interface Chapter { index: number; title: string; text: string; word_count: number }
export interface FinalStory { title: string; chapters: Chapter[]; epilogues: Record<string, string>; word_count: number; generated_by: string }
export interface ThemeOptionView { id: string; title: string; merged_count: number; originals?: string[]; votes?: number }
export interface NPCView { id: string; name: string; emoji: string; personality: string; disposition: number }
export interface Round { number: number; beat: string; title: string; comm_mode: CommMode; escalation: number }
export interface GameSettings {
  min_players: number; max_players: number; adventure_length: "short" | "medium" | "long";
  theme_submission_seconds: number; theme_voting_seconds: number; character_creation_seconds: number; decision_seconds: number;
  default_comm_mode: CommMode; allow_public_share: boolean; narrator_voice: string; auto_advance: boolean;
}
export interface AudioStatus { state?: string; provider?: string; chapters?: number; ready?: number[]; failed?: number[]; voice?: string }
export interface Revealed { title: string; text: string; known_by: string[]; round: number | null }

export interface GameView {
  game_id: string; code: string; version: number; phase: Phase; settings: GameSettings; host_id: string;
  me: Me; players: PublicPlayer[]; theme: string | null; objective: Objective | null; objective_progress: number;
  shared_resources: Record<string, ResourceTrack>; locations: Record<string, { id: string; name: string; description: string; state: string[] }>;
  npcs: NPCView[]; current_scene: string; round: Round | null; turn_number: number; total_rounds: number; comm_mode: CommMode;
  story_feed: StoryEntry[]; information: InfoItem[]; decisions: Decision[]; chat: ChatMessage[];
  timers: { phase_deadline: number | null; phase_started_at: number };
  outcome: Outcome | null; final_story: FinalStory | null; share_id: string | null; audio_status: AudioStatus;
  my_theme?: string | null; themes_submitted?: string[]; theme_options?: ThemeOptionView[]; my_vote?: string | null; votes_cast?: string[];
  revealed?: Revealed[];
}

export interface GameEvent { event: string; data: Record<string, unknown>; ts: number }
export interface ServerConfig {
  title: string; min_players: number; max_players_limit: number; default_max_players: number; llm: string;
  tts: { provider: string; server_side: boolean }; voices: { id: string; description: string }[];
}
