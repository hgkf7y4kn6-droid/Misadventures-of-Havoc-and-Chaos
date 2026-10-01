import { useEffect, useState } from "react";
import { client } from "./lib/api";
import { Game } from "./components/Game";
import { Landing } from "./components/Landing";
import { SharedStory } from "./components/SharedStory";

export default function App() {
  const path = location.pathname;
  const game = path.match(/^\/g\/([A-Za-z]{4,8})\/?$/);
  if (game) {
    return <GameRoute code={game[1].toUpperCase()} />;
  }
  const story = path.match(/^\/story\/([\w-]+)\/?$/);
  if (story) return <SharedStory shareId={story[1]} />;
  return <Landing />;
}

function GameRoute({ code }: { code: string }) {
  const [seated, setSeated] = useState<boolean | null>(null);
  useEffect(() => { void client.hasSeat(code).then(setSeated); }, [code]);
  if (seated === null) return null;
  return seated ? <Game code={code} /> : <Landing initialCode={code} />;
}
