import pytest

from app.config import Settings
from app.engine.game_manager import GameManager, NullPublisher, NullStore
from app.llm.services import LLMService
from app.models import events as ev


@pytest.fixture
def settings():
    return Settings(llm_provider="offline", database_url="sqlite+aiosqlite:///:memory:")


async def make_game(settings, n=4, length="short", seed=1234):
    llm = LLMService(None, settings)
    pub = NullPublisher()
    mgr, host = GameManager.new("Alex", llm, settings, pub, NullStore(), {"auto_advance": False, "adventure_length": length})
    mgr.state.random_seed = seed
    players = [host]
    for name in ["Sam", "Jordan", "Riley", "Casey", "Morgan", "Quinn", "Avery", "Drew", "Parker", "Reese", "Skyler"][: n - 1]:
        players.append(await mgr.join(name))
    for p in players:
        p.connected = True
    return mgr, players, pub


async def play_through(mgr, players, freeform_every=3):
    """Drive a whole adventure with scripted players."""
    host = players[0]
    for p in players[1:]:
        await mgr.handle(p.id, ev.SetReady(action="set_ready", ready=True))
    await mgr.handle(host.id, ev.StartGame(action="start_game"))
    themes = ["Pirates steal the moon", "Pirates trying to rob the moon", "Space pirates stealing the moon",
              "Office workers trapped inside a haunted Costco", "Raccoons accidentally become the government"]
    for i, p in enumerate(players):
        await mgr.handle(p.id, ev.SubmitTheme(action="submit_theme", text=themes[i % len(themes)]))
    st = mgr.state
    if st.phase.value == "theme_voting":
        for p in players:
            await mgr.handle(p.id, ev.CastVote(action="cast_vote", option_id=st.theme_options[0].id))
    assert mgr.state.phase.value == "objective_reveal"
    await mgr.on_timeout("objective_reveal")
    assert mgr.state.phase.value == "character_creation"
    for i, p in enumerate(players):
        if i % 2 == 0:
            await mgr.handle(p.id, ev.SaveCharacter(action="save_character", name=p.name, archetype="Extremely Confident Accountant",
                                                    special_ability="Can calculate probabilities instantly",
                                                    weakness="Cannot resist correcting people",
                                                    secret_motivation="Believes every problem can be solved with spreadsheets",
                                                    starting_item="Calculator", humorous_trait="hums constantly"))
    await mgr.close_phase(host)  # remaining characters get auto-generated
    step = 0
    while mgr.state.phase.value in ("adventure", "final_challenge"):
        st = mgr.state
        if not st.pending_decisions:
            await mgr.close_phase(host)
            continue
        for g in list(st.pending_decisions.values()):
            for p in g.player_ids:
                step += 1
                if step % freeform_every == 0:
                    msg = ev.SubmitDecision(action="submit_decision", group_id=g.group_id,
                                            freeform="I convince Sam to dress up like a giant chicken and distract the guard while I steal the key")
                else:
                    c = g.available_choices[step % len(g.available_choices)]
                    msg = ev.SubmitDecision(action="submit_decision", group_id=g.group_id, choice_id=c.id, push_luck=step % 5 == 0 and mgr.state.players[p].resources.luck > 0)
                if mgr.state.pending_decisions.get(g.group_id) is g:
                    await mgr.handle(p, msg)
        assert step < 500
    # wait for background story generation
    import asyncio
    for _ in range(200):
        if mgr.state.phase.value == "ended":
            break
        await asyncio.sleep(0.01)
    return mgr.state
