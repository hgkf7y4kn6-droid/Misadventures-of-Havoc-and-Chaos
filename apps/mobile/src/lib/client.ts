import { HavocClient, type KeyValueStore } from "@havoc/client";
import * as SecureStore from "expo-secure-store";

// Guest sessions and seat maps live in the device keychain / keystore.
const secureStore: KeyValueStore = {
  get: (k) => SecureStore.getItemAsync(k.replace(/[^A-Za-z0-9._-]/g, "_")),
  set: (k, v) => SecureStore.setItemAsync(k.replace(/[^A-Za-z0-9._-]/g, "_"), v),
  remove: (k) => SecureStore.deleteItemAsync(k.replace(/[^A-Za-z0-9._-]/g, "_")),
};

export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8787";
export const client = new HavocClient({ baseUrl: API_URL, storage: secureStore });
