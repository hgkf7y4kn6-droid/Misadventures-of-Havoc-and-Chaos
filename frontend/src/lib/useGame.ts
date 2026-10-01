import { useCallback, useEffect, useRef, useState } from "react";
import { GameSocket, type ClientMessage, type SocketStatus } from "@havoc/client";
import { client } from "./api";
import { identify } from "./analytics";
import type { Character, GameEvent, GameView } from "./types";

export type ConnState = SocketStatus;

export interface Toast { id: number; text: string; tone: "info" | "error" | "secret" }

const EVENT_TOASTS: Record<string, (d: Record<string, unknown>) => string | null> = {
  PRIVATE_INFORMATION: () => "🤫 You learned something nobody else knows.",
  RANDOM_EVENT: (d) => `🎲 ${String(d.title ?? "Something happened")}`,
  GROUP_FORMED: () => "👥 You've been grouped with others. Group chat is open.",
  FINAL_CHALLENGE_STARTED: () => "⚔️ The final challenge has begun!",
  PLAYER_KICKED: (d) => `👢 ${String(d.name ?? "Someone")} was removed.`,
  FINAL_STORY_GENERATED: () => "📖 The complete story is ready.",
};

export function useGame(code: string | null) {
  const [view, setView] = useState<GameView | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [suggestion, setSuggestion] = useState<Character | null>(null);
  const socket = useRef<GameSocket | null>(null);

  const toast = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 5000 : 3500);
  }, []);

  useEffect(() => {
    if (!code) return;
    const sock = new GameSocket(client, code, {
      onStatus: setConn,
      onMessage: (msg) => {
        if (msg.type === "state") {
          setView(msg.state);
          identify(msg.state.me.id, { game_id: msg.state.game_id });
        } else if (msg.type === "event") {
          setEvents((list) => [...list.slice(-50), msg]);
          const t = EVENT_TOASTS[msg.event]?.(msg.data ?? {});
          if (t) toast(t, msg.event === "PRIVATE_INFORMATION" ? "secret" : "info");
        } else if (msg.type === "error") {
          toast(msg.message ?? "Something went wrong", "error");
        } else if (msg.type === "character_suggestion") {
          setSuggestion(msg.character);
        }
      },
    }).start();
    socket.current = sock;
    return () => sock.stop();
  }, [code, toast]);

  const send = useCallback((msg: Record<string, unknown>) => {
    const ok = socket.current?.send(msg as ClientMessage) ?? false;
    if (!ok) toast("Reconnecting… try again in a second.", "error");
    return ok;
  }, [toast]);

  return { view, conn, send, toasts, events, toast, suggestion, clearSuggestion: () => setSuggestion(null) };
}

/** Seconds remaining until a unix-seconds deadline, ticking every second. */
export function useCountdown(deadline: number | null | undefined) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!deadline) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [deadline]);
  return deadline ? Math.max(0, Math.round(deadline - now)) : null;
}
