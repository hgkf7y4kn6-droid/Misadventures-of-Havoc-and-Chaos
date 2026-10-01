import { router } from "expo-router";
import { useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useTrack } from "../src/lib/analytics";
import { AccountButton } from "../src/lib/auth";
import { client } from "../src/lib/client";
import { Button, C, Card, Input, T } from "../src/ui";

export default function Home() {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"create" | "join" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const track = useTrack();

  async function go(kind: "create" | "join") {
    if (!name.trim()) return setError("Every legend needs a name. Even a bad one.");
    if (kind === "join" && code.trim().length < 4) return setError("Enter the game code.");
    setBusy(kind); setError(null);
    try {
      const seat = kind === "create" ? await client.createGame(name.trim()) : await client.joinGame(code.trim(), name.trim());
      track(kind === "create" ? "game_create_clicked" : "game_join_clicked", { platform: Platform.OS });
      router.push(`/game/${seat.code}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: C.ink }}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: 20, gap: 18, flexGrow: 1, justifyContent: "center" }} keyboardShouldPersistTaps="handled">
          <View accessible accessibilityRole="header" accessibilityLabel="The Misadventures of Havoc and Chaos" style={{ alignItems: "center" }}>
            <Text style={{ color: C.zap, fontSize: 22, fontWeight: "800", transform: [{ rotate: "-2deg" }] }}>The Misadventures of</Text>
            <Text style={{ fontSize: 52, fontWeight: "900", transform: [{ rotate: "-3deg" }] }}>
              <Text style={{ color: C.havoc }}>Havoc</Text><Text style={{ color: "#fff" }}> & </Text><Text style={{ color: C.chaos }}>Chaos</Text>
            </Text>
          </View>
          <T muted style={{ textAlign: "center" }}>Pitch a ridiculous premise. Make secret decisions. Find out at the end what everyone else was doing.</T>
          <Card>
            <T>Your name</T>
            <Input value={name} onChangeText={setName} maxLength={32} placeholder="e.g. Greg" accessibilityLabel="Your name" autoCapitalize="words" />
            <Button title="Start a Misadventure" onPress={() => void go("create")} busy={busy === "create"} />
            <T muted style={{ textAlign: "center" }}>— or join with a code —</T>
            <Input value={code} onChangeText={(v) => setCode(v.toUpperCase())} maxLength={6} placeholder="ABCDE" autoCapitalize="characters"
              accessibilityLabel="Game code" style={{ letterSpacing: 6, fontSize: 24, textAlign: "center", fontWeight: "900" }} />
            <Button tone="chaos" title="Join the Chaos" onPress={() => void go("join")} busy={busy === "join"} />
            {error ? <Text accessibilityRole="alert" style={{ color: C.chaos, fontWeight: "700" }}>{error}</Text> : null}
          </Card>
          <AccountButton />
          <T muted style={{ textAlign: "center", fontSize: 13 }}>2–12 players · no account needed</T>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
