import pytest

from tests.conftest import make_game, play_through


@pytest.mark.asyncio
@pytest.mark.parametrize("n,length,seed", [(2, "short", 1), (4, "short", 7), (5, "medium", 42), (8, "long", 99)])
async def test_full_adventure(settings, n, length, seed):
    mgr, players, pub = await make_game(settings, n=n, length=length, seed=seed)
    state = await play_through(mgr, players)
    assert state.phase.value == "ended"
    assert state.outcome is not None
    assert state.final_story is not None
    story = state.final_story
    assert story.validation is not None
    assert story.validation.passed, story.validation.issues
    text = " ".join(c.text for c in story.chapters)
    for p in state.active_players():
        assert p.display in text
    assert len(state.adventure_chronicle) > 5
    assert set(state.outcome.achievements) == {p.id for p in state.active_players()}
