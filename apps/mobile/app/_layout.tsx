import { Bangers_400Regular } from "@expo-google-fonts/bangers";
import { Nunito_400Regular, Nunito_700Bold, Nunito_800ExtraBold, useFonts } from "@expo-google-fonts/nunito";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AnalyticsProvider } from "../src/lib/analytics";
import { AuthProvider } from "../src/lib/auth";
import { C, F } from "../src/ui";

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  // Keys must match the family names in F (src/ui.tsx).
  const [fontsLoaded, fontError] = useFonts({
    [F.comic]: Bangers_400Regular,
    [F.regular]: Nunito_400Regular,
    [F.bold]: Nunito_700Bold,
    [F.extrabold]: Nunito_800ExtraBold,
  });
  // Hold the splash screen until fonts are ready so text never flashes in the system font.
  const ready = fontsLoaded || !!fontError;
  useEffect(() => {
    if (ready) SplashScreen.hideAsync();
  }, [ready]);
  if (!ready) return null;

  return (
    <SafeAreaProvider>
      <AuthProvider>
        <AnalyticsProvider>
          <StatusBar style="light" />
          <Stack screenOptions={{ headerStyle: { backgroundColor: C.ink2 }, headerTintColor: C.zap, contentStyle: { backgroundColor: C.ink }, headerTitleStyle: { fontFamily: F.comic, fontSize: 22 } }}>
            <Stack.Screen name="index" options={{ headerShown: false }} />
            <Stack.Screen name="game/[code]" options={{ title: "Havoc & Chaos" }} />
            <Stack.Screen name="story/[id]" options={{ title: "The Complete Story" }} />
          </Stack>
        </AnalyticsProvider>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
