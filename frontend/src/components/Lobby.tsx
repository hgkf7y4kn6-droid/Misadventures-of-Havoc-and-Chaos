import { useState } from "react";
import type { GameView } from "../lib/types";
import { Avatar, Panel } from "./common";

const AVATARS = ["🦊", "🐙", "🦉", "🐸", "🦄", "🐻", "🐼", "🦖", "🐝", "🦩", "🐢", "🦔", "🐧", "🦦", "👽", "🤖", "🧙", "🦝"];
const COLORS = ["#f97316", "#a855f7", "#22c55e", "#eab308", "#ec4899", "#06b6d4", "#ef4444", "#84cc16"];

type Send = (m: Record<string, unknown>) => boolean;

export function Lobby({ view, send }: { view: GameView; send: Send }) {
  const me = view.me;
  const isHost = me.id === view.host_id;
  const link = `${location.origin}/g/${view.code}`;
  const [copied, setCopied] = useState(false);
  const [archetype, setArchetype] = useState(me.character.archetype);
  const others = view.players.filter((p) => !p.is_host);
  const readyCount = view.players.filter((p) => p.ready || p.is_host).length;
  const canStart = view.players.length >= view.settings.min_players && others.every((p) => p.ready);

  async function share() {
    try {
      if (navigator.share) await navigator.share({ title: "The Misadventures of Havoc and Chaos", text: `Join my misadventure! Code ${view.code}`, url: link });
      else { await navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    } catch { /* dismissed */ }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_1.2fr] max-w-6xl mx-auto">
      <div className="flex flex-col gap-5">
        <Panel title="Gather the Party">
          <p className="text-violet-200">Share this code. Everyone joins on their own device.</p>
          <div className="my-3 flex items-center gap-3 flex-wrap">
            <span className="font-display text-6xl tracking-[0.25em] text-zap drop-shadow-[3px_3px_0_#000]" aria-label={`Game code ${view.code.split("").join(" ")}`}>{view.code}</span>
            <button className="btn btn-zap" onClick={share}>{copied ? "Link copied!" : "Share link"}</button>
          </div>
          <p className="text-sm text-violet-300 break-all">{link}</p>
        </Panel>

        <Panel title="You">
          <div className="flex items-center gap-3 mb-3">
            <Avatar p={me} size={56} />
            <div>
              <div className="font-display text-2xl">{me.name}</div>
              <div className="text-violet-300 text-sm">{me.character.archetype || "Archetype: undecided (dangerously)"}</div>
            </div>
          </div>
          <div className="flex flex-wrap gap-1 mb-2" role="group" aria-label="Pick an avatar">
            {AVATARS.map((a) => (
              <button key={a} aria-label={`Avatar ${a}`} aria-pressed={me.character.avatar === a}
                className={`text-2xl rounded-lg p-1 ${me.character.avatar === a ? "bg-zap/30 ring-2 ring-zap" : "hover:bg-white/10"}`}
                onClick={() => send({ action: "update_profile", avatar: a })}>{a}</button>
            ))}
          </div>
          <div className="flex gap-2 mb-3" role="group" aria-label="Pick a color">
            {COLORS.map((c) => (
              <button key={c} aria-label={`Color ${c}`} aria-pressed={me.character.color === c}
                className={`w-8 h-8 rounded-full border-[3px] ${me.character.color === c ? "border-white" : "border-black"}`} style={{ background: c }}
                onClick={() => send({ action: "update_profile", color: c })} />
            ))}
          </div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); send({ action: "update_profile", archetype }); }}>
            <input className="input" maxLength={80} value={archetype} onChange={(e) => setArchetype(e.target.value)} placeholder="Quick archetype idea (e.g. Overconfident Intern)" aria-label="Archetype idea" />
            <button className="btn btn-ghost">Save</button>
          </form>
          <p className="text-xs text-violet-300 mt-2">You'll flesh out your full character once the objective is revealed.</p>
        </Panel>
      </div>

      <div className="flex flex-col gap-5">
        <Panel title={`Players (${view.players.length}/${view.settings.max_players}) · ${readyCount} ready`}>
          <ul className="flex flex-col gap-2" aria-live="polite">
            {view.players.map((p) => (
              <li key={p.id} className="flex items-center gap-3 bg-black/20 rounded-xl px-3 py-2">
                <Avatar p={p} />
                <div className="flex-1 min-w-0">
                  <div className="font-bold truncate">{p.name} {p.is_host && <span className="text-zap text-sm">👑 host</span>} {p.id === me.id && <span className="text-sky text-sm">(you)</span>}</div>
                  <div className="text-xs text-violet-300 truncate">{p.character.archetype || "—"} {p.connected ? "" : "· 📴 disconnected"}</div>
                </div>
                <span className={`font-display text-lg ${p.ready || p.is_host ? "text-slime" : "text-violet-400"}`}>{p.is_host ? "HOST" : p.ready ? "READY" : "…"}</span>
                {isHost && p.id !== me.id && (
                  <button className="btn btn-ghost text-sm px-2 py-1" onClick={() => confirm(`Remove ${p.name}?`) && send({ action: "kick_player", player_id: p.id })} aria-label={`Remove ${p.name}`}>✕</button>
                )}
              </li>
            ))}
          </ul>
          <div className="mt-4 flex gap-3 flex-wrap">
            {!isHost && (
              <button className={`btn text-xl ${me.ready ? "btn-ghost" : "btn-havoc"}`} onClick={() => send({ action: "set_ready", ready: !me.ready })}>
                {me.ready ? "Actually, wait" : "I'm Ready!"}
              </button>
            )}
            {isHost && (
              <button className="btn btn-chaos text-xl" disabled={!canStart} onClick={() => send({ action: "start_game" })}>
                {canStart ? "Begin the Misadventure" : view.players.length < view.settings.min_players ? `Need ${view.settings.min_players}+ players` : "Waiting for everyone to be ready"}
              </button>
            )}
          </div>
        </Panel>

        {isHost ? <HostSettings view={view} send={send} /> : (
          <Panel title="Settings">
            <p className="text-violet-200">{view.settings.adventure_length} adventure · {view.settings.decision_seconds}s per decision · narrator: {view.settings.narrator_voice}</p>
          </Panel>
        )}
      </div>
    </div>
  );
}

function HostSettings({ view, send }: { view: GameView; send: Send }) {
  const s = view.settings;
  const set = (patch: Record<string, unknown>) => send({ action: "update_settings", settings: patch });
  return (
    <Panel title="Host Settings">
      <div className="grid sm:grid-cols-2 gap-3">
        <label className="font-semibold">Adventure length
          <select className="input mt-1" value={s.adventure_length} onChange={(e) => set({ adventure_length: e.target.value })}>
            <option value="short">Short (~4 scenes)</option><option value="medium">Medium (~6 scenes)</option><option value="long">Long (~9 scenes)</option>
          </select>
        </label>
        <label className="font-semibold">Max players
          <input type="number" min={2} max={12} className="input mt-1" value={s.max_players} onChange={(e) => set({ max_players: Number(e.target.value) })} />
        </label>
        <label className="font-semibold">Seconds per decision
          <input type="number" min={15} max={600} step={15} className="input mt-1" value={s.decision_seconds} onChange={(e) => set({ decision_seconds: Number(e.target.value) })} />
        </label>
        <label className="font-semibold">Default chat
          <select className="input mt-1" value={s.default_comm_mode} onChange={(e) => set({ default_comm_mode: e.target.value })}>
            <option value="open">Director decides (open by default)</option><option value="disabled">No chat at all</option>
          </select>
        </label>
        <label className="font-semibold">Narrator voice
          <select className="input mt-1" value={s.narrator_voice} onChange={(e) => set({ narrator_voice: e.target.value })}>
            {["storyteller", "comedic", "dramatic", "chaotic", "deadpan"].map((v) => <option key={v}>{v}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 font-semibold mt-6">
          <input type="checkbox" checked={s.allow_public_share} onChange={(e) => set({ allow_public_share: e.target.checked })} /> Allow share links
        </label>
        <label className="flex items-center gap-2 font-semibold">
          <input type="checkbox" checked={s.auto_advance} onChange={(e) => set({ auto_advance: e.target.checked })} /> Timers auto-advance
        </label>
      </div>
    </Panel>
  );
}
