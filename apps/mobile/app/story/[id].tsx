import type { SharedStory } from "@havoc/protocol";
import { useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { ScrollView } from "react-native";
import { client } from "../../src/lib/client";
import { C, Card, H, T } from "../../src/ui";

export default function SharedStoryScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [story, setStory] = useState<SharedStory | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { client.shared(id).then(setStory).catch((e) => setError((e as Error).message)); }, [id]);
  return (
    <ScrollView style={{ backgroundColor: C.ink }} contentContainerStyle={{ padding: 16, gap: 14 }}>
      {error ? <T>{error}</T> : !story ? <T>Unrolling the scroll…</T> : (
        <>
          <H size={28}>{story.title}</H>
          <T style={{ color: C.slime, fontWeight: "800" }}>{story.outcome.headline}</T>
          {story.chapters.map((c) => (
            <Card key={c.index} paper title={c.title}>
              {c.text.split(/\n\s*\n/).map((p, i) => <T paper key={i}>{p}</T>)}
            </Card>
          ))}
        </>
      )}
    </ScrollView>
  );
}
