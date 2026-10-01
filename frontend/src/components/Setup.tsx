import { useEffect, useState } from "react";
import type { Character, GameView } from "../lib/types";
import { Avatar, Panel, Timer } from "./common";

type Send = (m: Record<string, unknown>) => boolean;

const THEME_IDEAS = [
  "A group of incompetent pirates trying to steal the moon",
  "Office workers trapped inside a haunted Costco",
  "Medieval knights attempting to deliver a pizza before it gets cold",
  "A group of raccoons accidentally becomes the government",
  "Retired superheroes running a suspicious bed & breakfast",
  "Wedding planners hired by a dragon",
];

function HostSkip({ view, send, label }: { view: GameView; send: Send; label: string }) {
  if (view.me.id !== view.host_id) return null;
  return <button className="btn btn-ghost text-sm" onClick={() => send({ action: "close_phase" })}>{label}</button>;
}

export function ThemeSubmission({ view, send }: { view: GameView; send: Send }) {
  const [text, setText] = useState("");
  const submitted = view.themes_submitted ?? [];
  const idea = THEME_IDEAS[(view.code.charCodeAt(0) + view.me.name.length) % THEME_IDEAS.length];
  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-5">
      <div className="flex justify-between items-center flex-wrap gap-3">
        <h2 className="font-display text-5xl text-zap -rotate-1">Pitch a Premise</h2>
        <Timer deadline={view.timers.phase_deadline} />
      </div>
      <Panel tone="paper">
        <p className="font-type text-lg mb-3">Secretly pitch the adventure you want. Nobody sees pitches until submissions close. Similar ideas get merged.</p>
        {view.my_theme ? (
          <div>
            <p className="font-display text-2xl">Your pitch is in:</p>
            <p className="font-type text-xl mt-1">“{view.my_theme}”</p>
            <button className="btn btn-ghost mt-3" onClick={() => setText(view.my_theme ?? "")}>Change it</button>
          </div>
        ) : null}
        {(!view.my_theme || text) && (
          <form className="flex flex-col gap-3 mt-2" onSubmit={(e) => { e.preventDefault(); if (send({ action: "submit_theme", text })) setText(""); }}>
            <label className="sr-only" htmlFor="theme">Your theme</label>
            <textarea id="theme" className="input text-lg" rows={3} minLength={3} maxLength={200} value={text} onChange={(e) => setText(e.target.value)} placeholder={idea} />
            <div className="flex gap-2 flex-wrap">
              <button className="btn btn-havoc text-xl" disabled={text.trim().length < 3}>Submit secretly</button>
              <button type="button" className="btn btn-ghost" onClick={() => setText(idea)}>Steal an idea</button>
            </div>
          </form>
        )}
      </Panel>
      <Panel title={`Pitches in: ${submitted.length}/${view.players.length}`}>
        <div className="flex flex-wrap gap-3">
          {view.players.map((p) => (
            <div key={p.id} className={`flex items-center gap-2 rounded-xl px-3 py-1 ${submitted.includes(p.id) ? "bg-slime/20" : "bg-black/20 opacity-70"}`}>
              <Avatar p={p} size={28} /> {p.name} {submitted.includes(p.id) ? "✅" : "✍️"}
            </div>
          ))}
        </div>
        <div className="mt-3"><HostSkip view={view} send={send} label="Close submissions now" /></div>
      </Panel>
    </div>
  );
}

export function ThemeVoting({ view, send }: { view: GameView; send: Send }) {
  const votes = view.votes_cast ?? [];
  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-5">
      <div className="flex justify-between items-center flex-wrap gap-3">
        <h2 className="font-display text-5xl text-chaos rotate-1">Vote in Secret</h2>
        <Timer deadline={view.timers.phase_deadline} />
      </div>
      <div className="grid gap-4" role="radiogroup" aria-label="Theme options">
        {(view.theme_options ?? []).map((o, i) => {
          const mine = view.my_vote === o.id;
          return (
            <button key={o.id} role="radio" aria-checked={mine} onClick={() => send({ action: "cast_vote", option_id: o.id })}
              className={`sticker-paper text-left p-4 transition ${mine ? "ring-4 ring-havoc -rotate-1" : i % 2 ? "rotate-[0.5deg]" : "-rotate-[0.5deg]"}`}>
              <div className="font-display text-3xl">{o.title}</div>
              {o.merged_count > 1 && <div className="font-type text-sm mt-1">🧠 {o.merged_count} players had basically the same idea</div>}
              {mine && <div className="font-display text-havoc mt-1">YOUR VOTE</div>}
            </button>
          );
        })}
      </div>
      <Panel>
        <p>Votes cast: {votes.length}/{view.players.length}. Nobody can see who voted for what.</p>
        <div className="mt-2"><HostSkip view={view} send={send} label="Close voting now" /></div>
      </Panel>
    </div>
  );
}

export function ObjectiveReveal({ view, send }: { view: GameView; send: Send }) {
  const o = view.objective;
  if (!o) return null;
  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-5 animate-pop">
      <p className="text-center font-display text-2xl text-sky">The people have spoken:</p>
      <h2 className="text-center font-display text-5xl sm:text-6xl text-zap -rotate-2 drop-shadow-[4px_4px_0_#000]">{view.theme}</h2>
      <Panel tone="paper" title={<>🎯 {o.title}</>}>
        <p className="font-type text-lg">{o.description}</p>
        {o.tagline && <p className="font-display text-xl mt-2 text-havoc">{o.tagline}</p>}
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <div><h3 className="font-display text-xl">✅ Win if</h3><ul className="list-disc pl-5">{o.success_conditions.map((s) => <li key={s}>{s}</li>)}</ul></div>
          <div><h3 className="font-display text-xl">💥 Lose if</h3><ul className="list-disc pl-5">{o.failure_conditions.map((s) => <li key={s}>{s}</li>)}</ul></div>
        </div>
        <h3 className="font-display text-xl mt-3">📜 Rules of this world</h3>
        <ul className="list-disc pl-5">{o.world_rules.map((s) => <li key={s}>{s}</li>)}</ul>
      </Panel>
      <div className="flex justify-center gap-3 items-center">
        <Timer deadline={view.timers.phase_deadline} label="Character creation in" />
        <HostSkip view={view} send={send} label="Skip ahead" />
      </div>
    </div>
  );
}

const EMPTY: Character = { name: "", archetype: "", personality: "", special_ability: "", weakness: "", secret_motivation: "", starting_item: "", humorous_trait: "", avatar: "🎲", color: "#f97316", complete: false };
const FIELDS: [keyof Character, string, string][] = [
  ["name", "Name", "Greg"],
  ["archetype", "Archetype", "Extremely Confident Accountant"],
  ["personality", "Personality", "Overconfident and loud"],
  ["special_ability", "Special ability", "Can calculate probabilities instantly"],
  ["weakness", "Weakness", "Cannot resist correcting people"],
  ["secret_motivation", "Secret motivation (only you will know… until the end)", "Believes every problem can be solved with spreadsheets"],
  ["starting_item", "Starting item", "A laminated pie chart"],
  ["humorous_trait", "Humorous trait (optional)", "Hums the Jeopardy theme under pressure"],
];

export function CharacterCreation({ view, send, suggestion, clearSuggestion }: { view: GameView; send: Send; suggestion: Character | null; clearSuggestion: () => void }) {
  const me = view.me;
  const [c, setC] = useState<Character>(() => (me.character.complete ? me.character : { ...EMPTY, ...me.character, name: me.character.name || me.name }));
  const [editing, setEditing] = useState(!me.character.complete);
  useEffect(() => {
    if (suggestion) { setC((prev) => ({ ...suggestion, avatar: prev.avatar, color: prev.color })); clearSuggestion(); }
  }, [suggestion, clearSuggestion]);
  const done = view.players.filter((p) => p.character.complete).length;

  return (
    <div className="max-w-5xl mx-auto grid lg:grid-cols-[1.4fr_1fr] gap-5">
      <Panel tone="paper" title="Create Your Character">
        {editing ? (
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); if (send({ action: "save_character", ...c, stats: undefined })) setEditing(false); }}>
            {FIELDS.map(([key, label, ph]) => (
              <label key={key} className="font-semibold">{label}
                <input className="input mt-1" required={key === "name"} maxLength={key === "name" ? 40 : key === "starting_item" || key === "archetype" ? 80 : 200}
                  value={String(c[key] ?? "")} placeholder={ph} onChange={(e) => setC({ ...c, [key]: e.target.value })} />
              </label>
            ))}
            <div className="flex gap-2 flex-wrap">
              <button className="btn btn-havoc text-xl">Lock it in</button>
              <button type="button" className="btn btn-zap" onClick={() => send({ action: "suggest_character" })}>🎲 Roll me a character</button>
            </div>
          </form>
        ) : (
          <div className="font-type">
            <p className="font-display text-3xl">{me.character.avatar} {me.character.name}</p>
            <p className="text-lg">{me.character.archetype}</p>
            <ul className="mt-2 space-y-1">
              <li><b>Ability:</b> {me.character.special_ability}</li>
              <li><b>Weakness:</b> {me.character.weakness}</li>
              <li><b>Secret:</b> {me.character.secret_motivation} 🤫</li>
              <li><b>Item:</b> {me.character.starting_item}</li>
              <li><b>Stats:</b> {Object.entries(me.character.stats ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ")}</li>
            </ul>
            <button className="btn btn-ghost mt-3" onClick={() => setEditing(true)}>Edit</button>
          </div>
        )}
      </Panel>
      <div className="flex flex-col gap-5">
        <Timer deadline={view.timers.phase_deadline} />
        <Panel title={`The Party (${done}/${view.players.length} ready)`}>
          <ul className="space-y-2">
            {view.players.map((p) => (
              <li key={p.id} className="flex gap-2 items-center"><Avatar p={p} size={32} />
                <span className="flex-1">{p.character.complete ? <><b>{p.character.name}</b> · {p.character.archetype}</> : <i>{p.name} is still deciding who to be…</i>}</span>
                {p.character.complete ? "✅" : "✍️"}
              </li>
            ))}
          </ul>
          <p className="text-sm text-violet-300 mt-3">Anyone who runs out of time gets a character assigned by the universe.</p>
          {view.me.id === view.host_id && <button className="btn btn-ghost text-sm mt-2" onClick={() => send({ action: "close_phase" })}>Start the adventure now</button>}
        </Panel>
        {view.objective && <Panel title="🎯 The Objective"><p className="font-bold">{view.objective.title}</p><p className="text-sm text-violet-200">{view.objective.description}</p></Panel>}
      </div>
    </div>
  );
}
