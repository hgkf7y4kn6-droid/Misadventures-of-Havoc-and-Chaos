# Target architecture: Expo · EAS · Clerk · PostHog · Cloudflare Workers + Durable Objects

This document frames how **The Misadventures of Havoc and Chaos** runs on a serverless edge with native apps, what is already built and verified, and the decisions still open.

## 1. Topology

```
 iOS / Android (Expo, built by EAS)        Web (React/Vite, served by the Worker)
   @clerk/expo · posthog-react-native         @clerk/react · posthog-js
                 └──────────── @havoc/client  (identity → seat → ticket → socket) ───────────┘
                                         │ HTTPS + WSS  (one origin)
                                         ▼
 ┌───────────────────────── Cloudflare ─────────────────────────────────────────────┐
 │ Worker  workers/edge/src/index.ts                                                │
 │   • public API (same contract as the standalone engine, see @havoc/protocol)     │
 │   • Clerk JWT verification (networkless, pinned PEM) + guest sessions            │
 │   • 60s socket tickets · CORS · PostHog reverse proxy (/ingest) · static web app │
 │                                                                                  │
 │ Durable Object  GameRoom  (one per game code)  workers/edge/src/room.ts          │
 │   • player WebSockets (Hibernation API, tagged by player id)                     │
 │   • the game's phase timer as a DO alarm (at-least-once, retried)                │
 │   • fan-out of the engine's per-player messages                                  │
 └───────────────▲─────────────────────────────────────────────┬────────────────────┘
   signed ops    │ POST /internal/rooms/:code/ops               │ signed calls
   (send, kick,  │                                              ▼ /internal/games/...
   alarm arm/    │      ┌──────────── Python engine (backend/) ─────────────┐
   cancel)       └──────│ authoritative rules, state, LLM + TTS services     │──► PostHog (server events)
                        │ EdgeLink = Publisher + Scheduler ports             │──► LLM / TTS providers
                        └──────────────────────┬─────────────────────────────┘
                                               ▼
                                PostgreSQL (snapshots + completed archive)
```

**Division of responsibility**

| Concern | Owner | Why |
|---|---|---|
| Game rules, state, visibility, dice, outcomes | Python engine | Unchanged and fully tested; the LLM services live next to it. |
| Sockets, presence, fan-out | GameRoom DO | Hibernating sockets cost nothing while players think; one object per game is a natural single writer. |
| Phase timers | GameRoom DO alarm → engine | Alarms survive engine restarts and redeploys; the engine ignores stale/duplicate firings by token. |
| Identity | Worker (Clerk / guest) | Verified at the edge; the engine only ever sees signed requests naming a player. |
| Product analytics | Engine (server events) + clients (screen/UI events) | Outcomes are only known server-side; clients add funnels. Neither ever sends player-written text. |
| Distribution | EAS Build / Submit / Update | Profiles, channels, runtime versions, OTA. |

## 2. Contracts (the seams that make this portable)

**Engine ports** (`backend/app/engine/ports.py`): `Publisher`, `Scheduler`, `Store`, `Analytics`. Standalone uses the WebSocket hub + asyncio timers; edge mode uses `EdgeLink` for both publishing and scheduling. The engine code is identical in both.

**Timers.** Every armed timer has a random token persisted in `GameState.timers`. A firing is accepted only if `(tag, token)` matches the currently armed one, so delivering late, twice, or after a restart is safe. That is exactly the guarantee DO alarms give.

**Engine → room** (`EdgeLink`): all outbound operations for a game go into one ordered buffer flushed as a single signed `POST /internal/rooms/:code/ops` — `send`, `kick`, `alarm {at_ms, tag, token}`, `cancel_alarm`. Ordering matters (a cancel must never overtake the following arm).

**Room/Worker → engine** (`backend/app/edge/internal_api.py`, HMAC-signed, ±300 s skew): `POST /internal/games` (create with an edge-chosen code), `/join`, `GET /seat?user_id=`, `/connect`, `/disconnect`, `/action` (acknowledged immediately; results and errors come back as ops), `/alarm`, and per-player `state`, `story.txt`, `audio/:n`.

**Signing:** `x-havoc-ts` + `x-havoc-sig = hex(HMAC_SHA256(EDGE_SECRET, "{ts}.{METHOD}.{path}.{sha256(body)}"))`. Implemented in `backend/app/edge/signing.py` and `workers/edge/src/signing.ts`.

**Public client contract** (`packages/protocol`): routes, state projection, realtime events, client actions. Both the standalone engine and the Worker serve it, so clients never know which they're talking to. `backend/tests/test_protocol_contract.py` fails if the TypeScript event/action lists drift from the engine.

## 3. Identity (Clerk + guests)

Party games can't require accounts, so there are two identities and one seat model:

| Credential | Format | Issued by | Use |
|---|---|---|---|
| Clerk session JWT | RS256 JWT | Clerk | Signed-in players; seats follow the account across devices. |
| Guest session | `g.<b64url json>.<b64url hmac>` (30 days) | Worker / engine (`SESSION_SECRET`) | No-account players; stored in Keychain/Keystore (SecureStore) or localStorage. |
| Socket ticket | `t.<…>` (60 s, bound to one game + seat) | Worker / engine | Opening the socket and fetching audio/story URLs, so long-lived tokens never appear in URLs. |

- A seat is `(game, user_id)`. Joining again with the same identity returns the same seat in any phase (app restart, new phone). Kicked user ids can't rejoin.
- Clerk verification at the edge uses `@clerk/backend` `verifyToken` with a pinned PEM key (`CLERK_JWT_KEY`, networkless) and `authorizedParties` (the `azp` check). The engine has an equivalent verifier for standalone deployments.
- Native sign-in uses Clerk's hosted Account Portal (`useHostedAuth`), which needs no custom native UI and works in Expo Go; the token is cached in SecureStore via `@clerk/expo/token-cache`.

## 4. Analytics (PostHog)

Server-side events from the engine (`GameManager.track`), grouped by `game` (PostHog group analytics). The `distinct_id` is the Clerk user id or the guest/seat id.

| Event | Key properties |
|---|---|
| `game_created`, `player_joined`, `player_kicked` | adventure_length, max_players, signed_in, players |
| `game_started`, `theme_selected`, `adventure_started` | options, votes, merged_submissions, total_rounds, characters_auto_assigned |
| `decision_submitted` | kind, freeform, push_luck, use_ability, used_item, round |
| `scene_resolved` | round, beat, timeout, actions, freeform/rogue counts, interactions, comm_mode, tier histogram, objective_progress |
| `game_completed` | outcome, won, doomed, rounds, final_score/target, duration_s, hidden_truths_found, llm |
| `story_generated` | generated_by, words, chapters, validation_passed/attempts/issues |

Clients add UI funnels (`game_create_clicked`, `narration_played`, `narration_toggled`, `story_downloaded`) and screen views.

**Privacy is enforced in code, not by convention:** `analytics.sanitize()` drops any string longer than 64 characters or with more than a few words, so themes, actions, chat and secrets cannot be sent even by mistake (tested). Client autocapture of inputs and session recording are off. The Worker's `/ingest/*` route proxies PostHog through the game's own domain.

## 5. Expo + EAS

- `apps/mobile` uses Expo SDK 57, Expo Router, `@clerk/expo`, `posthog-react-native`, `expo-secure-store`, `expo-audio` (server narration, keeps playing with the screen locked) and `expo-speech` (on-device narration fallback).
- `app.config.ts` derives bundle ids and names from `APP_VARIANT` so development, preview and production builds install side by side.
- `eas.json` profiles: `development` (dev client, internal), `preview` (internal, `preview` channel), `production` (auto-increment, `production` channel, submit config). `runtimeVersion.policy = "appVersion"` keeps OTA updates (`eas update --channel …`) compatible with native builds.
- Configuration via `EXPO_PUBLIC_API_URL`, `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY`, `EXPO_PUBLIC_POSTHOG_KEY` (set with `eas env:create` or per-profile `env`). The API URL points at the Worker origin.
- The app is a workspace package; Metro resolves `@havoc/client` / `@havoc/protocol` from the monorepo.

## 6. Running it locally

```bash
# 1. engine in edge mode
cd backend
HAVOC_EDGE_SECRET=dev-edge-secret HAVOC_EDGE_URL=http://localhost:8787 uvicorn app.main:app --port 8000

# 2. Worker + GameRoom (real workerd runtime) — also serves the built web app
npm run build:web
cp workers/edge/.dev.vars.example workers/edge/.dev.vars
npm run edge                                    # http://localhost:8787

# 3. native app against the Worker
cd apps/mobile && cp .env.example .env.local && npx expo start

# smoke test: a whole game advanced only by DO alarms (Clerk host + guest)
cd workers/edge && node scripts/smoke.mjs --keygen   # paste CLERK_JWT_KEY into .dev.vars, restart `npm run edge`
node scripts/smoke.mjs
```

## 7. What is built and verified

| Item | Evidence |
|---|---|
| Engine ports, EdgeLink, signed internal API | `tests/test_edge.py`: a full game driven only through the internal API by a fake DO, including stale and duplicate alarms, signature tampering, and op ordering. |
| Identity (Clerk RS256, guests, tickets, seat rejoin, kicks) | `tests/test_identity.py` (engine) and `scripts/smoke.mjs` (edge: wrong `azp` and expired JWTs rejected, cross-game ticket refused). |
| Worker + GameRoom DO under `wrangler dev` (workerd) | A full browser playthrough through the Worker; a no-input game advanced entirely by DO alarms to a finished story; secrets hidden during play and revealed at the end. |
| PostHog | Event taxonomy and privacy filter tested; batching to `/batch/` tested against a mock. |
| Expo app | `tsc --noEmit` clean; `expo export` produces iOS and Android Hermes bundles. |
| Protocol contract | Python ↔ TypeScript event/action parity test. |

**Not verified here:** deploying to a real Cloudflare account, real Clerk and PostHog projects, EAS cloud builds and store submission, running the app on a device or simulator, and `expo-doctor` (the sandbox blocks those networks). The Dockerfile was updated for the workspace layout but not built (no Docker daemon).

## 8. Decisions still open

1. **Where the Python engine runs in production.**
   - *A (recommended now): engine as a private origin service* — any container host, or Cloudflare Containers, reached through `ENGINE` (service binding) or `ENGINE_URL`. Zero rewrite; the whole tested engine and LLM/TTS stack is reused.
   - *B (later, optional): port the engine to TypeScript inside the GameRoom DO* — removes the origin hop and makes DO storage the system of record. The deterministic RNG makes this safe to do incrementally: record seeds + inputs from real games and replay them against both engines as a conformance suite.
2. **Engine scaling.** The engine keeps live games in memory, so with more than one replica each game code must stick to one replica. The DO already knows the code; the planned change is to route to `ENGINE_URL` by consistent hash of the code (or one container per hot shard).
3. **Postgres provider** (Neon, Supabase, RDS…). If any Worker-side reads are added later (e.g. share pages at the edge), use Hyperdrive.
4. **Narration storage.** Audio is cached on the engine's disk; for multi-replica or Containers, move the cache to R2 and serve `audio/:n` from the Worker with R2 + Cache API.
5. **Deep links.** `havoc://game/CODE` works via the `scheme`; universal links / app links for `https://<domain>/g/CODE` need the domain decision plus an `apple-app-site-association` / `assetlinks.json` served by the Worker.
6. **Clerk instance settings.** Add the native redirect and web origins to `authorized parties`; decide whether guests can upgrade to accounts mid-game (the seat model supports it if the engine re-binds the seat's `user_id`).
