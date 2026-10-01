import json

import httpx

from app.analytics import PostHogAnalytics, sanitize
from app.engine.ports import NullAnalytics
from tests.conftest import make_game, play_through


def test_sanitize_refuses_free_text():
    out = sanitize({"n": 3, "ok": True, "kind": "full_success", "theme": "Pirates trying to steal the moon tonight",
                    "long": "x" * 200, "nested": {"tier": "success", "text": "I lick the doorknob very slowly"}, "obj": object()})
    assert out == {"n": 3, "ok": True, "kind": "full_success", "nested": {"tier": "success"}}


async def test_game_lifecycle_events_without_player_content(settings):
    mgr, players, _ = await make_game(settings, n=3, seed=31)
    mgr.analytics = NullAnalytics()
    state = await play_through(mgr, players)
    names = [e[0] for e in mgr.analytics.events]
    for expected in ("game_started", "theme_selected", "adventure_started", "decision_submitted", "scene_resolved",
                     "game_completed", "story_generated"):
        assert expected in names, expected
    blob = json.dumps([e[2] for e in mgr.analytics.events])
    forbidden = [s.text for s in state.theme_submissions.values()] + [p.character.secret_motivation for p in state.players.values()]
    forbidden += ["giant chicken", "steal the key", state.objective.title]
    for text in forbidden:
        if text:
            assert text not in blob, text
    completed = next(e for e in mgr.analytics.events if e[0] == "game_completed")
    assert completed[2]["outcome"] == state.outcome.kind.value and completed[3] == {"game": state.game_id}
    # signed-in players are tracked by account, guests by seat
    assert all(e[1] for e in mgr.analytics.events)


async def test_posthog_batches_to_capture_endpoint():
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.read())))
        return httpx.Response(200, json={"status": 1})

    ph = PostHogAnalytics("phc_test", "https://ph.test", flush_every=0)
    ph._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ph.capture("game_started", "user_1", {"players": 3, "theme": "a long free text theme that must go"}, {"game": "g_1"})
    ph.capture("game_completed", "user_1", {"outcome": "failure"})
    await ph.flush()
    path, body = sent[0]
    assert path == "/batch/" and body["api_key"] == "phc_test" and len(body["batch"]) == 2
    first = body["batch"][0]
    assert first["properties"] == {"players": 3, "$groups": {"game": "g_1"}}
    await ph.aclose()
