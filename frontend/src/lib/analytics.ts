/**
 * PostHog for the web client. A no-op unless VITE_POSTHOG_KEY is set.
 * Product events only — never player-written text (themes, actions, chat, secrets).
 * VITE_POSTHOG_HOST may point at the edge Worker's /ingest reverse proxy.
 */
import posthog from "posthog-js";

const key = import.meta.env.VITE_POSTHOG_KEY as string | undefined;
let ready = false;

export function initAnalytics() {
  if (!key || ready) return;
  posthog.init(key, {
    api_host: (import.meta.env.VITE_POSTHOG_HOST as string | undefined) ?? "https://us.i.posthog.com",
    capture_pageview: "history_change",
    autocapture: false, // inputs here are creative writing; never auto-capture them
    disable_session_recording: true,
    person_profiles: "identified_only",
  });
  ready = true;
}

export function track(event: string, props: Record<string, string | number | boolean> = {}) {
  if (ready) posthog.capture(event, props);
}

export function identify(distinctId: string, props: Record<string, string> = {}) {
  if (ready && posthog.get_distinct_id() !== distinctId) posthog.identify(distinctId, props);
}
