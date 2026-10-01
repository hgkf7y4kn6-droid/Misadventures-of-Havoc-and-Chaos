import { sessions } from "./lib/api";
import { Game } from "./components/Game";
import { Landing } from "./components/Landing";
import { SharedStory } from "./components/SharedStory";

export default function App() {
  const path = location.pathname;
  const game = path.match(/^\/g\/([A-Za-z]{4,8})\/?$/);
  if (game) {
    const code = game[1].toUpperCase();
    const session = sessions.get(code);
    return session ? <Game session={session} /> : <Landing initialCode={code} />;
  }
  const story = path.match(/^\/story\/([\w-]+)\/?$/);
  if (story) return <SharedStory shareId={story[1]} />;
  return <Landing />;
}
