import '@/global.css';

import { Bangers_400Regular } from '@expo-google-fonts/bangers';
import {
  Nunito_400Regular,
  Nunito_500Medium,
  Nunito_600SemiBold,
  Nunito_700Bold,
  Nunito_800ExtraBold,
  useFonts,
} from '@expo-google-fonts/nunito';
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
    comic: Bangers_400Regular,
    'sans-regular': Nunito_400Regular,
    'sans-medium': Nunito_500Medium,
    'sans-semibold': Nunito_600SemiBold,
    'sans-bold': Nunito_700Bold,
    'sans-extrabold': Nunito_800ExtraBold,
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
