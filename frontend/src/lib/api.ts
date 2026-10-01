import { HavocClient, type KeyValueStore } from "@havoc/client";

export type { SharedStory } from "@havoc/protocol";

const localStore: KeyValueStore = {
  async get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  async set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked: session lives in memory */ } },
  async remove(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

/**
 * Same-origin by default (Vite proxies /api and /ws in dev; the Worker or engine serves them in prod).
 * Set VITE_API_URL to point the web client at a separate edge origin.
 */
export const client = new HavocClient({ baseUrl: import.meta.env.VITE_API_URL ?? "", storage: localStore });
