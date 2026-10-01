import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "./api";
import type { Character, GameEvent, GameView } from "./types";

export type ConnState = "connecting" | "open" | "closed" | "kicked" | "invalid";

export interface Toast { id: number; text: string; tone: "info" | "error" | "secret" }

const EVENT_TOASTS: Record<string, (d: Record<string, unknown>) => string | null> = {
  PRIVATE_INFORMATION: () => "🤫 You learned something nobody else knows.",
  RANDOM_EVENT: (d) => `🎲 ${String(d.title ?? "Something happened")}`,
  GROUP_FORMED: () => "👥 You've been grouped with others. Group chat is open.",
  FINAL_CHALLENGE_STARTED: () => "⚔️ The final challenge has begun!",
  PLAYER_KICKED: (d) => `👢 ${String(d.name ?? "Someone")} was removed.`,
  FINAL_STORY_GENERATED: () => "📖 The complete story is ready.",
};

export function useGame(session: Session | null) {
  const [view, setView] = useState<GameView | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [suggestion, setSuggestion] = useState<Character | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const retry = useRef(0);
  const alive = useRef(true);

  const toast = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 5000 : 3500);
  }, []);

  useEffect(() => {
    if (!session) return;
    alive.current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      setConn("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const sock = new WebSocket(`${proto}://${location.host}/ws/${session.code}?token=${encodeURIComponent(session.token)}`);
      ws.current = sock;
      sock.onopen = () => { setConn("open"); retry.current = 0; };
      sock.onmessage = (e) => {
        let msg: { type: string; [k: string]: unknown };
        try { msg = JSON.parse(e.data); } catch { return; }
        if (msg.type === "state") setView(msg.state as GameView);
        else if (msg.type === "event") {
          const ev = msg as unknown as GameEvent & { type: string };
          setEvents((list) => [...list.slice(-50), ev]);
          const t = EVENT_TOASTS[ev.event]?.(ev.data ?? {});
          if (t) toast(t, ev.event === "PRIVATE_INFORMATION" ? "secret" : "info");
        } else if (msg.type === "error") {
          if (msg.fatal) { setConn("invalid"); alive.current = false; }
          toast(String(msg.message ?? "Something went wrong"), "error");
        } else if (msg.type === "kicked") { setConn("kicked"); alive.current = false; }
        else if (msg.type === "character_suggestion") setSuggestion(msg.character as Character);
      };
      sock.onclose = (e) => {
        if (e.code === 4003) setConn("kicked");
        if (!alive.current || e.code === 4003 || e.code === 4004) { if (e.code === 4004) setConn("invalid"); return; }
        setConn("closed");
        const delay = Math.min(10000, 500 * 2 ** retry.current++);
        timer = setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      alive.current = false;
      if (timer) clearTimeout(timer);
      ws.current?.close();
    };
  }, [session, toast]);

  const send = useCallback((msg: Record<string, unknown>) => {
    const sock = ws.current;
    if (!sock || sock.readyState !== WebSocket.OPEN) {
      toast("Reconnecting… try again in a second.", "error");
      return false;
    }
    sock.send(JSON.stringify(msg));
    return true;
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
