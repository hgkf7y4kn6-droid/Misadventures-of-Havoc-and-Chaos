import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View, type TextInputProps, type ViewStyle } from "react-native";

export const C = {
  // "Warm Riot": warm hues lead to prime laughs; sky is the one cool color, saved for secrets.
  ink: "#2b0f1e", ink2: "#3d1830", ink3: "#552241", havoc: "#ff6a1f", chaos: "#ff3f8e", zap: "#ffd23f",
  slime: "#a8e63c", sky: "#4dd8ff", paper: "#fff3d6", text: "#fff1e3", muted: "#f0b9a6", peach: "#ffc9a8",
};

const TONES = { havoc: C.havoc, chaos: C.chaos, zap: C.zap, ghost: C.ink3, slime: C.slime } as const;

export function Button({ title, onPress, tone = "havoc", disabled, busy }: { title: string; onPress: () => void; tone?: keyof typeof TONES; disabled?: boolean; busy?: boolean }) {
  return (
    <Pressable accessibilityRole="button" accessibilityState={{ disabled: !!disabled }} disabled={disabled || busy} onPress={onPress}
      style={({ pressed }) => [s.btn, { backgroundColor: TONES[tone], opacity: disabled ? 0.45 : 1, transform: [{ translateY: pressed ? 2 : 0 }] }]}>
      {busy ? <ActivityIndicator color="#000" /> : <Text style={[s.btnText, { color: tone === "ghost" ? "#fff" : C.ink }]}>{title}</Text>}
    </Pressable>
  );
}

export function Card({ children, title, paper, style }: { children: ReactNode; title?: string; paper?: boolean; style?: ViewStyle }) {
  return (
    <View style={[s.card, paper && { backgroundColor: C.paper }, style]}>
      {title ? <Text accessibilityRole="header" style={[s.cardTitle, paper && { color: C.ink }]}>{title}</Text> : null}
      {children}
    </View>
  );
}

export function Input(props: TextInputProps) {
  return <TextInput placeholderTextColor="#b07a8a" {...props} style={[s.input, props.style]} />;
}

export function T({ children, style, muted, paper }: { children: ReactNode; style?: object; muted?: boolean; paper?: boolean }) {
  return <Text style={[{ color: paper ? C.ink : muted ? C.muted : C.text, fontSize: 16, lineHeight: 23 }, style]}>{children}</Text>;
}

export function H({ children, color = C.zap, size = 32 }: { children: ReactNode; color?: string; size?: number }) {
  return <Text accessibilityRole="header" style={{ color, fontSize: size, fontWeight: "900", letterSpacing: 0.5, transform: [{ rotate: "-1.5deg" }] }}>{children}</Text>;
}

/** Accessible 0..1 control (adjustable role: VoiceOver/TalkBack swipe up/down). */
export function Stepper({ label, value, onChange, step = 0.1 }: { label: string; value: number; onChange: (v: number) => void; step?: number }) {
  const clamp = (v: number) => Math.round(Math.max(0, Math.min(1, v)) * 100) / 100;
  return (
    <View accessible accessibilityRole="adjustable" accessibilityLabel={label} accessibilityValue={{ min: 0, max: 100, now: Math.round(value * 100) }}
      onAccessibilityAction={(e) => onChange(clamp(value + (e.nativeEvent.actionName === "increment" ? step : -step)))}
      accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
      style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
      <Text style={{ color: C.muted }}>{label}</Text>
      <Button tone="ghost" title="−" onPress={() => onChange(clamp(value - step))} />
      <Text style={{ color: C.text, fontWeight: "800", minWidth: 44, textAlign: "center" }}>{Math.round(value * 100)}%</Text>
      <Button tone="ghost" title="+" onPress={() => onChange(clamp(value + step))} />
    </View>
  );
}

const s = StyleSheet.create({
  btn: { borderWidth: 3, borderColor: "#000", borderRadius: 14, paddingVertical: 12, paddingHorizontal: 16, alignItems: "center", shadowColor: "#000", shadowOffset: { width: 4, height: 4 }, shadowOpacity: 1, shadowRadius: 0, elevation: 4 },
  btnText: { fontWeight: "900", fontSize: 17, letterSpacing: 0.5 },
  card: { backgroundColor: C.ink2, borderWidth: 3, borderColor: "#000", borderRadius: 16, padding: 14, gap: 8, shadowColor: "#000", shadowOffset: { width: 5, height: 5 }, shadowOpacity: 1, shadowRadius: 0, elevation: 5 },
  cardTitle: { color: C.text, fontSize: 22, fontWeight: "900" },
  input: { backgroundColor: "#1f0a15", borderWidth: 3, borderColor: "#000", borderRadius: 12, padding: 12, color: "#fff", fontSize: 16 },
});
