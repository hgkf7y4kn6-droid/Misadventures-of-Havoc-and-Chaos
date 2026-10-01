"""Information isolation.

The server never sends raw ``GameState`` to anyone. Every client receives a
projection built here for exactly one player, and every LLM prompt receives
an authorised context built here for exactly the players it addresses.
"""

from __future__ import annotations

from typing import Any

from ..models.game import (
    ChatMessage,
    GameState,
    InfoItem,
    Phase,
    Player,
    StoryEntry,
    Visibility,
)

HIDDEN_DURING_PLAY = {Visibility.PRIVATE, Visibility.GROUP, Visibility.ENDGAME_REVEAL, Visibility.NEVER_REVEAL}


def can_see(visibility: Visibility, audience: list[str], player_id: str) -> bool:
    if visibility == Visibility.PUBLIC:
        return True
    return player_id in audience


def visible_to_all(visibility: Visibility, audience: list[str], player_ids: list[str]) -> bool:
    return all(can_see(visibility, audience, pid) for pid in player_ids)


def chat_visible(msg: ChatMessage, player_id: str) -> bool:
    return not msg.audience or player_id in msg.audience


def public_player(p: Player) -> dict[str, Any]:
    c = p.character
    return {
        "id": p.id,
        "name": p.name,
        "is_host": p.is_host,
        "ready": p.ready,
        "connected": p.connected,
        "status": p.status,
        "character": {
            "name": c.name, "archetype": c.archetype, "personality": c.personality,
            "special_ability": c.special_ability, "weakness": c.weakness, "starting_item": c.starting_item,
            "humorous_trait": c.humorous_trait, "avatar": c.avatar, "color": c.color, "complete": c.complete,
        },
        "health": p.resources.health,
        "inventory": [i.model_dump() for i in p.inventory if not i.hidden],
    }


def project(state: GameState, player_id: str) -> dict[str, Any]:
    me = state.players.get(player_id)
    if me is None:
        raise KeyError(player_id)
    ended = state.phase == Phase.ENDED

    my_decisions = []
    for g in state.pending_decisions.values():
        if player_id not in g.player_ids:
            continue
        my_decisions.append({
            "group_id": g.group_id, "round": g.round, "kind": g.kind, "title": g.title,
            "player_ids": g.player_ids, "location_id": g.location_id, "shared_context": g.shared_context,
            "visible_information": [
                state.hidden_information[i].model_dump() for i in g.visible_information
                if i in state.hidden_information and can_see(state.hidden_information[i].visibility, state.hidden_information[i].audience, player_id)
            ],
            "available_choices": [c.model_dump() for c in g.available_choices],
            "allow_freeform": g.allow_freeform, "deadline": g.deadline, "comm_mode": g.comm_mode,
            # who has decided — never *what* they decided
            "submitted": {pid: pid in g.submissions for pid in g.player_ids},
            "my_submission": g.submissions[player_id].model_dump(exclude={"interpretation"}) if player_id in g.submissions else None,
        })

    phase_extra: dict[str, Any] = {}
    if state.phase == Phase.THEME_SUBMISSION:
        phase_extra["my_theme"] = state.theme_submissions[player_id].text if player_id in state.theme_submissions else None
        phase_extra["themes_submitted"] = sorted(state.theme_submissions.keys())
    if state.phase in (Phase.THEME_VOTING,):
        phase_extra["theme_options"] = [{"id": o.id, "title": o.title, "merged_count": len(o.originals)} for o in state.theme_options]
        phase_extra["my_vote"] = state.theme_votes.get(player_id)
        phase_extra["votes_cast"] = sorted(state.theme_votes.keys())
    if state.phase not in (Phase.LOBBY, Phase.THEME_SUBMISSION, Phase.THEME_VOTING):
        # After voting, every option and its originals are public; individual votes stay secret.
        phase_extra["theme_options"] = [
            {"id": o.id, "title": o.title, "merged_count": len(o.originals), "originals": o.originals,
             "votes": state.theme_tally.get(o.id, 0)}
            for o in state.theme_options
        ]

    view: dict[str, Any] = {
        "game_id": state.game_id,
        "code": state.code,
        "version": state.version,
        "phase": state.phase,
        "settings": state.settings.model_dump(),
        "host_id": state.host_id,
        "me": {
            **public_player(me),
            "character": me.character.model_dump(),
            "resources": me.resources.model_dump(),
            "inventory": [i.model_dump() for i in me.inventory],
            "location_id": me.location_id,
            "preferences": me.preferences.model_dump(),
            "stats": me.stats.model_dump(),
        },
        "players": [public_player(p) for p in sorted(state.players.values(), key=lambda p: p.joined_at)],
        "theme": state.theme,
        "objective": state.objective.model_dump() if state.objective else None,
        "objective_progress": state.objective_progress,
        "shared_resources": {k: v.model_dump() for k, v in state.shared_resources.items()},
        "locations": {k: v.model_dump() for k, v in state.locations.items()},
        "npcs": [
            {"id": n.id, "name": n.name, "emoji": n.emoji, "personality": n.personality, "location_id": n.location_id,
             "disposition": n.disposition}
            for n in state.npcs.values() if player_id in n.known_by
        ],
        "current_scene": state.current_scene,
        "round": state.current_round().model_dump() if state.current_round() else None,
        "turn_number": state.turn_number,
        "total_rounds": state.total_rounds,
        "comm_mode": state.comm_mode,
        "story_feed": [e.model_dump() for e in state.story_history if _entry_visible(e, player_id)],
        "information": [i.model_dump() for i in state.hidden_information.values() if can_see(i.visibility, i.audience, player_id)],
        "decisions": my_decisions,
        "chat": [m.model_dump() for m in state.chat[-200:] if chat_visible(m, player_id)],
        "timers": state.timers.model_dump(),
        "outcome": _outcome_for(state, player_id) if state.outcome else None,
        "final_story": state.final_story.model_dump() if (ended and state.final_story) else None,
        "share_id": state.share_id if ended else None,
        "audio_status": state.audio_status if ended else {},
        **phase_extra,
    }
    if ended:
        view["revealed"] = revealed_secrets(state)
    return view


def _entry_visible(e: StoryEntry, player_id: str) -> bool:
    return can_see(e.visibility, e.audience, player_id)


def _outcome_for(state: GameState, player_id: str) -> dict[str, Any]:
    o = state.outcome
    assert o is not None
    return {
        "kind": o.kind, "headline": o.headline, "group_summary": o.group_summary,
        "final_roll": o.final_roll, "final_target": o.final_target,
        "personal": o.personal,  # personal outcomes are part of the shared end-of-game record
        "achievements": {k: [a.model_dump() for a in v] for k, v in o.achievements.items()},
    }


def revealed_secrets(state: GameState) -> list[dict[str, Any]]:
    """End-of-game reveals: everything hidden during play except never_reveal."""
    out = []
    for info in state.hidden_information.values():
        if info.visibility in (Visibility.PRIVATE, Visibility.GROUP, Visibility.ENDGAME_REVEAL):
            names = [state.players[p].display for p in info.audience if p in state.players]
            out.append({"title": info.title, "text": info.text, "known_by": names, "round": info.round})
    for hv in state.hidden_variables:
        if hv.visibility != Visibility.NEVER_REVEAL:
            out.append({"title": "The Hidden Truth", "text": hv.fact,
                        "known_by": [state.players[p].display for p in hv.discovered_by if p in state.players], "round": None})
    for p in state.players.values():
        if p.character.secret_motivation:
            out.append({"title": f"{p.display}'s Secret Motivation", "text": p.character.secret_motivation,
                        "known_by": [p.display], "round": None})
    return out


def public_context(state: GameState, max_entries: int = 8) -> str:
    """Context every player is allowed to know — safe to send to any LLM call."""
    lines = []
    if state.theme:
        lines.append(f"Theme: {state.theme}")
    if state.objective:
        lines.append(f"Objective: {state.objective.title} — {state.objective.description}")
        lines.append(f"Antagonist: {state.objective.antagonist}. Macguffin: {state.objective.macguffin}.")
    if state.memory.medium_summary:
        lines.append(f"Story so far: {state.memory.medium_summary}")
    public = [e for e in state.story_history if e.visibility == Visibility.PUBLIC][-max_entries:]
    for e in public:
        lines.append(f"- {e.title}: {e.text[:400]}")
    if state.memory.running_jokes:
        lines.append("Running jokes: " + "; ".join(j.description for j in state.memory.running_jokes[:5]))
    res = ", ".join(f"{t.label} {t.value}/{t.max}" for t in state.shared_resources.values())
    if res:
        lines.append(f"Shared resources: {res}")
    party = "; ".join(
        f"{p.display} ({p.character.archetype}; ability: {p.character.special_ability}; weakness: {p.character.weakness})"
        for p in state.active_players()
    )
    lines.append(f"Party: {party}")
    return "\n".join(lines)


def authorized_context(state: GameState, player_ids: list[str]) -> str:
    """Public context plus only the information *every* listed player may see.

    For a single player this includes their private discoveries and secret
    motivation; for a group it includes only what the whole group shares.
    """
    lines = [public_context(state)]
    shared: list[InfoItem] = [
        i for i in state.hidden_information.values()
        if i.visibility != Visibility.PUBLIC and visible_to_all(i.visibility, i.audience, player_ids)
    ]
    for i in shared[-8:]:
        lines.append(f"Known only to {'you' if len(player_ids) == 1 else 'this group'}: {i.title} — {i.text}")
    if len(player_ids) == 1:
        p = state.players[player_ids[0]]
        if p.character.secret_motivation:
            lines.append(f"Your secret motivation: {p.character.secret_motivation}")
        if p.inventory:
            lines.append("Your inventory: " + ", ".join(i.name for i in p.inventory))
        mem = state.memory.character_memories.get(p.id, [])[-4:]
        if mem:
            lines.append("You remember: " + " | ".join(mem))
    return "\n".join(lines)
