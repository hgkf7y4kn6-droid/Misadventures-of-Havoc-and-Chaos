// Edge smoke test: a whole game advanced ONLY by GameRoom alarms, through Worker → Durable Object → engine.
//
//   node scripts/smoke.mjs --keygen          # writes .keys/clerk_{priv,pub}.pem; put the pub key in .dev.vars as CLERK_JWT_KEY
//   (engine)  HAVOC_EDGE_SECRET=dev-edge-secret HAVOC_EDGE_URL=http://localhost:8787 uvicorn app.main:app --port 8000
//   (edge)    npm run dev
//   node scripts/smoke.mjs
//
// The host signs in with a locally-signed Clerk-format JWT; the second player is a guest. Nobody acts after
// "start": stragglers are auto-submitted when each phase's alarm fires.
import { createSign, generateKeyPairSync } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
if (process.argv.includes("--keygen")) {
  const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  mkdirSync(".keys", { recursive: true });
  writeFileSync(".keys/clerk_priv.pem", privateKey.export({ type: "pkcs8", format: "pem" }));
  writeFileSync(".keys/clerk_pub.pem", publicKey.export({ type: "spki", format: "pem" }));
  console.log(`CLERK_JWT_KEY="${publicKey.export({ type: "spki", format: "pem" }).toString().trim().replace(/\n/g, "\\n")}"`);
  process.exit(0);
}

const BASE = process.env.BASE ?? "http://localhost:8787";
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
function clerkJwt(sub, azp = "http://localhost:8787", expIn = 600) {
  const now = Math.floor(Date.now() / 1000);
  const head = b64({ alg: "RS256", typ: "JWT", kid: "test" });
  const body = b64({ sub, azp, iat: now, nbf: now - 5, exp: now + expIn, sid: "sess_1", iss: "https://clerk.test" });
  const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign(readFileSync(".keys/clerk_priv.pem")).toString("base64url");
  return `${head}.${body}.${sig}`;
}
const call = async (path, { method = "GET", token, body } = {}) => {
  const r = await fetch(BASE + path, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
};
const host = clerkJwt("user_clerk_alex");
// negative auth checks
console.log("bad azp ->", (await call("/api/games", { method: "POST", token: clerkJwt("user_x", "https://evil.test"), body: { name: "x" } })).status);
console.log("expired ->", (await call("/api/games", { method: "POST", token: clerkJwt("user_x", undefined, -60), body: { name: "x" } })).status);
console.log("session ->", JSON.stringify((await call("/api/session", { token: host })).data));

const fast = { theme_submission_seconds: 5, theme_voting_seconds: 5, character_creation_seconds: 5, decision_seconds: 5 };
const game = await call("/api/games", { method: "POST", token: host, body: { name: "Alex", settings: fast } });
const code = game.data.code;
const guest = (await call("/api/session/guest", { method: "POST", body: { name: "Sam" } })).data.token;
const seat = await call(`/api/games/${code}/join`, { method: "POST", token: guest, body: { name: "Sam" } });
const again = await call(`/api/games/${code}/join`, { method: "POST", token: guest, body: { name: "Sam again" } });
console.log("code", code, "rejoin same seat:", seat.data.player_id === again.data.player_id);
const stranger = (await call("/api/session/guest", { method: "POST", body: { name: "Nope" } })).data.token;
console.log("stranger ticket ->", (await call(`/api/games/${code}/ticket`, { method: "POST", token: stranger })).status);

const states = {};
async function open(token, label) {
  const { ticket } = (await call(`/api/games/${code}/ticket`, { method: "POST", token })).data;
  const ws = new WebSocket(`${BASE.replace(/^http/, "ws")}/ws/${code}?ticket=${ticket}`);
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === "state") states[label] = m.state; if (m.type === "error") console.log(label, "error:", m.message); };
  await new Promise((r) => (ws.onopen = r));
  return ws;
}
const hws = await open(host, "host");
const gws = await open(guest, "guest");
// forged identity: a reused ticket for another game must be refused
const { ticket } = (await call(`/api/games/${code}/ticket`, { method: "POST", token: guest })).data;
const bad = new WebSocket(`${BASE.replace(/^http/, "ws")}/ws/ZZZZZ?ticket=${ticket}`);
bad.onmessage = (e) => console.log("cross-game ticket ->", JSON.parse(e.data).message);
await new Promise((r) => setTimeout(r, 500));
gws.send(JSON.stringify({ action: "set_ready", ready: true }));
await new Promise((r) => setTimeout(r, 500));
hws.send(JSON.stringify({ action: "start_game" }));
const seen = [];
let leakDuringPlay = false, checkedDuringPlay = false;
const t0 = Date.now();
while (Date.now() - t0 < 240_000) {
  const p = states.host?.phase;
  if (p && seen.at(-1) !== `${p}:${states.host.turn_number}`) { seen.push(`${p}:${states.host.turn_number}`); console.log(((Date.now() - t0) / 1000).toFixed(1) + "s", p, "turn", states.host.turn_number); }
  if (["adventure", "final_challenge"].includes(p) && states.guest && states.host.me.character.secret_motivation &&
      JSON.stringify(states.guest).includes(states.host.me.character.secret_motivation)) leakDuringPlay = true;
  if (p === "adventure") checkedDuringPlay = true;
  if (p === "ended" && states.host.final_story) break;
  await new Promise((r) => setTimeout(r, 250));
}
const s = states.host;
console.log("RESULT", s.phase, s.outcome?.kind, "chapters", s.final_story?.chapters.length, "guest ended:", states.guest?.phase);
console.log("checked during play:", checkedDuringPlay, "| host secret visible to guest during play:", leakDuringPlay, "| revealed at end:", JSON.stringify(states.guest.revealed).includes(s.me.character.secret_motivation));
hws.close(); gws.close(); bad.close();
process.exit(0);
