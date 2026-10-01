import { useEffect, useState } from "react";
import { client, type SharedStory as Shared } from "../lib/api";
import { Logo, OUTCOME_STYLE, Panel } from "./common";

export function SharedStory({ shareId }: { shareId: string }) {
  const [story, setStory] = useState<Shared | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    client.shared(shareId).then((s) => { setStory(s); document.title = s.title; }).catch((e) => setError((e as Error).message));
  }, [shareId]);
  if (error) return <main className="p-10 text-center"><Logo /><p className="mt-6 text-chaos text-xl">{error}</p></main>;
  if (!story) return <main className="p-10 text-center"><Logo /><p className="mt-6">Unrolling the scroll…</p></main>;
  const style = OUTCOME_STYLE[story.outcome.kind] ?? OUTCOME_STYLE.partial_success;
  return (
    <main className="max-w-4xl mx-auto px-4 py-8 flex flex-col gap-6">
      <Logo size="sm" />
      <header className="text-center">
        <h1 className="font-display text-5xl text-zap -rotate-1">{story.title}</h1>
        <p className={`font-display text-3xl mt-3 ${style.color}`}>{style.emoji} {story.outcome.headline}</p>
        <p className="mt-2">Starring {story.players.map((p) => `${p.avatar} ${p.name}`).join(", ")}</p>
      </header>
      <div className="sticker-paper p-6 sm:p-10">
        {story.chapters.map((c) => (
          <section key={c.index} className="mb-8">
            <h2 className="font-display text-3xl mb-2 text-[#c2410c]">{c.title}</h2>
            {c.text.split(/\n\s*\n/).map((p, i) => <p key={i} className="font-type leading-relaxed mb-3">{p}</p>)}
          </section>
        ))}
      </div>
      <Panel title="🏆 Achievements">
        <ul className="space-y-1">{Object.entries(story.achievements).map(([name, list]) => (
          <li key={name}><b>{name}:</b> {list.map((a) => `${a.emoji} ${a.title}`).join(" · ")}</li>
        ))}</ul>
      </Panel>
      <div className="flex gap-3 justify-center flex-wrap">
        <a className="btn btn-ghost" href={`/api/share/${shareId}/story.txt`} download>Download as text</a>
        <a className="btn btn-havoc" href="/">Start your own misadventure</a>
      </div>
    </main>
  );
}
