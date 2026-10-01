from app.engine import director, resolution
from app.engine.resolution import PlannedAction
from app.models.game import OutcomeTier, Risk
from tests.conftest import make_game, play_through


async def _adventure_state(settings, n=3, seed=5):
    mgr, players, _ = await make_game(settings, n=n, seed=seed)
    s = mgr.state
    from app.engine import procgen

    s.theme = "Pirates steal the moon"
    s.objective = procgen.make_objective(s.theme, s.random_seed, 4, n)
    s.shared_resources = procgen.make_resources(s.theme, n, 4)
    s.locations = {loc.id: loc for loc in procgen.make_locations(s.theme, s.random_seed)}
    s.turn_number = 1
    return mgr, players


async def test_distraction_helps_a_thief_in_another_place(settings):
    mgr, (a, b, c) = await _adventure_state(settings)
    s = mgr.state
    acts = [
        PlannedAction(key="a", group_id="g1", player_ids=[a.id], description="Create a loud distraction", tags=["distract", "noise"], risk=Risk.RISKY, stat="charm"),
        PlannedAction(key="b", group_id="g2", player_ids=[b.id], description="Take it. Quietly.", tags=["steal"], risk=Risk.RISKY, stat="sneak"),
        PlannedAction(key="c", group_id="g3", player_ids=[c.id], description="Haggle", tags=["negotiate"], risk=Risk.RISKY, stat="charm"),
    ]
    res = resolution.resolve_round(s, acts, escalation=1)
    by = {o.planned.key: o for o in res.outcomes}
    assert by["b"].result.modifiers.get("other players", 0) > 0  # A's diversion covered B
    assert any("already stolen" in t for t in by["c"].result.interactions)  # B's theft undercut C
    assert "a" in by["b"].interaction_partners


async def test_resolution_is_deterministic(settings):
    out = []
    for _ in range(2):
        mgr, (a, b, c) = await _adventure_state(settings, seed=77)
        act = PlannedAction(key="x", group_id="g", player_ids=[a.id], description="Press the button", tags=["destroy"], risk=Risk.WILD, stat="weird")
        r = resolution.resolve_round(mgr.state, [act], 1).outcomes[0].result
        out.append((r.roll, r.tier, r.resource_changes))
    assert out[0] == out[1]


async def test_destroyed_world_affects_later_scenes(settings):
    mgr, (a, b, c) = await _adventure_state(settings, seed=9)
    s = mgr.state
    loc = next(iter(s.locations))
    for seed in range(50):  # find a seed where the destruction lands
        s.random_seed = seed
        s.world_state["scars"] = []
        resolution.resolve_round(s, [PlannedAction(key=f"d{seed}", group_id="g", player_ids=[a.id], description="Saw through the bridge",
                                                   tags=["destroy"], risk=Risk.SAFE, stat="brawn", location_id=loc)], 1)
        if s.world_state["scars"]:
            break
    assert s.world_state["scars"]
    s.turn_number = 2
    o = resolution.resolve_round(s, [PlannedAction(key="cross", group_id="g", player_ids=[b.id], description="Cross normally",
                                                   tags=["cross"], risk=Risk.RISKY, stat="brawn")], 1).outcomes[0]
    assert o.result.modifiers.get("world state", 0) < 0
    assert any(a.display in t for t in o.result.interactions)


async def test_costs_are_paid_and_shortfalls_penalised(settings):
    mgr, (a, b, c) = await _adventure_state(settings)
    s = mgr.state
    s.shared_resources["money"].value = 1
    o = resolution.resolve_round(s, [PlannedAction(key="bribe", group_id="g", player_ids=[a.id], description="Pay full price",
                                                   tags=["negotiate"], risk=Risk.SAFE, stat="charm", cost={"money": 3})], 1).outcomes[0]
    assert s.res("money") == 0
    assert o.result.modifiers.get("couldn't afford it") == -3


async def test_director_groups_and_structure(settings):
    mgr, players = await _adventure_state(settings, n=5)
    s = mgr.state
    beats = director.BEATS[s.settings.adventure_length]
    plans = [director.plan_round(s, i + 1) for i in range(len(beats))]
    assert plans[0].groups == [[p.id for p in s.active_players()]]  # everyone together first
    assert all(len(g) == 1 for g in plans[1].groups)  # then everyone alone
    assert plans[1].comm_mode.value == "restricted"
    s.total_rounds = len(beats)
    s.turn_number = len(beats)
    assert director.should_start_finale(s)


async def test_every_outcome_tier_is_reachable(settings):
    tiers = set()
    for seed in range(30):
        mgr, players, _ = await make_game(settings, n=4, seed=seed)
        st = await play_through(mgr, players)
        for g in st.decision_archive:
            tiers |= {r.tier for r in g.resolution}
    assert tiers >= {OutcomeTier.SUCCESS, OutcomeTier.PARTIAL_SUCCESS, OutcomeTier.COMPLICATION, OutcomeTier.FAILURE,
                     OutcomeTier.UNEXPECTED_SUCCESS, OutcomeTier.CATASTROPHIC_SUCCESS}


async def test_outcome_kinds_vary(settings):
    kinds = set()
    for seed in range(40):
        mgr, players, _ = await make_game(settings, n=3, seed=seed)
        kinds.add((await play_through(mgr, players)).outcome.kind)
    assert len(kinds) >= 3, kinds


async def test_rehydrate_from_database(tmp_path, settings):
    from app.llm.services import LLMService
    from app.models import events as ev
    from app.persistence.db import Repository
    from app.realtime.hub import Hub
    from app.registry import GameRegistry
    from app.tts.providers.browser import BrowserTTS
    from app.tts.service import TTSService

    repo = Repository(f"sqlite+aiosqlite:///{tmp_path}/r.db")
    await repo.init()
    reg = GameRegistry(settings, LLMService(None, settings), TTSService(BrowserTTS(), settings), Hub(), repo)
    mgr, host_id, token = await reg.create("Alex", {"auto_advance": False})
    guest = await mgr.join("Sam")
    await mgr.handle(guest.id, ev.SetReady(action="set_ready", ready=True))
    await mgr.handle(host_id, ev.StartGame(action="start_game"))
    await mgr.handle(guest.id, ev.SubmitTheme(action="submit_theme", text="Pirates steal the moon"))
    code = mgr.state.code

    reg2 = GameRegistry(settings, LLMService(None, settings), TTSService(BrowserTTS(), settings), Hub(), repo)  # "server restart"
    mgr2 = await reg2.get(code)
    assert mgr2.state.phase.value == "theme_submission"
    assert mgr2.authenticate(token).id == host_id
    assert mgr2.state.theme_submissions[guest.id].text == "Pirates steal the moon"
    await repo.close()
