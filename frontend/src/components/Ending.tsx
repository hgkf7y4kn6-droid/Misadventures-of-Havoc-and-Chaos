import { useCallback, useEffect, useMemo, useState } from "react";
import { client } from "../lib/api";
import { track } from "../lib/analytics";
import { useNarrator } from "../lib/narrator";
import type { Chapter, GameView } from "../lib/types";
import { Avatar, OUTCOME_STYLE, Panel } from "./common";

type Send = (m: Record<string, unknown>) => boolean;

const PREFS_KEY = "havoc.audio";
interface LocalPrefs { audio_enabled: boolean; narration_volume: number; preferred_voice: string }

function loadLocalPrefs(): Partial<LocalPrefs> {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}"); } catch { return {}; }
}

export function StoryGenerating({ view }: { view: GameView }) {
  const o = view.outcome;
  const style = OUTCOME_STYLE[o?.kind ?? ""] ?? OUTCOME_STYLE.partial_success;
  return (
    <div className="max-w-2xl mx-auto text-center flex flex-col gap-5 animate-pop">
      {o && <h2 className={`font-display text-6xl ${style.color} -rotate-2`}>{style.emoji} {o.headline}</h2>}
      {o && <p className="text-lg">{o.group_summary}</p>}
      <Panel tone="paper">
        <p className="font-display text-3xl">Reconstructing what <em>actually</em> happened…</p>
        <p className="font-type mt-2">Cross-referencing secret decisions, hidden discoveries, and at least one goose.</p>
        <div className="mt-4 text-5xl animate-wobble" aria-hidden>📜✍️</div>
      </Panel>
    </div>
  );
}

export function Ending({ view, send, code }: { view: GameView; send: Send; code: string }) {
  const story = view.final_story;
  const o = view.outcome;
  const isHost = view.me.id === view.host_id;
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => { document.title = `${story?.title ?? "The Misadventures of Havoc and Chaos"} — The Complete Story`; }, [story?.title]);
  if (!story || !o) return null;
  const style = OUTCOME_STYLE[o.kind] ?? OUTCOME_STYLE.partial_success;
  const shareUrl = view.share_id && view.settings.allow_public_share ? `${location.origin}/story/${view.share_id}` : null;
  const fullText = `${story.title}\n\n` + story.chapters.map((c) => `${c.title}\n\n${c.text}`).join("\n\n");

  async function copy(text: string, label: string) {
    try { await navigator.clipboard.writeText(text); setCopied(label); setTimeout(() => setCopied(null), 2000); } catch { /* ignore */ }
  }

  return (
    <div className="max-w-6xl mx-auto flex flex-col gap-6">
      <header className="text-center">
        <p className="font-display text-2xl text-sky">The Misadventures of Havoc and Chaos:</p>
        <h2 className="font-display text-5xl sm:text-7xl text-zap -rotate-2 drop-shadow-[5px_5px_0_#000]">The Complete Story</h2>
        <p className={`font-display text-4xl mt-4 ${style.color}`}>{style.emoji} {o.headline}</p>
        <p className="max-w-2xl mx-auto mt-2 text-violet-100">{o.group_summary}</p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1.7fr_1fr]">
        <article className="flex flex-col gap-4 min-w-0" aria-label="The complete adventure">
          <ReadAloud view={view} send={send} code={code} chapters={story.chapters} title={story.title} />
          <nav className="sticker p-3" aria-label="Chapters">
            <ol className="flex flex-wrap gap-2 text-sm">
              {story.chapters.map((c) => <li key={c.index}><a className="underline decoration-zap hover:text-zap" href={`#chapter-${c.index}`}>{c.title}</a></li>)}
            </ol>
          </nav>
          <div className="sticker-paper p-5 sm:p-8">
            <h3 className="font-display text-4xl mb-1">{story.title}</h3>
            <p className="font-type text-sm opacity-70 mb-4">{story.word_count.toLocaleString()} words · {story.chapters.length} chapters · told by {story.generated_by === "procedural" ? "the house narrator" : "the AI narrator"}</p>
            {story.chapters.map((c) => (
              <section key={c.index} id={`chapter-${c.index}`} className="mb-8 scroll-mt-4">
                <h4 className="font-display text-3xl mb-2 text-[#c2410c]">{c.title}</h4>
                {c.text.split(/\n\s*\n/).map((para, i) => <p key={i} className="font-type text-[1.05rem] leading-relaxed mb-3">{para}</p>)}
              </section>
            ))}
          </div>
        </article>

        <aside className="flex flex-col gap-4 min-w-0">
          <Panel title="🏆 Achievements">
            <ul className="space-y-3">
              {view.players.map((p) => (
                <li key={p.id}>
                  <div className="flex items-center gap-2 font-bold"><Avatar p={p} size={28} /> {p.character.name || p.name}</div>
                  <ul className="flex flex-wrap gap-1 mt-1">
                    {(o.achievements[p.id] ?? []).map((a) => (
                      <li key={a.key} title={a.description} className="text-xs rounded-full bg-zap text-black font-bold px-2 py-0.5 border-2 border-black">{a.emoji} {a.title}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="🪦 What Became of Everyone">
            <ul className="space-y-2 text-sm">
              {view.players.map((p) => <li key={p.id}><b>{p.character.name || p.name}:</b> {o.personal[p.id]}</li>)}
            </ul>
          </Panel>
          {view.revealed && view.revealed.length > 0 && (
            <Panel title="🔓 Secrets Revealed">
              <ul className="space-y-2 text-sm">
                {view.revealed.map((r, i) => (
                  <li key={i} className="bg-black/25 rounded p-2"><b>{r.title}:</b> {r.text}{r.known_by.length > 0 && <span className="text-violet-300"> — known by {r.known_by.join(", ")}</span>}</li>
                ))}
              </ul>
            </Panel>
          )}
          <Panel title="📤 Keep the Legend">
            <div className="flex flex-col gap-2">
              <button className="btn btn-zap" onClick={() => copy(fullText, "story")}>{copied === "story" ? "Copied!" : "Copy the story"}</button>
              <button className="btn btn-ghost" onClick={async () => { track("story_downloaded", {}); location.assign(await client.storyTxtUrl(code)); }}>Download as text</button>
              {shareUrl ? (
                <button className="btn btn-chaos" onClick={() => copy(shareUrl, "link")}>{copied === "link" ? "Link copied!" : "Copy read-only share link"}</button>
              ) : <p className="text-sm text-violet-300">The host disabled public share links.</p>}
              {isHost && <button className="btn btn-havoc text-xl mt-2" onClick={() => send({ action: "restart_game" })}>Play again (same party)</button>}
            </div>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function ReadAloud({ view, send, code, chapters, title }: { view: GameView; send: Send; code: string; chapters: Chapter[]; title: string }) {
  const server = view.audio_status?.provider && view.audio_status.provider !== "browser" && view.audio_status.state !== "client";
  // Local preferences apply instantly (even offline); the server copy follows this player across devices.
  const [prefs, setPrefs] = useState<LocalPrefs>(() => ({ ...view.me.preferences, ...loadLocalPrefs() }));
  const savePrefs = useCallback((patch: Partial<LocalPrefs>) => {
    setPrefs((p) => {
      const next = { ...p, ...patch };
      try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
    send({ action: "set_preferences", ...patch });
  }, [send]);

  const audioUrl = useCallback((i: number) => client.audioUrl(code, i, prefs.preferred_voice), [code, prefs.preferred_voice]);
  const opts = useMemo(() => ({
    mode: (server ? "server" : "browser") as "server" | "browser", chapters, title, audioUrl,
    voiceStyle: prefs.preferred_voice, volume: prefs.narration_volume,
  }), [server, chapters, title, audioUrl, prefs.preferred_voice, prefs.narration_volume]);
  const { state, narrator } = useNarrator(opts, prefs.audio_enabled);
  const ready = view.audio_status?.ready ?? [];
  const total = chapters.length;
  const progress = ((state.chapter + state.chapterProgress) / total) * 100;

  return (
    <section className="sticker p-4" aria-label="Read aloud">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="font-display text-3xl text-zap">THE COMPLETE ADVENTURE</h3>
        <button className={`btn ${prefs.audio_enabled ? "btn-slime bg-slime text-black" : "btn-ghost"}`} aria-pressed={prefs.audio_enabled}
          onClick={() => { track("narration_toggled", { enabled: !prefs.audio_enabled }); savePrefs({ audio_enabled: !prefs.audio_enabled }); }}>
          {prefs.audio_enabled ? "🔊 Audio On" : "🔇 Audio Off"}
        </button>
      </div>
      {state.status === "unsupported" && <p className="text-chaos mt-2">Your browser can't narrate. The story is all here to read.</p>}
      {prefs.audio_enabled ? (
        <>
          <div className="flex flex-wrap gap-2 mt-3">
            {state.status === "paused" ? (
              <button className="btn btn-havoc" onClick={() => narrator.resume()}>Resume ▶</button>
            ) : state.status === "playing" || state.status === "loading" ? (
              <button className="btn btn-zap" onClick={() => narrator.pause()}>Pause ⏸</button>
            ) : (
              <button className="btn btn-havoc" onClick={() => { track("narration_played", { mode: server ? "server" : "browser", voice: prefs.preferred_voice }); narrator.play(state.status === "idle" && state.chapterProgress >= 1 ? 0 : state.chapter); }}>Play Story ▶</button>
            )}
            <button className="btn btn-ghost" onClick={() => narrator.stop()} disabled={state.status === "idle"}>Stop ■</button>
            <button className="btn btn-ghost" onClick={() => narrator.restart()}>Restart ⟲</button>
            <label className="flex items-center gap-2 font-semibold">Volume
              <input type="range" min={0} max={1} step={0.05} value={prefs.narration_volume} aria-label="Narration volume"
                onChange={(e) => savePrefs({ narration_volume: Number(e.target.value) })} />
            </label>
            <label className="flex items-center gap-2 font-semibold">Voice
              <select className="rounded border-2 border-black bg-ink-3 px-1" value={prefs.preferred_voice} onChange={(e) => { narrator.stop(); savePrefs({ preferred_voice: e.target.value }); }}>
                {["storyteller", "comedic", "dramatic", "chaotic", "deadpan"].map((v) => <option key={v}>{v}</option>)}
              </select>
            </label>
          </div>
          <div className="mt-3">
            <div className="flex justify-between text-sm font-semibold">
              <span aria-live="polite">Chapter {state.chapter + 1} of {total}{state.status === "loading" ? " · loading narration…" : ""}</span>
              {server && <span className="text-violet-300">{ready.length}/{total} chapters narrated</span>}
            </div>
            <div className="h-4 rounded-full bg-black/40 border-2 border-black overflow-hidden mt-1" role="progressbar" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100} aria-label="Story playback progress">
              <div className="h-full bg-havoc transition-all" style={{ width: `${progress}%` }} />
            </div>
            <ol className="flex gap-1 mt-2 flex-wrap">
              {chapters.map((c) => (
                <li key={c.index}>
                  <button className={`text-xs rounded px-2 py-0.5 border-2 border-black ${c.index === state.chapter ? "bg-zap text-black" : "bg-ink-3"}`}
                    onClick={() => narrator.play(c.index)} aria-label={`Play ${c.title}`}>{c.index + 1}{server && !ready.includes(c.index) ? "…" : ""}</button>
                </li>
              ))}
            </ol>
          </div>
          {state.error && <p className="text-chaos mt-2" role="alert">{state.error}</p>}
          <p className="text-xs text-violet-300 mt-2">Narration plays only for you. Everyone else controls their own.</p>
        </>
      ) : <p className="mt-2 text-violet-200">Narration is off. Read silently below — it won't start again unless you turn it back on.</p>}
    </section>
  );
}
