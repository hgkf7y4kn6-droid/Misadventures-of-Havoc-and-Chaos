/**
 * Read-aloud engine for the final story.
 *
 * Two backends behind one interface:
 *  - "server": the backend's TTS provider renders one MP3 per chapter (cached); chapters play sequentially.
 *  - "browser": the Web Speech API reads sentence-sized chunks locally (no server audio needed).
 *
 * Playback is entirely per-player: nothing here is synchronised with other players.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { Chapter } from "./types";

export type NarratorStatus = "idle" | "loading" | "playing" | "paused" | "error" | "unsupported";

export interface NarratorState {
  status: NarratorStatus;
  chapter: number;
  chapterProgress: number; // 0..1 within the current chapter
  error?: string;
}

export interface NarratorOptions {
  mode: "server" | "browser";
  chapters: Chapter[];
  title: string;
  /** Resolves a playable URL (it may need a fresh short-lived ticket). */
  audioUrl: (chapter: number) => Promise<string>;
  voiceStyle: string;
  volume: number;
}

const STYLE_SPEECH: Record<string, { rate: number; pitch: number }> = {
  dramatic: { rate: 0.9, pitch: 0.8 },
  comedic: { rate: 1.05, pitch: 1.15 },
  storyteller: { rate: 0.98, pitch: 1.0 },
  chaotic: { rate: 1.25, pitch: 1.3 },
  deadpan: { rate: 0.92, pitch: 0.7 },
};

export function chunkText(text: string, max = 220): string[] {
  const sentences = text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g) ?? [text];
  const out: string[] = [];
  let cur = "";
  for (const s of sentences) {
    if ((cur + s).length > max && cur) { out.push(cur.trim()); cur = ""; }
    cur += s;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

class Narrator {
  private opts: NarratorOptions;
  private audio: HTMLAudioElement | null = null;
  private chunks: string[] = [];
  private chunkIndex = 0;
  private token = 0; // invalidates callbacks from stopped playback
  state: NarratorState = { status: "idle", chapter: 0, chapterProgress: 0 };
  onChange: (s: NarratorState) => void = () => {};

  constructor(opts: NarratorOptions) {
    this.opts = opts;
    if (opts.mode === "browser" && typeof window !== "undefined" && !("speechSynthesis" in window)) {
      this.state = { ...this.state, status: "unsupported" };
    }
  }

  private set(patch: Partial<NarratorState>) {
    this.state = { ...this.state, ...patch };
    this.onChange(this.state);
  }

  update(opts: Partial<NarratorOptions>) {
    const volumeChanged = opts.volume !== undefined && opts.volume !== this.opts.volume;
    this.opts = { ...this.opts, ...opts };
    if (volumeChanged && this.audio) this.audio.volume = this.opts.volume;
    // Speech volume applies per utterance; restart the current sentence so the change is immediate.
    if (volumeChanged && this.opts.mode === "browser" && this.state.status === "playing") {
      this.token++;
      speechSynthesis.cancel();
      this.speakChunk(this.token);
    }
  }

  play(chapter = this.state.chapter) {
    if (this.state.status === "unsupported" || !this.opts.chapters.length) return;
    this.stopInternal();
    const t = ++this.token;
    this.set({ chapter, chapterProgress: 0, status: "loading", error: undefined });
    if (this.opts.mode === "server") void this.playServer(chapter, t);
    else this.playBrowser(chapter, t);
  }

  pause() {
    if (this.state.status !== "playing") return;
    if (this.opts.mode === "server") this.audio?.pause();
    else speechSynthesis.pause();
    this.set({ status: "paused" });
  }

  resume() {
    if (this.state.status !== "paused") return;
    if (this.opts.mode === "server") void this.audio?.play();
    else speechSynthesis.resume();
    this.set({ status: "playing" });
  }

  stop() {
    this.stopInternal();
    this.set({ status: "idle", chapterProgress: 0 });
  }

  restart() {
    this.play(0);
  }

  destroy() {
    this.stopInternal();
    this.onChange = () => {};
  }

  private stopInternal() {
    this.token++;
    if (this.audio) {
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
      this.audio = null;
    }
    if (this.opts.mode === "browser" && typeof window !== "undefined" && "speechSynthesis" in window) speechSynthesis.cancel();
  }

  private next(t: number) {
    if (t !== this.token) return;
    const nextChapter = this.state.chapter + 1;
    if (nextChapter >= this.opts.chapters.length) {
      this.set({ status: "idle", chapterProgress: 1 });
      return;
    }
    this.play(nextChapter);
  }

  private async playServer(chapter: number, t: number) {
    let url: string;
    try { url = await this.opts.audioUrl(chapter); } catch {
      if (t === this.token) this.set({ status: "error", error: "Couldn't reach the narrator. You can keep reading!" });
      return;
    }
    if (t !== this.token) return;
    const audio = new Audio(url);
    audio.preload = "auto";
    audio.volume = this.opts.volume;
    this.audio = audio;
    audio.ontimeupdate = () => {
      if (t === this.token && audio.duration) this.set({ chapterProgress: audio.currentTime / audio.duration });
    };
    audio.onplaying = () => t === this.token && this.set({ status: "playing" });
    audio.onended = () => this.next(t);
    audio.onerror = () => {
      if (t !== this.token) return;
      this.set({ status: "error", error: "Narration for this chapter isn't available. You can keep reading!" });
    };
    // Warm the cache for the next chapter while this one plays.
    if (chapter + 1 < this.opts.chapters.length) void this.opts.audioUrl(chapter + 1).then((u) => fetch(u)).catch(() => {});
    audio.play().catch(() => t === this.token && this.set({ status: "error", error: "Tap play to start narration." }));
  }

  private playBrowser(chapter: number, t: number) {
    const ch = this.opts.chapters[chapter];
    const intro = chapter === 0 ? `${this.opts.title}. ` : "";
    this.chunks = chunkText(`${intro}${ch.title}. ${ch.text}`);
    this.chunkIndex = 0;
    this.set({ status: "playing" });
    this.speakChunk(t);
  }

  private speakChunk(t: number) {
    if (t !== this.token) return;
    if (this.chunkIndex >= this.chunks.length) return this.next(t);
    const u = new SpeechSynthesisUtterance(this.chunks[this.chunkIndex]);
    const style = STYLE_SPEECH[this.opts.voiceStyle] ?? STYLE_SPEECH.storyteller;
    u.rate = style.rate;
    u.pitch = style.pitch;
    u.volume = this.opts.volume;
    u.lang = "en-US";
    const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
    if (voices.length) u.voice = voices[Math.abs(hash(this.opts.voiceStyle)) % voices.length];
    u.onend = () => {
      if (t !== this.token) return;
      this.chunkIndex++;
      this.set({ chapterProgress: this.chunkIndex / this.chunks.length });
      this.speakChunk(t);
    };
    u.onerror = (e) => {
      if (t !== this.token || e.error === "interrupted" || e.error === "canceled") return;
      this.set({ status: "error", error: "Your browser's narrator gave up. The text is all still here!" });
    };
    speechSynthesis.speak(u);
  }
}

function hash(s: string) {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0;
  return h;
}

export function useNarrator(opts: NarratorOptions, enabled: boolean) {
  const narrator = useMemo(() => new Narrator(opts), [opts.mode, opts.chapters, opts.title]); // eslint-disable-line react-hooks/exhaustive-deps
  const [state, setState] = useState<NarratorState>(narrator.state);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  useEffect(() => {
    narrator.onChange = setState;
    setState(narrator.state);
    return () => narrator.destroy();
  }, [narrator]);

  useEffect(() => {
    narrator.update({ volume: opts.volume, voiceStyle: opts.voiceStyle, audioUrl: opts.audioUrl });
  }, [narrator, opts.volume, opts.voiceStyle, opts.audioUrl]);

  // Turning audio off stops playback immediately; turning it back on never auto-plays.
  useEffect(() => {
    if (!enabled) narrator.stop();
  }, [enabled, narrator]);

  return { state, narrator };
}
