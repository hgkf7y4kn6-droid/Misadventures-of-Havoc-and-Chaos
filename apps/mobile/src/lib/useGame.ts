import { GameSocket, type ClientMessage, type SocketStatus } from "@havoc/client";
import type { Character, GameView } from "@havoc/protocol";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert } from "react-native";
import { client } from "./client";

export function useGame(code: string) {
  const [view, setView] = useState<GameView | null>(null);
  const [status, setStatus] = useState<SocketStatus>("connecting");
  const [notice, setNotice] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<Character | null>(null);
  const socket = useRef<GameSocket | null>(null);

  useEffect(() => {
    const sock = new GameSocket(client, code, {
      onStatus: setStatus,
      onMessage: (m) => {
        if (m.type === "state") setView(m.state);
        else if (m.type === "error") setNotice(m.message);
        else if (m.type === "character_suggestion") setSuggestion(m.character);
        else if (m.type === "event" && m.event === "PRIVATE_INFORMATION") setNotice("🤫 You learned something nobody else knows.");
        else if (m.type === "event" && m.event === "RANDOM_EVENT") setNotice(`🎲 ${String(m.data.title ?? "Something happened")}`);
      },
    }).start();
    socket.current = sock;
    return () => sock.stop();
  }, [code]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 3500);
    return () => clearTimeout(t);
  }, [notice]);

  const send = useCallback((msg: ClientMessage) => {
    const ok = socket.current?.send(msg) ?? false;
    if (!ok) Alert.alert("Reconnecting…", "Try again in a second.");
    return ok;
  }, []);

  return { view, status, send, notice, suggestion, clearSuggestion: () => setSuggestion(null) };
}
