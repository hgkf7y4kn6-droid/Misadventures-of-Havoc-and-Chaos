import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AnalyticsProvider } from "../src/lib/analytics";
import { AuthProvider } from "../src/lib/auth";
import { C } from "../src/ui";

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <AnalyticsProvider>
          <StatusBar style="light" />
          <Stack screenOptions={{ headerStyle: { backgroundColor: C.ink2 }, headerTintColor: C.zap, contentStyle: { backgroundColor: C.ink }, headerTitleStyle: { fontWeight: "900" } }}>
            <Stack.Screen name="index" options={{ headerShown: false }} />
            <Stack.Screen name="game/[code]" options={{ title: "Havoc & Chaos" }} />
            <Stack.Screen name="story/[id]" options={{ title: "The Complete Story" }} />
          </Stack>
        </AnalyticsProvider>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
