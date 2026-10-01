"""Achievements — every one computed from recorded stats and chronicle events."""

from __future__ import annotations

from ..models.game import Achievement, GameState, OutcomeTier, Player


def _events_for(state: GameState, pid: str, *types: str, tiers: tuple[str, ...] = ()) -> list[str]:
    out = []
    for ev in state.adventure_chronicle:
        if pid in ev.player_ids and (not types or ev.event_type in types):
            if not tiers or any(d.get("tier") in tiers for d in ev.decisions):
                out.append(ev.event_id)
    return out


def compute(state: GameState) -> dict[str, list[Achievement]]:
    players = state.active_players()
    result: dict[str, list[Achievement]] = {p.id: [] for p in players}
    if not players:
        return result

    def award(p: Player, key: str, title: str, desc: str, emoji: str, evidence: list[str]) -> None:
        result[p.id].append(Achievement(key=key, title=title, description=desc, emoji=emoji, evidence_event_ids=evidence[:5]))

    total_problems = sum(p.stats.complications_caused + p.stats.failures + p.stats.catastrophes for p in players)
    if total_problems:
        worst = max(players, key=lambda p: (p.stats.complications_caused + p.stats.failures + p.stats.catastrophes, p.id))
        share = round(100 * (worst.stats.complications_caused + worst.stats.failures + worst.stats.catastrophes) / total_problems)
        if share >= 20:
            award(worst, "problems", f"Caused {share}% of the Problems", "A statistically significant source of chaos.", "🔥",
                  _events_for(state, worst.id, "action", "group_action", tiers=("failure", "complication")))

    wild_fails = [p for p in players if p.stats.highest_risk_failure_roll is not None]
    if wild_fails:
        p = min(wild_fails, key=lambda p: (p.stats.highest_risk_failure_roll, p.id))
        award(p, "questionable", "Most Questionable Decision", f"Took a wild risk and rolled a {p.stats.highest_risk_failure_roll}.", "🤦",
              _events_for(state, p.id, tiers=("failure",)))

    finals = [p for p in players if p.stats.final_contribution > 0]
    if finals:
        hero = min(finals, key=lambda p: (p.stats.pre_final_contribution - 2 * p.stats.final_contribution, p.id))
        if hero.stats.pre_final_contribution <= sorted(x.stats.pre_final_contribution for x in players)[len(players) // 2]:
            award(hero, "unexpected_hero", "Unexpected Hero", "Quiet all adventure, then clutch when it mattered.", "🦸",
                  _events_for(state, hero.id, "final_action"))

    negotiators = [p for p in players if p.stats.social_successes >= 1]
    if negotiators:
        p = max(negotiators, key=lambda p: (p.stats.social_successes, p.id))
        award(p, "negotiator", "Supreme Negotiator", f"Talked their way through {p.stats.social_successes} situation(s).", "🤝",
              [e for e in _events_for(state, p.id) if e])
    touchers = [p for p in players if p.stats.items_taken + p.stats.harmful_events_triggered >= 1]
    if touchers:
        p = max(touchers, key=lambda p: (p.stats.items_taken + p.stats.harmful_events_triggered, p.id))
        award(p, "touched", "Probably Shouldn't Have Touched That", f"Picked up {p.stats.items_taken} thing(s) that were not theirs.", "🫳",
              _events_for(state, p.id))
    for p in players:
        if p.stats.betrayals:
            award(p, "traitor", "Not Technically a Traitor", f"Betrayed the party {p.stats.betrayals} time(s).", "🗡️",
                  _events_for(state, p.id, "betrayal", "action"))
        if p.stats.secrets_found:
            award(p, "truth", "Found the Hidden Truth", "Discovered something the universe was trying to hide.", "🔍",
                  _events_for(state, p.id, "discovery"))
        if p.stats.helps >= 2:
            award(p, "team_player", "Actual Team Player", f"Helped others {p.stats.helps} times.", "🫶", _events_for(state, p.id))
        if p.stats.freeform_actions >= 3:
            award(p, "off_script", "Refused to Read the Script", f"Went off-script {p.stats.freeform_actions} times.", "📜", _events_for(state, p.id))
        if p.stats.luck_spent >= 2:
            award(p, "gambler", "Pushed Their Luck", f"Spent {p.stats.luck_spent} luck. Regrets: unclear.", "🎰", _events_for(state, p.id))
        if p.stats.catastrophes >= 1 and p.stats.successes >= 2:
            award(p, "chaos_agent", "Agent of Chaos", "Succeeded often, and catastrophically.", "🌪️",
                  _events_for(state, p.id, tiers=(OutcomeTier.CATASTROPHIC_SUCCESS.value, OutcomeTier.FAILURE.value)))
        if p.stats.times_soloed >= 3 and p.stats.betrayals == 0:
            award(p, "lone_wolf", "Lone Wolf", "Spent most of the adventure going it alone.", "🐺", _events_for(state, p.id))
    for p in players:
        if not result[p.id]:
            award(p, "participant", "Was Definitely There", "Present for all of it. Responsible for some of it.", "🎟️", _events_for(state, p.id))
    return result
