import { useEffect } from "react";
import { sessions, type Session } from "../lib/api";
import { useGame } from "../lib/useGame";
import { Adventure } from "./Adventure";
import { Logo, Toasts } from "./common";
import { Ending, StoryGenerating } from "./Ending";
import { Lobby } from "./Lobby";
import { CharacterCreation, ObjectiveReveal, ThemeSubmission, ThemeVoting } from "./Setup";

const PHASE_LABEL: Record<string, string> = {
  lobby: "Lobby", theme_submission: "Pitching", theme_voting: "Voting", objective_reveal: "The Objective",
  character_creation: "Character Creation", adventure: "Adventure", final_challenge: "Final Challenge",
  story_generation: "Writing the Legend", ended: "The End",
};

export function Game({ session }: { session: Session }) {
  const { view, conn, send, toasts, suggestion, clearSuggestion } = useGame(session);

  useEffect(() => {
    if (view) document.title = `${PHASE_LABEL[view.phase] ?? ""} · The Misadventures of Havoc and Chaos`;
  }, [view?.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  if (conn === "kicked" || conn === "invalid") {
    sessions.remove(session.code);
    return (
      <main className="p-10 text-center flex flex-col gap-4 items-center">
        <Logo />
        <p className="text-xl">{conn === "kicked" ? "The host removed you from this misadventure. Rude, but legal." : "That game session isn't valid anymore."}</p>
        <a href="/" className="btn btn-havoc">Back to the start</a>
      </main>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b-[3px] border-black bg-ink-2/80 backdrop-blur sticky top-0 z-40">
        <Logo size="sm" />
        <div className="flex items-center gap-3 text-sm">
          {view && <span className="font-display text-lg text-zap tracking-widest" title="Game code">{view.code}</span>}
          {view && <span className="hidden sm:inline text-violet-300">{PHASE_LABEL[view.phase]}</span>}
          <span className={`w-3 h-3 rounded-full ${conn === "open" ? "bg-slime" : "bg-chaos animate-pulse"}`} title={conn} aria-label={`Connection ${conn}`} />
        </div>
      </header>
      <main className="px-3 sm:px-6 py-6">
        {!view ? <p className="text-center font-display text-3xl mt-20 animate-wobble">Loading chaos…</p> : (
          <>
            {view.phase === "lobby" && <Lobby view={view} send={send} />}
            {view.phase === "theme_submission" && <ThemeSubmission view={view} send={send} />}
            {view.phase === "theme_voting" && <ThemeVoting view={view} send={send} />}
            {view.phase === "objective_reveal" && <ObjectiveReveal view={view} send={send} />}
            {view.phase === "character_creation" && <CharacterCreation view={view} send={send} suggestion={suggestion} clearSuggestion={clearSuggestion} />}
            {(view.phase === "adventure" || view.phase === "final_challenge") && <Adventure view={view} send={send} />}
            {view.phase === "story_generation" && <StoryGenerating view={view} />}
            {view.phase === "ended" && <Ending view={view} send={send} session={session} />}
          </>
        )}
      </main>
      <Toasts toasts={toasts} />
    </div>
  );
}
