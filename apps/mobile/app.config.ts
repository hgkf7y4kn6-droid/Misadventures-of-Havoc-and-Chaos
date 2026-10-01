import type { ExpoConfig } from "expo/config";

// Build-time configuration. Values come from EAS environment variables (eas.json "env" or `eas env`).
const variant = process.env.APP_VARIANT ?? "development"; // development | preview | production
const suffix = variant === "production" ? "" : `.${variant}`;

const config: ExpoConfig = {
  name: variant === "production" ? "Havoc & Chaos" : `Havoc & Chaos (${variant})`,
  description: "The Misadventures of Havoc and Chaos — secret decisions, shared disasters, one legend.",
  slug: "havoc-and-chaos",
  scheme: "havoc",
  version: "1.0.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  userInterfaceStyle: "dark",
  backgroundColor: "#1a1033",
  runtimeVersion: { policy: "appVersion" },
  updates: process.env.EAS_PROJECT_ID ? { url: `https://u.expo.dev/${process.env.EAS_PROJECT_ID}` } : undefined,
  ios: {
    bundleIdentifier: `com.havocandchaos.app${suffix}`,
    supportsTablet: true,
    infoPlist: { UIBackgroundModes: ["audio"] }, // the final story keeps narrating with the screen locked
  },
  android: {
    package: `com.havocandchaos.app${suffix}`,
    adaptiveIcon: {
      backgroundColor: "#1a1033",
      foregroundImage: "./assets/android-icon-foreground.png",
      backgroundImage: "./assets/android-icon-background.png",
      monochromeImage: "./assets/android-icon-monochrome.png",
    },
  },
  web: { favicon: "./assets/favicon.png" },
  plugins: [
    "expo-router",
    "expo-secure-store",
    ["expo-audio", { microphonePermission: false }],
    "expo-web-browser",
    "@clerk/expo",
    "expo-localization",
  ],
  experiments: { typedRoutes: true },
  extra: {
    eas: process.env.EAS_PROJECT_ID ? { projectId: process.env.EAS_PROJECT_ID } : undefined,
  },
};

export default config;
