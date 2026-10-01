/**
 * Native read-aloud: server-rendered chapter audio via expo-audio (works with the screen locked),
 * or on-device speech via expo-speech when the deployment narrates client-side.
 * Playback is per player; nothing is synchronised between devices.
 */
import type { Chapter } from "@havoc/protocol";
import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from "expo-audio";
import * as Speech from "expo-speech";
import { useEffect, useMemo, useState } from "react";
import { Platform } from "react-native";

export type NarratorStatus = "idle" | "loading" | "playing" | "paused" | "error";
export interface NarratorState { status: NarratorStatus; chapter: number; progress: number; error?: string }

const STYLE = {
  dramatic: { rate: 0.9, pitch: 0.8 }, comedic: { rate: 1.05, pitch: 1.15 }, storyteller: { rate: 1, pitch: 1 },
  chaotic: { rate: 1.2, pitch: 1.3 }, deadpan: { rate: 0.92, pitch: 0.75 },
} as const;

function chunks(text: string, max = Math.min(Speech.maxSpeechInputLength || 4000, 1500)) {
  const out: string[] = [];
  let cur = "";
  for (const s of text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]+\s*|[^.!?]+$/g) ?? [text]) {
    if ((cur + s).length > max && cur) { out.push(cur); cur = ""; }
    cur += s;
  }
  return cur ? [...out, cur] : out;
}

class Narrator {
  state: NarratorState = { status: "idle", chapter: 0, progress: 0 };
  onChange: (s: NarratorState) => void = () => {};
  private player: AudioPlayer | null = null;
  private token = 0;
  private parts: string[] = [];
  private part = 0;

  constructor(
    private mode: "server" | "device",
    private chapters: Chapter[],
    private title: string,
    private audioUrl: (i: number) => Promise<string>,
    public voice: string,
    public volume: number,
  ) {}

  private set(p: Partial<NarratorState>) { this.state = { ...this.state, ...p }; this.onChange(this.state); }

  setVolume(v: number) { this.volume = v; if (this.player) this.player.volume = v; }

  async play(chapter = this.state.chapter) {
    this.stop(false);
    const t = ++this.token;
    this.set({ chapter, progress: 0, status: "loading", error: undefined });
    if (this.mode === "server") {
      try {
        await setAudioModeAsync({ playsInSilentMode: true, shouldPlayInBackground: true });
        const url = await this.audioUrl(chapter);
        if (t !== this.token) return;
        const p = createAudioPlayer({ uri: url });
        p.volume = this.volume;
        this.player = p;
        p.addListener("playbackStatusUpdate", (s) => {
          if (t !== this.token) return;
          if (s.duration) this.set({ progress: s.currentTime / s.duration, status: s.playing ? "playing" : this.state.status });
          if (s.didJustFinish) this.next(t);
        });
        p.play();
        this.set({ status: "playing" });
      } catch {
        if (t === this.token) this.set({ status: "error", error: "Narration isn't available. The story is all here to read." });
      }
    } else {
      const c = this.chapters[chapter];
      this.parts = chunks(`${chapter === 0 ? this.title + ". " : ""}${c.title}. ${c.text}`);
      this.part = 0;
      this.set({ status: "playing" });
      this.speak(t);
    }
  }

  private speak(t: number) {
    if (t !== this.token) return;
    if (this.part >= this.parts.length) return this.next(t);
    const style = STYLE[this.voice as keyof typeof STYLE] ?? STYLE.storyteller;
    Speech.speak(this.parts[this.part], {
      ...style, volume: this.volume, language: "en-US",
      onDone: () => { if (t !== this.token) return; this.part++; this.set({ progress: this.part / this.parts.length }); this.speak(t); },
      onError: () => { if (t === this.token) this.set({ status: "error", error: "The device narrator gave up. Keep reading!" }); },
    });
  }

  private next(t: number) {
    if (t !== this.token) return;
    if (this.state.chapter + 1 >= this.chapters.length) return this.set({ status: "idle", progress: 1 });
    void this.play(this.state.chapter + 1);
  }

  pause() {
    if (this.state.status !== "playing") return;
    if (this.player) this.player.pause();
    else if (Platform.OS === "android") { this.stop(false); this.set({ status: "paused" }); return; } // no speech pause on Android
    else void Speech.pause();
    this.set({ status: "paused" });
  }

  resume() {
    if (this.state.status !== "paused") return;
    if (this.player) { this.player.play(); this.set({ status: "playing" }); }
    else if (Platform.OS === "android") void this.play(this.state.chapter);
    else { void Speech.resume(); this.set({ status: "playing" }); }
  }

  stop(reset = true) {
    this.token++;
    this.player?.remove();
    this.player = null;
    void Speech.stop();
    if (reset) this.set({ status: "idle", progress: 0 });
  }
}

export function useNarrator(opts: { mode: "server" | "device"; chapters: Chapter[]; title: string; audioUrl: (i: number) => Promise<string>; voice: string; volume: number; enabled: boolean }) {
  const narrator = useMemo(
    () => new Narrator(opts.mode, opts.chapters, opts.title, opts.audioUrl, opts.voice, opts.volume),
    [opts.mode, opts.chapters, opts.title], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const [state, setState] = useState(narrator.state);
  useEffect(() => { narrator.onChange = setState; return () => narrator.stop(); }, [narrator]);
  useEffect(() => { narrator.setVolume(opts.volume); }, [narrator, opts.volume]);
  useEffect(() => { narrator.voice = opts.voice; }, [narrator, opts.voice]);
  // Turning narration off stops it immediately; turning it back on never auto-plays.
  useEffect(() => { if (!opts.enabled) narrator.stop(); }, [narrator, opts.enabled]);
  return { state, narrator };
}
