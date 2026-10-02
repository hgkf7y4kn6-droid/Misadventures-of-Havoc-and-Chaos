import '@/global.css';

import {
  PlusJakartaSans_400Regular,
  PlusJakartaSans_500Medium,
  PlusJakartaSans_600SemiBold,
  PlusJakartaSans_700Bold,
  PlusJakartaSans_800ExtraBold,
  useFonts,
} from '@expo-google-fonts/plus-jakarta-sans';
import { DarkTheme, DefaultTheme, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useColorScheme } from 'react-native';

import { AnimatedSplashOverlay } from '@/components/animated-icon';
import AppTabs from '@/components/app-tabs';

SplashScreen.preventAutoHideAsync();

export default function TabLayout() {
  const colorScheme = useColorScheme();
  // Keys are the family names the theme's --font-sans-* variables point to (src/global.css).
  const [fontsLoaded, fontError] = useFonts({
    'sans-regular': PlusJakartaSans_400Regular,
    'sans-medium': PlusJakartaSans_500Medium,
    'sans-semibold': PlusJakartaSans_600SemiBold,
    'sans-bold': PlusJakartaSans_700Bold,
    'sans-extrabold': PlusJakartaSans_800ExtraBold,
  });

  // Keep the splash screen up until the fonts are ready so text never flashes in the system font.
  if (!fontsLoaded && !fontError) return null;

  return (
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      <AnimatedSplashOverlay />
      <AppTabs />
    </ThemeProvider>
  );
}
