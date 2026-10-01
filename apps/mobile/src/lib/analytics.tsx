/**
 * PostHog for iOS/Android. No-op without EXPO_PUBLIC_POSTHOG_KEY.
 * Touch/text autocapture stays OFF: player input here is creative writing and never leaves the game.
 */
import { PostHogProvider, usePostHog } from "posthog-react-native";
import type { ReactNode } from "react";

const KEY = process.env.EXPO_PUBLIC_POSTHOG_KEY;

export function AnalyticsProvider({ children }: { children: ReactNode }) {
  if (!KEY) return <>{children}</>;
  return (
    <PostHogProvider apiKey={KEY} options={{ host: process.env.EXPO_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com" }}
      autocapture={{ captureScreens: true, captureTouches: false }}>
      {children}
    </PostHogProvider>
  );
}

export function useTrack() {
  const posthog = KEY ? usePostHog() : null; // the provider is static per build, so hook order is stable
  return (event: string, props: Record<string, string | number | boolean> = {}) => posthog?.capture(event, props);
}
