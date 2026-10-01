import type { ClientMessage, Decision, GameView } from "@havoc/protocol";
import { Stack, router, useLocalSearchParams } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, Share, Switch, Text, View } from "react-native";
import { useTrack } from "../../src/lib/analytics";
import { client } from "../../src/lib/client";
import { useNarrator } from "../../src/lib/narrator";
import { useGame } from "../../src/lib/useGame";
import { Button, C, Card, H, Input, Stepper, T } from "../../src/ui";

type Send = (m: ClientMessage) => boolean;

export default function GameScreen() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const { view, status, send, notice, suggestion, clearSuggestion } = useGame(code);

  if (status === "kicked" || status === "invalid") {
    return (
      <View style={{ flex: 1, padding: 20, gap: 12, justifyContent: "center", backgroundColor: C.ink }}>
        <T>{status === "kicked" ? "The host removed you from this misadventure." : "You don't have a seat in this game."}</T>
        <Button title="Back" onPress={() => { void client.forgetSeat(code); router.replace("/"); }} />
      </View>
    );
  }
  if (!view) return <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: C.ink }}><ActivityIndicator color={C.zap} /></View>;

  return (
    <View style={{ flex: 1, backgroundColor: C.ink }}>
      <Stack.Screen options={{ title: `${view.code} · ${view.phase.replace(/_/g, " ")}`, headerRight: () => <Text style={{ color: status === "open" ? C.slime : C.chaos }}>●</Text> }} />
      <ScrollView contentContainerStyle={{ padding: 14, gap: 14, paddingBottom: 60 }} keyboardShouldPersistTaps="handled">
        {view.phase === "lobby" && <Lobby view={view} send={send} />}
        {view.phase === "theme_submission" && <ThemeSubmit view={view} send={send} />}
        {view.phase === "theme_voting" && <Vote view={view} send={send} />}
        {view.phase === "objective_reveal" && <Objective view={view} />}
        {view.phase === "character_creation" && <CharacterForm view={view} send={send} suggestion={suggestion} clear={clearSuggestion} />}
        {(view.phase === "adventure" || view.phase === "final_challenge") && <Adventure view={view} send={send} />}
        {view.phase === "story_generation" && <Card paper title="Reconstructing what actually happened…"><T paper>{view.outcome?.headline}</T></Card>}
        {view.phase === "ended" && <Ending view={view} send={send} code={code} />}
        {view.me.id === view.host_id && ["theme_submission", "theme_voting", "objective_reveal", "character_creation", "adventure", "final_challenge"].includes(view.phase) && (
          <Button tone="ghost" title="Host: move things along" onPress={() => send({ action: "close_phase" })} />
        )}
      </ScrollView>
      {notice ? <View accessibilityLiveRegion="polite" style={{ position: "absolute", bottom: 20, left: 16, right: 16, backgroundColor: C.ink3, borderWidth: 3, borderRadius: 12, padding: 12 }}><T>{notice}</T></View> : null}
    </View>
  );
}

function Lobby({ view, send }: { view: GameView; send: Send }) {
  const isHost = view.me.id === view.host_id;
  const ready = view.players.length >= view.settings.min_players && view.players.every((p) => p.is_host || p.ready);
  return (
    <>
      <Card title="Gather the party">
        <H size={44}>{view.code}</H>
        <Button tone="zap" title="Share invite" onPress={() => void Share.share({ message: `Join my misadventure in Havoc & Chaos! Code: ${view.code}` })} />
      </Card>
      <Card title={`Players (${view.players.length}/${view.settings.max_players})`}>
        {view.players.map((p) => (
          <T key={p.id}>{p.character.avatar} {p.name}{p.is_host ? " 👑" : ""} — {p.is_host ? "host" : p.ready ? "READY" : "…"}{p.connected ? "" : " 📴"}</T>
        ))}
        {isHost
          ? <Button tone="chaos" disabled={!ready} title={ready ? "Begin the Misadventure" : "Waiting for everyone…"} onPress={() => send({ action: "start_game" })} />
          : <Button title={view.me.ready ? "Actually, wait" : "I'm Ready!"} tone={view.me.ready ? "ghost" : "havoc"} onPress={() => send({ action: "set_ready", ready: !view.me.ready })} />}
      </Card>
    </>
  );
}

function ThemeSubmit({ view, send }: { view: GameView; send: Send }) {
  const [text, setText] = useState("");
  return (
    <Card paper title="Pitch a premise (secretly)">
      {view.my_theme ? <T paper>Your pitch: “{view.my_theme}”</T> : null}
      <Input value={text} onChangeText={setText} multiline maxLength={200} placeholder="Medieval knights attempting to deliver a pizza before it gets cold" />
      <Button title="Submit secretly" disabled={text.trim().length < 3} onPress={() => send({ action: "submit_theme", text }) && setText("")} />
      <T paper>{view.themes_submitted?.length ?? 0}/{view.players.length} pitches in</T>
    </Card>
  );
}

function Vote({ view, send }: { view: GameView; send: Send }) {
  return (
    <Card title="Vote in secret">
      {(view.theme_options ?? []).map((o) => (
        <Button key={o.id} tone={view.my_vote === o.id ? "havoc" : "ghost"} title={`${o.title}${o.merged_count > 1 ? `  (×${o.merged_count})` : ""}`}
          onPress={() => send({ action: "cast_vote", option_id: o.id })} />
      ))}
    </Card>
  );
}

function Objective({ view }: { view: GameView }) {
  const o = view.objective;
  if (!o) return null;
  return (
    <Card paper title={`🎯 ${o.title}`}>
      <T paper>{o.description}</T>
      {o.success_conditions.map((s) => <T paper key={s}>✅ {s}</T>)}
      {o.failure_conditions.map((s) => <T paper key={s}>💥 {s}</T>)}
    </Card>
  );
}

const FIELDS = [
  ["name", "Name"], ["archetype", "Archetype"], ["personality", "Personality"], ["special_ability", "Special ability"],
  ["weakness", "Weakness"], ["secret_motivation", "Secret motivation (only you know)"], ["starting_item", "Starting item"], ["humorous_trait", "Humorous trait"],
] as const;

function CharacterForm({ view, send, suggestion, clear }: { view: GameView; send: Send; suggestion: GameView["me"]["character"] | null; clear: () => void }) {
  const [c, setC] = useState<Record<string, string>>(() => ({ name: view.me.character.name || view.me.name }));
  useEffect(() => { if (suggestion) { setC({ ...(suggestion as unknown as Record<string, string>) }); clear(); } }, [suggestion, clear]);
  if (view.me.character.complete) return <Card title={`${view.me.character.avatar} ${view.me.character.name}`}><T>{view.me.character.archetype}</T><T muted>Waiting for the others…</T></Card>;
  return (
    <Card paper title="Create your character">
      {FIELDS.map(([k, label]) => (
        <View key={k} style={{ gap: 4 }}>
          <T paper>{label}</T>
          <Input value={c[k] ?? ""} onChangeText={(v) => setC({ ...c, [k]: v })} maxLength={k === "name" ? 40 : 200} accessibilityLabel={label} />
        </View>
      ))}
      <Button tone="zap" title="🎲 Roll me a character" onPress={() => send({ action: "suggest_character" })} />
      <Button title="Lock it in" disabled={!c.name?.trim()} onPress={() => send({ action: "save_character", name: c.name, archetype: c.archetype, personality: c.personality,
        special_ability: c.special_ability, weakness: c.weakness, secret_motivation: c.secret_motivation, starting_item: c.starting_item, humorous_trait: c.humorous_trait })} />
    </Card>
  );
}

function Adventure({ view, send }: { view: GameView; send: Send }) {
  const feed = view.story_feed.filter((e) => e.kind !== "system").slice(-12).reverse();
  return (
    <>
      <H size={28}>{view.phase === "final_challenge" ? "THE FINAL CHALLENGE" : `Scene ${view.turn_number}: ${view.round?.title ?? ""}`}</H>
      {view.decisions.map((d) => <DecisionCard key={d.group_id} view={view} d={d} send={send} />)}
      <Card title="Resources">
        {Object.values(view.shared_resources).map((r) => <T key={r.key}>{r.emoji} {r.label}: {r.value}/{r.max}</T>)}
        <T muted>❤️ {view.me.resources.health} · 🍀 {view.me.resources.luck} · ✨ {view.me.resources.ability_charges} · 🎯 {view.objective_progress}/{view.objective?.progress_target}</T>
      </Card>
      {view.information.filter((i) => i.visibility !== "public").slice(-4).map((i) => (
        <Card key={i.id} title={`🤫 ${i.title}`}><T>{i.text}</T></Card>
      ))}
      <Card title="The story so far">
        {feed.map((e) => (
          <View key={e.id} style={{ borderLeftWidth: e.visibility === "public" ? 0 : 6, borderLeftColor: C.zap, paddingLeft: 8, gap: 2 }}>
            <Text style={{ color: e.kind === "scene" ? C.zap : C.text, fontWeight: "800" }}>{e.visibility !== "public" ? "🤫 " : ""}{e.title}</Text>
            <T>{e.text}</T>
          </View>
        ))}
      </Card>
    </>
  );
}

function DecisionCard({ view, d, send }: { view: GameView; d: Decision; send: Send }) {
  const [choice, setChoice] = useState<string | null>(d.my_submission?.choice_id ?? null);
  const [free, setFree] = useState("");
  const [luck, setLuck] = useState(false);
  const submitted = d.submitted[view.me.id];
  const waiting = d.player_ids.filter((p) => !d.submitted[p]).length;
  return (
    <Card paper title={`${d.kind === "group" ? "👥" : d.kind === "final" ? "⚔️" : "🤫"} ${d.title}`}>
      <T paper>{d.shared_context}</T>
      {d.available_choices.map((c) => (
        <Pressable key={c.id} accessibilityRole="radio" accessibilityState={{ checked: choice === c.id && !free }} onPress={() => { setChoice(c.id); setFree(""); }}
          style={{ borderWidth: 3, borderRadius: 12, padding: 10, backgroundColor: choice === c.id && !free ? "#ffe2d4" : "#fff" }}>
          <Text style={{ fontWeight: "800", fontSize: 16 }}>{c.label}</Text>
          <Text style={{ fontSize: 12 }}>{c.risk.toUpperCase()} · {c.stat}{Object.entries(c.cost).map(([k, v]) => ` · -${v} ${view.shared_resources[k]?.label ?? k}`).join("")}</Text>
        </Pressable>
      ))}
      <Input value={free} onChangeText={setFree} maxLength={300} multiline placeholder="…or do literally anything else" style={{ backgroundColor: "#fff", color: "#000" }} />
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <Switch value={luck} onValueChange={setLuck} disabled={!view.me.resources.luck} accessibilityLabel="Push your luck" />
        <T paper>🍀 Push luck ({view.me.resources.luck})</T>
      </View>
      <Button title={submitted ? "Change my decision" : "Lock it in"} disabled={!choice && !free.trim()}
        onPress={() => send({ action: "submit_decision", group_id: d.group_id, choice_id: free.trim() ? null : choice, freeform: free.trim() || null, push_luck: luck })} />
      <T paper>{submitted ? (waiting ? `Locked in. Waiting on ${waiting}…` : "Resolving…") : "Nobody else can see what you choose."}</T>
    </Card>
  );
}

function Ending({ view, send, code }: { view: GameView; send: Send; code: string }) {
  const story = view.final_story!;
  const o = view.outcome!;
  const track = useTrack();
  const prefs = view.me.preferences;
  const server = Boolean(view.audio_status?.provider && view.audio_status.provider !== "browser" && view.audio_status.state !== "client");
  const audioUrl = useMemo(() => (i: number) => client.audioUrl(code, i, prefs.preferred_voice), [code, prefs.preferred_voice]);
  const { state, narrator } = useNarrator({ mode: server ? "server" : "device", chapters: story.chapters, title: story.title, audioUrl,
    voice: prefs.preferred_voice, volume: prefs.narration_volume, enabled: prefs.audio_enabled });
  const total = story.chapters.length;
  return (
    <>
      <H size={30}>The Complete Story</H>
      <T style={{ color: C.slime, fontWeight: "900", fontSize: 20 }}>{o.headline}</T>
      <T muted>{o.group_summary}</T>
      <Card title="Read aloud">
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <Switch value={prefs.audio_enabled} accessibilityLabel="Narration audio"
            onValueChange={(v) => { track("narration_toggled", { enabled: v }); send({ action: "set_preferences", audio_enabled: v }); }} />
          <T>{prefs.audio_enabled ? "🔊 Audio on" : "🔇 Audio off — read silently below"}</T>
        </View>
        {prefs.audio_enabled && (
          <>
            <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
              {state.status === "paused" ? <Button title="Resume ▶" onPress={() => narrator.resume()} />
                : state.status === "playing" || state.status === "loading" ? <Button tone="zap" title="Pause ⏸" onPress={() => narrator.pause()} />
                : <Button title="Play Story ▶" onPress={() => { track("narration_played", { mode: server ? "server" : "device" }); void narrator.play(); }} />}
              <Button tone="ghost" title="Stop ■" onPress={() => narrator.stop()} />
              <Button tone="ghost" title="Restart ⟲" onPress={() => void narrator.play(0)} />
            </View>
            <T>Chapter {state.chapter + 1} of {total}</T>
            <View accessibilityRole="progressbar" style={{ height: 12, borderWidth: 2, borderRadius: 8, overflow: "hidden", backgroundColor: "#0006" }}>
              <View style={{ height: "100%", width: `${((state.chapter + state.progress) / total) * 100}%`, backgroundColor: C.havoc }} />
            </View>
            <Stepper label="Volume" value={prefs.narration_volume} onChange={(v) => send({ action: "set_preferences", narration_volume: v })} />
            {state.error ? <T style={{ color: C.chaos }}>{state.error}</T> : null}
          </>
        )}
      </Card>
      {story.chapters.map((c) => (
        <Card key={c.index} paper title={c.title}>
          {c.text.split(/\n\s*\n/).map((p, i) => <T paper key={i}>{p}</T>)}
        </Card>
      ))}
      <Card title="🏆 Achievements">
        {view.players.map((p) => <T key={p.id}>{p.character.name}: {(o.achievements[p.id] ?? []).map((a) => `${a.emoji} ${a.title}`).join(" · ")}</T>)}
      </Card>
      {view.share_id && view.settings.allow_public_share ? (
        <Button tone="chaos" title="Share the legend" onPress={() => void Share.share({ message: `${story.title}\n${client.baseUrl}/story/${view.share_id}` })} />
      ) : null}
      {view.me.id === view.host_id && <Button title="Play again (same party)" onPress={() => send({ action: "restart_game" })} />}
    </>
  );
}
