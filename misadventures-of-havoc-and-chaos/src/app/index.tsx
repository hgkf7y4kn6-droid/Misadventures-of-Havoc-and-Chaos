import { Link } from 'expo-router';
import { Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

// Font vocabulary: font-sans-regular (body), font-sans-bold (headings, links), font-sans-extrabold (emphasis).
export default function HomeScreen() {
  return (
    <SafeAreaView className="flex-1 bg-paper">
      <View className="flex-1 items-center justify-center gap-md px-md">
        <Text className="text-5xl text-primary font-sans-extrabold">Home</Text>
        <Text className="text-center text-lg text-ink font-sans-regular">
          Secret decisions. Shared disasters. One legend.
        </Text>
        <Link href="/explore" className="mt-4 text-lg text-secondary font-sans-bold">
          Explore →
        </Link>
      </View>
    </SafeAreaView>
  );
}
