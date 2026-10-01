import type { ReactNode } from "react";
import { useCountdown, type Toast } from "../lib/useGame";
import type { PublicPlayer } from "../lib/types";

export function Logo({ size = "lg" }: { size?: "sm" | "lg" }) {
  if (size === "sm") {
    return (
      <a href="/" className="font-display text-xl leading-none text-zap no-underline" aria-label="The Misadventures of Havoc and Chaos — home">
        <span className="text-sky-300">The Misadventures of</span>{" "}
        <span className="text-havoc">Havoc</span> <span className="text-white">&amp;</span> <span className="text-chaos">Chaos</span>
      </a>
    );
  }
  return (
    <h1 className="text-center select-none" aria-label="The Misadventures of Havoc and Chaos">
      <span className="block font-display text-2xl sm:text-4xl text-zap -rotate-2" aria-hidden>The Misadventures of</span>
      <span className="block font-display text-6xl sm:text-8xl leading-[0.9] -rotate-3 animate-wobble drop-shadow-[5px_5px_0_#000]" aria-hidden>
        <span className="text-havoc">Havoc</span> <span className="text-white">&amp;</span> <span className="text-chaos">Chaos</span>
      </span>
    </h1>
  );
}

export function Timer({ deadline, label = "Time left" }: { deadline: number | null | undefined; label?: string }) {
  const secs = useCountdown(deadline);
  if (secs === null) return null;
  const urgent = secs <= 10;
  return (
    <div className={`sticker px-3 py-1 font-display text-lg ${urgent ? "text-chaos animate-shake" : "text-zap"}`} role="timer" aria-live={urgent ? "assertive" : "off"}>
      ⏱ {label}: {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")}
    </div>
  );
}

export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm" aria-live="polite" role="status">
      {toasts.map((t) => (
        <div key={t.id} className={`sticker animate-pop px-4 py-2 font-semibold ${t.tone === "error" ? "bg-chaos! text-white" : t.tone === "secret" ? "bg-[#3b1f6e]! text-zap" : ""}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function Avatar({ p, size = 40 }: { p: Pick<PublicPlayer, "character" | "name">; size?: number }) {
  return (
    <span
      className="inline-flex items-center justify-center rounded-full border-[3px] border-black shrink-0"
      style={{ width: size, height: size, background: p.character.color, fontSize: size * 0.55 }}
      aria-hidden
    >
      {p.character.avatar}
    </span>
  );
}

export function Panel({ title, children, className = "", tone }: { title?: ReactNode; children: ReactNode; className?: string; tone?: "paper" }) {
  return (
    <section className={`${tone === "paper" ? "sticker-paper" : "sticker"} p-4 ${className}`}>
      {title && <h2 className="font-display text-2xl mb-2">{title}</h2>}
      {children}
    </section>
  );
}

export const OUTCOME_STYLE: Record<string, { emoji: string; color: string }> = {
  full_success: { emoji: "🏆", color: "text-slime" },
  partial_success: { emoji: "🤷", color: "text-zap" },
  failure: { emoji: "💥", color: "text-chaos" },
  costly_success: { emoji: "🩹", color: "text-havoc" },
  accidental_success: { emoji: "🍀", color: "text-slime" },
  success_new_problem: { emoji: "🐙", color: "text-sky" },
};
