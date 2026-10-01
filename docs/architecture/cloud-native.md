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

| Engine container image | `backend/Dockerfile` builds for linux/amd64; the built image ran as the engine behind the Worker against PostgreSQL, completing the alarm-only smoke game, and resumed the game after the engine was killed mid-game and kept down past several phase timers. |
| Production Worker config | `wrangler deploy --env production --dry-run` validates the bundle, both DO bindings, static assets, and the container definition. |

**Not verified here:** deploying to a real Cloudflare account (no credentials, and the sandbox can't reach `api.cloudflare.com`), real Clerk and PostHog projects, EAS cloud builds and store submission, running the app on a device or simulator, and `expo-doctor`.

## 8. Engine on Cloudflare Containers (decided: Option A)

The Python engine is kept as-is and runs as a **Cloudflare Container** (`EngineContainer` in `workers/edge/src/engine-container.ts`, image `backend/Dockerfile`). The alternative — porting the engine to TypeScript inside the GameRoom DO — is set aside.

- **Sharding.** Game codes are hashed (FNV-1a) onto `ENGINE_SHARDS` container instances (`engine-0 … engine-N-1`), so every game has exactly one authoritative engine process. Changing `ENGINE_SHARDS` re-hashes codes; do it only when no games are live. `max_instances` must be ≥ `ENGINE_SHARDS`.
- **Sleep and wake.** Instances sleep after 15 idle minutes. Games are persisted to Postgres on every change (including presence), and phase timers live in GameRoom alarms. So the next request or alarm wakes the instance, which rehydrates the game and continues.
- **Engine unavailable.** If the engine can't be reached (waking, rolling out, crashed), the GameRoom re-arms its alarm with backoff (1 s → 30 s, indefinitely). Duplicate firings are discarded by the engine's timer tokens. Socket actions get a friendly "waking up, try again" error instead of failing silently.
- **Configuration.** The Worker passes engine settings into the container as env vars (`engineEnv()`): `EDGE_SECRET`, `PUBLIC_URL` (where the engine posts room ops), `DATABASE_URL` (required, because container disks are ephemeral and the container refuses to start without it), `SESSION_SECRET`, `LLM_*`, `TTS_*`, `POSTHOG_*`.
- **Local dev stays Docker-free.** `ENGINE_MODE=url` points the Worker at an engine on `localhost:8000`; production sets `ENGINE_MODE=container`.

### Deploying

One-time setup:

```bash
cd workers/edge
# fill in [env.production.vars] in wrangler.toml: PUBLIC_URL, CLERK_AUTHORIZED_PARTIES, LLM/TTS providers
for s in EDGE_SECRET SESSION_SECRET DATABASE_URL CLERK_JWT_KEY LLM_API_KEY POSTHOG_API_KEY; do
  npx wrangler secret put $s --env production
done
```

Then either push to `main` (`.github/workflows/deploy.yml` runs the tests, builds the engine image, and runs `wrangler deploy --env production`; needs the repository secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`), or deploy from a machine with Docker:

```bash
npm run deploy:production -w @havoc/edge
```

`DATABASE_URL` uses SQLAlchemy's asyncpg form, e.g. `postgresql+asyncpg://user:pass@host:5432/db` (Neon, Supabase, RDS…).

## 9. Decisions still open

1. **Postgres provider** (Neon, Supabase, RDS…). If Worker-side reads are added later (e.g. share pages at the edge), use Hyperdrive.
2. **Narration storage.** Audio is cached on the container's ephemeral disk and regenerated after a sleep. Move it to R2 and serve `audio/:n` from the Worker with R2 + Cache API when server-side TTS is enabled.
3. **Deep links.** `havoc://game/CODE` works via the `scheme`; universal links / app links for `https://<domain>/g/CODE` need the domain decision plus an `apple-app-site-association` / `assetlinks.json` served by the Worker.
4. **Clerk instance settings.** Add the native redirect and web origins to `authorized parties`; decide whether guests can upgrade to accounts mid-game (the seat model supports it if the engine re-binds the seat's `user_id`).
