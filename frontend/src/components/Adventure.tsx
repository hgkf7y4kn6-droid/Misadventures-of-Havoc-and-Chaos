import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatMessage, CommMode, Decision, GameView, StoryEntry } from "../lib/types";
import { Avatar, Panel, Timer } from "./common";

type Send = (m: Record<string, unknown>) => boolean;

const COMM_LABEL: Record<CommMode, string> = {
  open: "💬 Open chat",
  restricted: "🤐 Group chat only",
  private_only: "📨 Private messages only",
  disabled: "🔇 Silence. The Director forbids talking.",
};
const RISK_STYLE = { safe: "bg-slime text-black", risky: "bg-zap text-black", wild: "bg-chaos text-white" } as const;
const VIS_STYLE: Record<string, string> = {
  public: "",
  private: "border-l-8 border-l-zap",
  group: "border-l-8 border-l-sky",
};

export function Adventure({ view, send }: { view: GameView; send: Send }) {
  const final = view.phase === "final_challenge";
  return (
    <div className="max-w-7xl mx-auto flex flex-col gap-4">
      <SceneHeader view={view} />
      <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <div className="flex flex-col gap-4 min-w-0">
          {view.decisions.map((d) => <DecisionPanel key={d.group_id} view={view} decision={d} send={send} />)}
          {!view.decisions.length && (
            <Panel>
              <p className="font-display text-2xl text-zap">{final ? "Bracing for impact…" : "The dust settles…"}</p>
              <p className="text-violet-200">Read what happened below. The Director will move things along shortly.</p>
              {view.me.id === view.host_id && (
                <button className="btn btn-havoc mt-2" onClick={() => send({ action: "close_phase" })}>Continue to the next scene</button>
              )}
            </Panel>
          )}
          <StoryFeed entries={view.story_feed} />
        </div>
        <aside className="flex flex-col gap-4 min-w-0" aria-label="Party status">
          <ObjectivePanel view={view} />
          <ResourcesPanel view={view} />
          <MePanel view={view} />
          <SecretsPanel view={view} />
          <ChatPanel view={view} send={send} />
          <PartyPanel view={view} />
        </aside>
      </div>
    </div>
  );
}

function SceneHeader({ view }: { view: GameView }) {
  const r = view.round;
  const deadline = view.decisions[0]?.deadline ?? view.timers.phase_deadline;
  return (
    <div className="flex flex-wrap items-center gap-3 justify-between">
      <div>
        <div className="text-sm text-violet-300 font-semibold">
          {view.phase === "final_challenge" ? "THE FINAL CHALLENGE" : `SCENE ${view.turn_number} OF ${view.total_rounds}`}{r ? ` · ${r.beat.replace(/_/g, " ")}` : ""}
        </div>
        <h2 className="font-display text-4xl sm:text-5xl text-zap -rotate-1 drop-shadow-[3px_3px_0_#000]">{r?.title ?? "…"}</h2>
      </div>
      <div className="flex gap-2 items-center flex-wrap">
        <span className="sticker px-3 py-1 font-semibold">{COMM_LABEL[view.comm_mode]}</span>
        <Timer deadline={deadline} />
      </div>
    </div>
  );
}

function DecisionPanel({ view, decision: d, send }: { view: GameView; decision: Decision; send: Send }) {
  const me = view.me;
  const [choice, setChoice] = useState<string | null>(d.my_submission?.choice_id ?? null);
  const [freeform, setFreeform] = useState(d.my_submission?.freeform ?? "");
  const [pushLuck, setPushLuck] = useState(false);
  const [useAbility, setUseAbility] = useState(false);
  const [item, setItem] = useState("");
  const submitted = d.submitted[me.id];
  const waiting = d.player_ids.filter((p) => !d.submitted[p]).length;
  const members = view.players.filter((p) => d.player_ids.includes(p.id));

  function submit() {
    const ok = send({
      action: "submit_decision", group_id: d.group_id,
      choice_id: freeform.trim() ? null : choice, freeform: freeform.trim() || null,
      push_luck: pushLuck, use_ability: useAbility, item_id: item || null,
    });
    if (ok) { setPushLuck(false); setUseAbility(false); }
  }

  return (
    <section className="sticker-paper p-4 animate-pop" aria-labelledby={`dec-${d.group_id}`}>
      <div className="flex justify-between items-start gap-2 flex-wrap">
        <h3 id={`dec-${d.group_id}`} className="font-display text-3xl">{d.kind === "group" ? "👥 " : d.kind === "final" ? "⚔️ " : "🤫 "}{d.title}</h3>
        <span className="font-type text-sm bg-black/10 rounded px-2 py-1">
          {d.kind === "group" ? `Group decision with ${members.filter((m) => m.id !== me.id).map((m) => m.character.name || m.name).join(", ")}` : d.kind === "final" ? "Your part in the finale" : "Only you can see this"}
        </span>
      </div>
      <p className="font-type text-lg mt-2 whitespace-pre-line">{d.shared_context}</p>
      {d.visible_information.length > 0 && (
        <ul className="mt-2 space-y-1">{d.visible_information.map((i) => <li key={i.id} className="bg-zap/40 rounded px-2 py-1">🔎 <b>{i.title}:</b> {i.text}</li>)}</ul>
      )}
      <div className="grid sm:grid-cols-2 gap-2 mt-3" role="radiogroup" aria-label="Choices">
        {d.available_choices.map((c) => (
          <button key={c.id} role="radio" aria-checked={choice === c.id && !freeform}
            onClick={() => { setChoice(c.id); setFreeform(""); }}
            className={`text-left rounded-xl border-[3px] border-black p-3 bg-white transition ${choice === c.id && !freeform ? "ring-4 ring-havoc -rotate-1 shadow-[4px_4px_0_#000]" : "hover:-rotate-[0.5deg]"}`}>
            <div className="font-bold text-lg">{c.label}</div>
            {c.description && <div className="text-sm opacity-80">{c.description}</div>}
            <div className="flex gap-1 flex-wrap mt-1 text-xs font-bold">
              <span className={`rounded px-1.5 py-0.5 border-2 border-black ${RISK_STYLE[c.risk]}`}>{c.risk.toUpperCase()}</span>
              <span className="rounded px-1.5 py-0.5 border-2 border-black bg-violet-200">{c.stat}</span>
              {Object.entries(c.cost).map(([k, v]) => <span key={k} className="rounded px-1.5 py-0.5 border-2 border-black bg-orange-200">-{v} {view.shared_resources[k]?.label ?? k}</span>)}
              {c.advances_objective && <span className="rounded px-1.5 py-0.5 border-2 border-black bg-emerald-200">🎯 objective</span>}
            </div>
          </button>
        ))}
      </div>
      {d.allow_freeform && (
        <label className="block mt-3 font-semibold">…or do literally anything else:
          <textarea className="input mt-1 text-base" rows={2} maxLength={300} value={freeform} onChange={(e) => setFreeform(e.target.value)}
            placeholder='e.g. "I convince Greg to dress up like a giant chicken and distract the guard."' />
        </label>
      )}
      <div className="flex flex-wrap gap-3 mt-3 items-center">
        <label className={`flex items-center gap-1 font-semibold ${me.resources.luck ? "" : "opacity-40"}`}>
          <input type="checkbox" disabled={!me.resources.luck} checked={pushLuck} onChange={(e) => setPushLuck(e.target.checked)} /> 🍀 Push luck ({me.resources.luck} left)
        </label>
        <label className={`flex items-center gap-1 font-semibold ${me.resources.ability_charges ? "" : "opacity-40"}`}>
          <input type="checkbox" disabled={!me.resources.ability_charges} checked={useAbility} onChange={(e) => setUseAbility(e.target.checked)} /> ✨ Use ability ({me.resources.ability_charges})
        </label>
        {me.inventory.length > 0 && (
          <label className="flex items-center gap-1 font-semibold">🎒
            <select className="rounded border-2 border-black px-1 bg-white" value={item} onChange={(e) => setItem(e.target.value)} aria-label="Use an item">
              <option value="">No item</option>
              {me.inventory.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
          </label>
        )}
      </div>
      <div className="flex items-center gap-3 mt-3 flex-wrap">
        <button className="btn btn-havoc text-xl" disabled={!choice && !freeform.trim()} onClick={submit}>{submitted ? "Change my decision" : "Lock it in"}</button>
        <span className="font-type" aria-live="polite">
          {submitted ? (waiting ? `Locked in. Waiting on ${waiting} other${waiting > 1 ? "s" : ""}…` : "Everyone's in. Resolving…") : "Nobody else can see what you choose."}
        </span>
      </div>
      {d.kind === "group" && (
        <div className="flex gap-2 mt-2 flex-wrap text-sm">
          {members.map((m) => <span key={m.id} className="flex items-center gap-1"><Avatar p={m} size={22} />{d.submitted[m.id] ? "✅" : "🤔"}</span>)}
        </div>
      )}
    </section>
  );
}

function StoryFeed({ entries }: { entries: StoryEntry[] }) {
  const end = useRef<HTMLDivElement>(null);
  const shown = entries.filter((e) => !["Theme Submissions Open", "Theme Voting"].includes(e.title));
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [shown.length]);
  return (
    <Panel title="📖 The Story So Far" className="max-h-[70vh] overflow-y-auto">
      <ol className="flex flex-col gap-3" aria-live="polite">
        {shown.map((e) => (
          <li key={e.id} className={`rounded-xl bg-black/25 p-3 ${VIS_STYLE[e.visibility] ?? ""} ${e.kind === "scene" ? "bg-havoc/15" : ""}`}>
            <div className="flex justify-between gap-2 flex-wrap">
              <h3 className={`font-display text-xl ${e.kind === "scene" ? "text-zap" : e.kind === "event" ? "text-sky" : "text-white"}`}>{e.title}</h3>
              {e.visibility !== "public" && (
                <span className="text-xs font-bold rounded px-2 py-0.5 bg-zap text-black self-start">{e.visibility === "group" ? "👥 GROUP ONLY" : "🤫 ONLY YOU"}</span>
              )}
            </div>
            <p className="whitespace-pre-line leading-relaxed">{e.text}</p>
          </li>
        ))}
      </ol>
      <div ref={end} />
    </Panel>
  );
}

function ObjectivePanel({ view }: { view: GameView }) {
  const o = view.objective;
  if (!o) return null;
  const pct = Math.min(100, Math.round((view.objective_progress / Math.max(1, o.progress_target)) * 100));
  return (
    <Panel title="🎯 Objective">
      <p className="font-bold">{o.title}</p>
      <p className="text-sm text-violet-200">{o.description}</p>
      <div className="mt-2" role="progressbar" aria-valuenow={view.objective_progress} aria-valuemin={0} aria-valuemax={o.progress_target} aria-label="Objective progress">
        <div className="h-4 rounded-full bg-black/40 border-2 border-black overflow-hidden"><div className="h-full bg-slime transition-all" style={{ width: `${pct}%` }} /></div>
        <div className="text-xs mt-1">Progress {view.objective_progress}/{o.progress_target}</div>
      </div>
    </Panel>
  );
}

function ResourcesPanel({ view }: { view: GameView }) {
  return (
    <Panel title="🧺 Shared Resources">
      <ul className="grid grid-cols-1 gap-1.5">
        {Object.values(view.shared_resources).map((r) => {
          const low = r.value <= Math.max(1, r.max * 0.2);
          return (
            <li key={r.key} title={r.description} className="flex items-center gap-2">
              <span className="w-6 text-center" aria-hidden>{r.emoji}</span>
              <span className="flex-1 text-sm font-semibold">{r.label}</span>
              <span className="w-24 h-3 rounded-full bg-black/40 border border-black overflow-hidden" aria-hidden>
                <span className={`block h-full ${low ? "bg-chaos" : "bg-zap"}`} style={{ width: `${(r.value / r.max) * 100}%` }} />
              </span>
              <span className={`w-12 text-right font-display ${low ? "text-chaos" : ""}`}>{r.value}/{r.max}</span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function MePanel({ view }: { view: GameView }) {
  const me = view.me;
  const r = me.resources;
  return (
    <Panel title={<span className="flex items-center gap-2"><Avatar p={me} size={34} /> {me.character.name}</span>}>
      <p className="text-sm text-violet-200">{me.character.archetype}</p>
      <div className="grid grid-cols-4 gap-2 my-2 text-center">
        <Stat label="Health" value={`${r.health}/10`} warn={r.health <= 3} emoji="❤️" />
        <Stat label="Luck" value={r.luck} emoji="🍀" />
        <Stat label="Trust" value={r.trust} warn={r.trust <= 3} emoji="🤝" />
        <Stat label="Ability" value={r.ability_charges} emoji="✨" />
      </div>
      {me.status === "incapacitated" && <p className="text-chaos font-bold">You're knocked out. You can still act — badly.</p>}
      <p className="text-sm"><b>Ability:</b> {me.character.special_ability}</p>
      <p className="text-sm"><b>Weakness:</b> {me.character.weakness}</p>
      <p className="text-sm"><b>Secret:</b> <span className="blur-sm hover:blur-none focus:blur-none transition" tabIndex={0}>{me.character.secret_motivation}</span></p>
      <h3 className="font-display text-lg mt-2">🎒 Inventory</h3>
      <ul className="text-sm list-disc pl-5">
        {me.inventory.length ? me.inventory.map((i) => <li key={i.id}>{i.name}{i.hidden && <span className="text-zap" title="Nobody else knows you have this"> 🤫</span>}</li>) : <li>Pockets: empty. Hope: low.</li>}
      </ul>
    </Panel>
  );
}

function Stat({ label, value, emoji, warn }: { label: string; value: string | number; emoji: string; warn?: boolean }) {
  return (
    <div className={`rounded-lg bg-black/30 p-1 ${warn ? "text-chaos" : ""}`}>
      <div aria-hidden>{emoji}</div><div className="font-display text-lg">{value}</div><div className="text-[10px] uppercase">{label}</div>
    </div>
  );
}

function SecretsPanel({ view }: { view: GameView }) {
  const items = view.information.filter((i) => i.visibility !== "public");
  if (!items.length && !view.npcs.length) return null;
  return (
    <Panel title="🤫 What Only You Know">
      {items.length === 0 && <p className="text-sm text-violet-300">Nothing yet. Go poke something.</p>}
      <ul className="space-y-2">
        {items.slice().reverse().map((i) => (
          <li key={i.id} className={`rounded-lg p-2 ${i.visibility === "group" ? "bg-sky/15" : "bg-zap/15"}`}>
            <div className="font-bold text-sm">{i.visibility === "group" ? "👥 " : "🤫 "}{i.title} <span className="font-normal text-xs opacity-70">· scene {i.round}</span></div>
            <div className="text-sm">{i.text}</div>
          </li>
        ))}
      </ul>
      {view.npcs.length > 0 && (
        <>
          <h3 className="font-display text-lg mt-3">People you've met</h3>
          <ul className="text-sm space-y-1">
            {view.npcs.map((n) => (
              <li key={n.id}>{n.emoji} <b>{n.name}</b> — {n.disposition >= 2 ? "likes you" : n.disposition <= -2 ? "hates you" : n.disposition < 0 ? "suspicious" : "undecided"}</li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

function PartyPanel({ view }: { view: GameView }) {
  return (
    <Panel title="🧑‍🤝‍🧑 The Party">
      <ul className="space-y-1.5">
        {view.players.map((p) => (
          <li key={p.id} className={`flex items-center gap-2 ${p.status !== "active" ? "opacity-50" : ""}`}>
            <Avatar p={p} size={28} />
            <span className="flex-1 text-sm"><b>{p.character.name || p.name}</b> <span className="text-violet-300">({p.name})</span></span>
            <span className="text-sm">❤️ {p.health}</span>
            {p.status === "incapacitated" && <span title="Knocked out">💫</span>}
            {!p.connected && <span title="Disconnected">📴</span>}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ChatPanel({ view, send }: { view: GameView; send: Send }) {
  const mode = view.comm_mode;
  const groups = view.decisions.filter((d) => d.player_ids.length > 1);
  const channels = useMemo(() => {
    const out: { id: string; label: string; enabled: boolean }[] = [{ id: "global", label: "Everyone", enabled: mode === "open" }];
    groups.forEach((g) => out.push({ id: `group:${g.group_id}`, label: "My group", enabled: mode === "open" || mode === "restricted" }));
    out.push({ id: "dm", label: "Private", enabled: mode === "open" || mode === "private_only" });
    return out;
  }, [mode, groups]);
  const [channel, setChannel] = useState("global");
  const [to, setTo] = useState<string>("");
  const [text, setText] = useState("");
  const active = channels.find((c) => c.id === channel) ?? channels[0];
  const others = view.players.filter((p) => p.id !== view.me.id);
  const msgs = view.chat.filter((m: ChatMessage) => (active.id === "dm" ? m.channel.startsWith("dm:") : m.channel === active.id));
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [msgs.length]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    const payload: Record<string, unknown> = { action: "chat", channel: active.id === "dm" ? "dm" : active.id, text };
    if (active.id === "dm") {
      if (!to) return;
      payload.to = [to];
    }
    if (send(payload)) setText("");
  }

  return (
    <Panel title="💬 Chat">
      <div className="flex gap-1 mb-2 flex-wrap" role="tablist">
        {channels.map((c) => (
          <button key={c.id} role="tab" aria-selected={active.id === c.id} className={`btn text-sm px-2 py-1 ${active.id === c.id ? "btn-zap" : "btn-ghost"}`} onClick={() => setChannel(c.id)}>
            {c.label}{!c.enabled && " 🔒"}
          </button>
        ))}
      </div>
      <div className="h-48 overflow-y-auto bg-black/30 rounded-lg p-2 text-sm space-y-1" aria-live="polite">
        {msgs.length === 0 && <p className="text-violet-400 italic">No messages.</p>}
        {msgs.map((m) => (
          <p key={m.id}><b style={{ color: view.players.find((p) => p.id === m.sender_id)?.character.color }}>{m.sender_name}</b>
            {m.channel.startsWith("dm:") && <span className="text-xs text-violet-300"> → {m.audience.filter((a) => a !== m.sender_id).map((a) => view.players.find((p) => p.id === a)?.name).join(", ")}</span>}
            : {m.text}</p>
        ))}
        <div ref={end} />
      </div>
      {active.enabled ? (
        <form className="mt-2 flex flex-col gap-2" onSubmit={submit}>
          {active.id === "dm" && (
            <select className="input py-1" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Message who?">
              <option value="">Whisper to…</option>
              {others.map((p) => <option key={p.id} value={p.id}>{p.character.name || p.name}</option>)}
            </select>
          )}
          <div className="flex gap-2">
            <input className="input py-1" maxLength={400} value={text} onChange={(e) => setText(e.target.value)} placeholder="Say something you'll regret…" aria-label="Chat message" />
            <button className="btn btn-havoc px-3 py-1">Send</button>
          </div>
        </form>
      ) : <p className="mt-2 text-sm text-violet-300">{COMM_LABEL[mode]}</p>}
    </Panel>
  );
}
