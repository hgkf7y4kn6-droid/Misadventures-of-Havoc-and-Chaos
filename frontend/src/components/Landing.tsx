import { useEffect, useState } from "react";
import { client } from "../lib/api";
import { track } from "../lib/analytics";
import { AccountButton } from "../lib/auth";
import { Logo } from "./common";

export function Landing({ initialCode = "" }: { initialCode?: string }) {
  const [name, setName] = useState(() => localStorage.getItem("havoc.name") ?? "");
  const [code, setCode] = useState(initialCode);
  const [length, setLength] = useState<"short" | "medium" | "long">("short");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"create" | "join">(initialCode ? "join" : "create");

  useEffect(() => { document.title = "The Misadventures of Havoc and Chaos"; }, []);

  const remember = () => { try { localStorage.setItem("havoc.name", name.trim()); } catch { /* ignore */ } };

  async function create() {
    if (!name.trim()) return setError("Every legend needs a name. Even a bad one.");
    setBusy(true); setError(null); remember();
    try {
      const s = await client.createGame(name.trim(), { adventure_length: length });
      track("game_create_clicked", { adventure_length: length });
      location.assign(`/g/${s.code}`);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }

  async function join() {
    const c = code.trim().toUpperCase();
    if (!name.trim() || c.length < 4) return setError("Need a name and a game code.");
    setBusy(true); setError(null); remember();
    try {
      // Joining again with the same identity returns the same seat (rejoin from any device).
      const s = await client.joinGame(c, name.trim());
      track("game_join_clicked", {});
      location.assign(`/g/${s.code}`);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-8 px-4 py-10">
      <Logo />
      <p className="text-center text-lg max-w-xl text-violet-200">
        Pitch a ridiculous premise. Make <strong className="text-zap">secret</strong> decisions. Watch an AI narrator turn everyone's
        terrible ideas into one shared legend — and find out at the end <em>what everyone else was doing</em>.
      </p>

      <div className="sticker w-full max-w-md p-5">
        <div className="flex gap-2 mb-4" role="tablist">
          <button role="tab" aria-selected={mode === "create"} className={`btn flex-1 ${mode === "create" ? "btn-havoc" : "btn-ghost"}`} onClick={() => setMode("create")}>Create Game</button>
          <button role="tab" aria-selected={mode === "join"} className={`btn flex-1 ${mode === "join" ? "btn-chaos" : "btn-ghost"}`} onClick={() => setMode("join")}>Join Game</button>
        </div>
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void (mode === "create" ? create() : join()); }}>
          <label className="font-semibold">Your name
            <input className="input mt-1" maxLength={32} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Greg" autoFocus />
          </label>
          {mode === "join" ? (
            <label className="font-semibold">Game code
              <input className="input mt-1 uppercase tracking-[0.3em] font-display text-2xl" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="ABCDE" />
            </label>
          ) : (
            <fieldset>
              <legend className="font-semibold mb-1">Adventure length</legend>
              <div className="flex gap-2">
                {(["short", "medium", "long"] as const).map((l) => (
                  <label key={l} className={`btn flex-1 text-center ${length === l ? "btn-zap" : "btn-ghost"}`}>
                    <input type="radio" name="length" className="sr-only" checked={length === l} onChange={() => setLength(l)} />
                    {l}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          {error && <p className="text-chaos font-semibold" role="alert">{error}</p>}
          <button type="submit" disabled={busy} className={`btn text-2xl ${mode === "create" ? "btn-havoc" : "btn-chaos"}`}>
            {busy ? "Summoning chaos…" : mode === "create" ? "Start a Misadventure" : "Join the Chaos"}
          </button>
        </form>
      </div>
      <div className="flex items-center gap-3"><AccountButton /></div>
      <p className="text-sm text-violet-300">2–12 players · no account needed · narrated by an AI with poor judgment</p>
    </main>
  );
}
