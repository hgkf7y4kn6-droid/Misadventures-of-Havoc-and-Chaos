# The Misadventures of Havoc and Chaos

A real-time, multiplayer, AI-narrated cooperative storytelling game for 2–12 players.

Everyone pitches a ridiculous premise in secret, votes on one, and then sets off together — only to be split up, handed different information, and asked to make simultaneous hidden decisions whose consequences collide in ways nobody can predict. At the end, the game reconstructs **the complete adventure**: one polished, chaptered story that reveals what everyone was *actually* doing — and reads it aloud.

> *"Wait… THAT'S why the bridge exploded?"*

It is a **multiplayer game engine whose narrative is powered by AI**, not a chatbot pretending to be a game. The server is authoritative over every rule, roll, resource, timer, vote and secret; the language model only supplies words.

---

## Quick start

### Fully offline (no API keys, no services)

```bash
# backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload            # http://localhost:8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                              # http://localhost:5173 (proxies /api and /ws to :8000)
```

Open the site in two browser windows (or on two phones), create a game in one and join with the code in the other.

With no configuration the game uses SQLite, in-process realtime, the built-in **procedural narrator**, and **browser speech** for read-aloud. Everything works; adding an LLM makes the prose richer.

### Full stack with Docker (PostgreSQL + Redis)

```bash
cp .env.example .env          # optionally add LLM / TTS keys
docker compose up --build
open http://localhost:8000
```

The image builds the web client and serves it from the game server.

### With an LLM narrator

```bash
HAVOC_LLM_PROVIDER=anthropic HAVOC_LLM_API_KEY=sk-ant-... uvicorn app.main:app
```

The Anthropic provider uses the official SDK with structured outputs (default model `claude-opus-5-5`, server-side refusal fallback enabled). An OpenAI-compatible provider is included for any chat-completions endpoint. See [Configuration](#configuration).

---

## How a game plays

```
LOBBY → READY → THEME SUBMISSION (secret) → DUPLICATE MERGING → THEME VOTING (secret)
→ COMMON OBJECTIVE → CHARACTER CREATION → ADVENTURE
    ↳ scenes: individual hidden decisions · branching paths · group decisions
      · resources · random events · consequences · escalation
→ FINAL CHALLENGE → GROUP OUTCOME → COMPLETE ADVENTURE RECONSTRUCTION → NARRATED EPILOGUE
```

| Phase | What happens |
|---|---|
| **Lobby** | Host creates a game and shares a 5-letter code/link. Players join live, pick avatar/color/archetype, mark ready. Host can change settings, remove players, start (only when the minimum is met and everyone is ready), and restart after the ending. |
| **Theme submission** | Each player privately pitches a premise. Nobody sees pitches until submissions close (all in, timer, or host closes). |
| **Merging** | Pitches are clustered by semantic similarity — *"Pirates steal the moon"*, *"Pirates trying to rob the moon"* and *"Space pirates stealing the moon"* become one option: **Pirates Attempt to Steal the Moon**. |
| **Voting** | One secret vote each. Server tallies; ties break deterministically (most original submitters, then a seeded draw). |
| **Objective** | A shared, funny, failable objective with success/failure conditions, world rules, an antagonist, a macguffin, themed resource names, locations, NPCs and **hidden variables** (secret facts players can discover). |
| **Characters** | Name, archetype, personality, special ability, weakness, secret motivation, starting item, humorous trait (or "Roll me a character"). Abilities, weaknesses and items mechanically affect rolls. |
| **Adventure** | The Narrative Director runs a sequence of scenes (4 / 6 / 9 for short / medium / long). |
| **Final challenge** | Everyone contributes individually; the result combines contributions, objective progress, hidden truths discovered, morale and who is still standing. |
| **Ending** | Outcome, personal outcomes, evidence-based achievements, revealed secrets, and the complete chaptered story with Read Aloud, copy, download and share. |

---

## Architecture

```
┌──────────── React + TypeScript + Tailwind (Vite) ───────────┐
│ Landing · Lobby · Themes · Characters · Adventure · Ending  │
│ useGame (WebSocket, reconnect) · Narrator (TTS / speech)    │
└───────────────▲──────────────────────────────┬──────────────┘
     per-player │ projections                  │ validated actions
                │                              ▼
┌────────────────────── FastAPI (Python) ─────────────────────────┐
│ api/routes.py      REST + WebSocket, token auth, rate limits    │
│ realtime/hub.py    fan-out (in-process, or Redis pub/sub)       │
│ registry.py        live games, rehydration after restart        │
│ ┌───────────── engine/  (AUTHORITATIVE) ─────────────────────┐  │
│ │ game_manager  phases · timers · permissions · orchestration│  │
│ │ director      pacing · grouping · comm modes · events      │  │
│ │ resolution    rolls · interactions · costs · consequences  │  │
│ │ visibility    per-player projections · authorised contexts │  │
│ │ themes        semantic merging · deterministic tally       │  │
│ │ chronicle     AdventureEvent log → final story pipeline    │  │
│ │ achievements  computed from recorded stats/events          │  │
│ │ rng           seeded, per-purpose deterministic streams    │  │
│ └─────────────────────────────────────────────────────────────┘ │
│ llm/  provider abstraction + 14 specialised services (words only)│
│ tts/  provider abstraction · chapter segmentation · audio cache │
│ persistence/  SQLAlchemy: live snapshots + completed archive    │
└──────────────────────────────────────────────────────────────────┘
          PostgreSQL (or SQLite)            Redis (optional)
```

### Who decides what

| The engine decides | The LLM writes |
|---|---|
| Phases, timers, transitions, permissions | Scene openings, situations, choices (sanitised) |
| Votes, ties, groups, communication modes | Interpretation of free-text actions (validated, clamped) |
| Dice (seeded), modifiers, outcome tiers | Narration of outcomes the engine already decided |
| Shared & individual resources, costs | NPC personalities, objective flavour, twists |
| Who may see which information | Summaries, the final story, repairs to it |
| Success, failure, the final outcome | |

Every LLM response is parsed into a Pydantic model (`models/llm_schemas.py`), retried once with the validation error, then **sanitised** (tags filtered to a fixed vocabulary, stats/costs clamped, ids resolved by the engine) — and if anything fails, the deterministic procedural generator takes over so a table of players is never stuck waiting on a provider.

### The LLM services

`ThemeNormalizer`, `ThemeMerger`, `ObjectiveGenerator`, `CharacterGenerator`, `SceneGenerator`, `DecisionGenerator`, `ActionInterpreter`, `ConsequenceResolver`, `NPCGenerator`, `NarrativeDirector`, `StorySummarizer`, `AdventureChronicleBuilder` (deterministic, in `engine/chronicle.py`), `FinalStoryGenerator`, `FinalStoryEditor` — see `backend/app/llm/services.py`. Each has its own narrow prompt; there is no giant "what happens next?" prompt.

---

## Core mechanics

### Simultaneous hidden decisions
In a scene the Director forms decision groups (solo, pairs, halves or everyone). Each group gets its own context and choices built from **only what its members are allowed to know**. Submissions stay private (others see only *that* you decided, never *what*) until every group has decided or the timer expires (stragglers hesitate into the safest option). Then all actions resolve **together**.

### Cross-player interactions
Resolution computes interactions between actions in the same scene and across scenes, for example:

- a **distraction** in one place gives a sneaking/stealing player elsewhere a bonus;
- a **noise** ruins someone else's hiding;
- a **locked door** strands a fleeing teammate;
- someone **negotiates** for an item another player already **stole**;
- a **destroyed** bridge penalises anyone crossing it in a later scene (a persistent world "scar"), while a **built** contraption helps them;
- a **betrayal** quietly drains party money and trust.

Each interaction links the two `AdventureEvent`s, which is exactly what the final story uses to produce the *"OH — that's what you were doing!"* reveals.

### Rolls and outcomes
`d20 + stat + ability/weakness/item + morale/health/hunger + reputation/NPC disposition + interactions + world scars` vs a target set by risk (safe 8 / risky 12 / wild 15) and escalation. Outcomes: **catastrophic success, unexpected success, success, partial success, complication, failure**, plus delayed consequences (they come back 1–2 scenes later), injuries, new opportunities (loot, clues), and hidden-truth discoveries. Players can push their luck (reroll, costs luck), use their special ability (charges), or use an inventory item. All randomness is derived from the game seed plus a purpose label, so outcomes are reproducible and independent of submission order.

### Free-text actions
Any decision accepts *"…or do literally anything else"*. The `ActionInterpreter` maps it to feasibility, risk, stat, tags, costs, targets and possible consequences; the engine resolves it like any other action. Betraying the group, burning down the cave or dressing a teammate as a chicken are all valid. In group decisions, the plurality wins — but anyone who wrote their own action **goes rogue** and resolves it separately.

### Resources that matter
Shared: food (eaten every scene; running out costs health and morale), money (bribes, purchases), time (every scene costs time; at zero the finale starts), supplies (needed to build/repair), morale (bonus/penalty to every roll; collapse forces an early finale), reputation (affects every negotiation), vehicle (broken transport costs extra time). Individual: health (zero = incapacitated), luck, trust, ability charges, inventory (hidden items), personal knowledge.

### The Narrative Director
Chooses each scene's beat (early adventure → first major decision → branching paths → resource pressure → group interaction → escalation → plot twist → major consequence → final challenge), groups players by similar paths, sets the communication mode (`open`, `restricted`, `private_only`, `disabled`), raises difficulty, draws random events (helpful when resources are low, harmful when the party is comfortable, character-specific for the least-involved player), brings back running jokes as callbacks, plants hunches toward hidden truths, and guarantees the story reaches an ending.

### Story memory
Short-term entries, a rolling medium summary, long-term facts, per-character memories, world facts, unresolved threads, running jokes (with mention counts), major decisions and important consequences — all bounded, so prompts never grow without limit.

---

## Information isolation

Four layers, all server-side:

1. **Visibility on everything.** Info items, story entries and chronicle events carry `public`, `private`, `group`, `endgame_reveal` or `never_reveal` plus an audience.
2. **Per-player projections.** Clients never receive `GameState`; `engine/visibility.py` builds a view for exactly one player. Tokens, the RNG seed, other players' choices and secret motivations, undiscovered hidden truths and anything outside your audience are never sent.
3. **Authorised LLM contexts.** A prompt addressed to a group contains only information *every* member may see; a prompt for one player contains their own secrets and nobody else's.
4. **Endgame rules.** The final story reveals private, group and `endgame_reveal` information, never `never_reveal`. Share links expose only the story, outcome and achievements — no tokens, ids or raw chronicle.

These guarantees are tested (`tests/test_isolation.py`, `tests/test_llm_services.py::test_prompts_respect_information_isolation`).

---

## The complete adventure reconstruction

```
Game History → Event Filtering → Chronological Reconstruction → Cross-Player Relationship Mapping
→ Story Chronicle → Final Narrative (LLM) → Validation → (Repair ×2) → Complete Adventure Story
                                                     ↘ procedural writer if the LLM is unavailable or keeps failing
```

- The **Adventure Chronicle** is written during play as structured `AdventureEvent`s (`event_id, timestamp, sequence_number, event_type, player_ids, location, public/private/group information, decisions, consequences, resource_changes, narrative_summary, importance, related_events`).
- **Intersections** come from the `related_events` links created by interactions and world scars.
- **Validation** checks that the story includes every player character, the objective, every major event, the correct outcome, every hidden truth meant for reveal, important NPCs and recurring jokes; that it reveals nothing marked `never_reveal`, leaks no internal ids, and meets the length target. Failed drafts go to the `FinalStoryEditor` with the list of issues.
- **Length** targets per adventure length are configurable (defaults 1–2k / 2–4k / 4–7k words). The story is split into titled chapters and per-character epilogues.

## Read Aloud

```
Final Story → Chapter Segmentation → TTS per Chapter (split under provider limits) → Audio Cache → Sequential Playback
```

- Providers live behind `TTSProvider`: **OpenAI**, **ElevenLabs**, **Google Cloud TTS**, **Amazon Polly**, and the **browser** fallback (Web Speech API, no server audio).
- Voices are provider-independent: `{"voice_style": "comedic", "language": "en-US", "speed": 1.0}` with styles *dramatic, comedic, storyteller, chaotic, deadpan*; each provider maps styles to its own voices.
- Chapters are pre-generated in order after the story is written (`FINAL_AUDIO_CHAPTER_READY` events show progress) and are also generated on demand, cached by provider + voice + text.
- Controls: play, pause, resume, stop, restart, per-chapter jump, volume, voice, **Audio On/Off**. Playback and preferences are **per player** (stored locally and server-side); turning audio off stops it immediately and it never auto-restarts. The full text is always on screen.

---

## Realtime protocol

Clients connect to `GET /ws/{code}?token=…`. The socket's identity is fixed at connect time, so a client can never act as someone else.

**Server → client:** `{"type":"state","state":<projection>}`, `{"type":"event","event":<NAME>,"data":{…}}`, `{"type":"error",…}`, `{"type":"character_suggestion",…}`, `{"type":"kicked"}`.

**Events:** `PLAYER_JOINED, PLAYER_LEFT, PLAYER_READY, GAME_STARTED, THEME_SUBMITTED, THEME_SUBMISSIONS_CLOSED, THEME_VOTING_STARTED, THEME_VOTE_CAST, THEME_SELECTED, OBJECTIVE_REVEALED, SCENE_STARTED, DECISION_STARTED, DECISION_SUBMITTED, DECISION_RESOLVED, GROUP_FORMED, GROUP_DECISION_STARTED, RESOURCE_CHANGED, RANDOM_EVENT, NPC_UPDATED, SCENE_RESOLVED, FINAL_CHALLENGE_STARTED, GAME_WON, GAME_LOST, FINAL_STORY_GENERATION_STARTED, FINAL_STORY_GENERATED, FINAL_AUDIO_GENERATION_STARTED, FINAL_AUDIO_CHAPTER_READY, FINAL_AUDIO_GENERATION_COMPLETE, GAME_ENDED` (plus `PRIVATE_INFORMATION`, `CHAT_MESSAGE`, `CHARACTER_UPDATED`, `SETTINGS_CHANGED`, `PLAYER_KICKED`, `GAME_RESTARTED`). Events are delivered only to their audience.

**Client → server** (discriminated by `action`, validated with Pydantic, unknown shapes rejected): `set_ready, update_profile, update_settings, start_game, kick_player, restart_game, submit_theme, close_phase, cast_vote, save_character, suggest_character, submit_decision, chat, set_preferences, ping`.

### REST

| Method | Path | |
|---|---|---|
| GET | `/api/health`, `/api/config` | status, limits, voices, providers |
| POST | `/api/games` | create → `{code, player_id, token}` |
| GET | `/api/games/{code}` | public lobby info |
| POST | `/api/games/{code}/join` | join → `{player_id, token}` |
| GET | `/api/games/{code}/state` | your projection (`Authorization: Bearer <token>`) |
| PUT | `/api/games/{code}/preferences` | audio preferences |
| GET | `/api/games/{code}/story.txt` | download the story |
| GET | `/api/games/{code}/audio/{chapter}` | narration MP3 (`204` when narration is browser-side) |
| GET | `/api/share/{share_id}` (+ `/story.txt`) | read-only public story, if the host allowed sharing |

---

## Security

- All player text is untrusted: length-limited, control characters stripped, prompt-injection phrasing defanged, and always wrapped in `<player_input>` delimiters with an explicit instruction that it is story material, not instructions (`llm/safety.py`).
- LLM output can't change rules or state directly; it is validated and clamped, and the engine owns every number.
- Players authenticate with a random secret token; decisions are only accepted from members of that decision group, in the right phase, with resources they actually have.
- WebSocket messages are schema-validated, size-limited and rate-limited per connection.
- Removed players' tokens are revoked.

---

## Configuration

All settings are environment variables prefixed `HAVOC_` (see `.env.example`).

| Variable | Default | |
|---|---|---|
| `HAVOC_DATABASE_URL` | `sqlite+aiosqlite:///./havoc.db` | `postgresql+asyncpg://…` in production |
| `HAVOC_REDIS_URL` | unset | enables Redis pub/sub fan-out |
| `HAVOC_LLM_PROVIDER` | `offline` | `offline`, `anthropic`, `openai` |
| `HAVOC_LLM_MODEL` / `HAVOC_LLM_API_KEY` / `HAVOC_LLM_BASE_URL` | | provider settings |
| `HAVOC_TTS_PROVIDER` | `browser` | `browser`, `openai`, `elevenlabs`, `google`, `polly` |
| `HAVOC_TTS_API_KEY` | | |
| `HAVOC_MIN_PLAYERS` / `HAVOC_MAX_PLAYERS_LIMIT` / `HAVOC_DEFAULT_MAX_PLAYERS` | 2 / 12 / 8 | |
| `HAVOC_DECISION_SECONDS` etc. | | phase timers (hosts can change per lobby) |
| `HAVOC_STORY_WORDS_SHORT/MEDIUM/LONG` | `[1000,2000]` … | final story length targets |

**Scaling note:** each game is owned by one server process (its `GameManager` holds the lock, timers and in-memory state; snapshots go to the database after every change, and a restarted server rehydrates games and re-arms timers). Redis provides cross-instance WebSocket fan-out; for multiple instances, route each game code to one instance (e.g. consistent hashing on `/ws/{code}` and `/api/games/{code}`).

---

## Development

```bash
cd backend && pytest            # 36 tests: engine, isolation, LLM validation, mechanics, TTS, full game over WebSockets
cd frontend && npm run build    # typecheck + production build
```

```
backend/app/
  config.py            settings
  main.py              FastAPI app (serves frontend/dist if built)
  registry.py          live games, rehydration, narration pre-generation
  api/routes.py        REST + WebSocket
  engine/              authoritative game engine (see Architecture)
  llm/                 provider abstraction, safety, specialised services
  tts/                 TTS providers, voices, chapter pipeline + cache
  models/              GameState & friends, realtime protocol, LLM schemas
  persistence/db.py    SQLAlchemy models + repository
  realtime/hub.py      WebSocket fan-out
frontend/src/
  lib/                 API client, WebSocket hook, narrator, types
  components/          Landing, Lobby, Setup, Adventure, Ending, SharedStory
```

### Adding a provider
- **LLM:** implement `LLMProvider.complete_json(system, user, schema)` (and optionally `embed`) in `backend/app/llm/providers/`, register it in `build_provider`.
- **TTS:** implement `TTSProvider.synthesize(text, VoiceConfig)` with your own style→voice mapping and `max_chars`, register it in `build_tts_provider`.
