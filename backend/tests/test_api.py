"""Full game over HTTP + WebSockets, exactly as the frontend plays it."""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HAVOC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("HAVOC_TTS_CACHE_DIR", str(tmp_path / "audio"))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def until(ws, pred, limit=400):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg.get("type") == "error" and msg.get("fatal"):
            raise AssertionError(msg)
        if msg.get("type") == "state" and pred(msg["state"]):
            return msg["state"]
    raise AssertionError("condition never reached")


def test_health_and_title(client):
    r = client.get("/api/health")
    assert r.json()["title"] == "The Misadventures of Havoc and Chaos"
    assert client.get("/api/config").json()["tts"]["provider"] == "browser"


def test_auth_required(client):
    code = client.post("/api/games", json={"name": "Alex"}).json()["code"]
    assert client.get(f"/api/games/{code}/state").status_code == 401
    assert client.get(f"/api/games/{code}/state", headers={"Authorization": "Bearer nope"}).status_code == 403
    with client.websocket_connect(f"/ws/{code}?token=forged") as ws:
        assert ws.receive_json()["fatal"] is True


def poll(client, code, token, pred, timeout=20.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/games/{code}/state", headers={"Authorization": f"Bearer {token}"}).json()
        if pred(st):
            return st
        time.sleep(0.02)
    raise AssertionError(f"condition never reached; phase={st['phase']} turn={st['turn_number']} decisions={[(d['kind'], d['submitted']) for d in st['decisions']]} feed={[e['title'] for e in st['story_feed'][-4:]]}")


def test_full_game_over_websockets(client):
    host = client.post("/api/games", json={"name": "Alex", "settings": {"auto_advance": False}}).json()
    code = host["code"]
    guest = client.post(f"/api/games/{code}/join", json={"name": "Sam"}).json()
    assert client.get(f"/api/games/{code}").json()["players"] == 2
    H, G = host["token"], guest["token"]

    with client.websocket_connect(f"/ws/{code}?token={H}") as hws, client.websocket_connect(f"/ws/{code}?token={G}") as gws:
        socks = {host["player_id"]: (hws, H), guest["player_id"]: (gws, G)}
        st = until(hws, lambda s: len(s["players"]) == 2)
        assert H not in json.dumps(st) or st["me"]["id"] == host["player_id"]

        # garbage and forged messages are rejected without breaking the socket
        gws.send_text("not json")
        assert json.loads(gws.receive_text())  # error or state, socket still alive
        gws.send_text(json.dumps({"action": "start_game"}))  # not host
        gws.send_text(json.dumps({"action": "set_ready", "ready": True}))
        poll(client, code, H, lambda s: all(p["ready"] for p in s["players"] if not p["is_host"]))
        assert poll(client, code, H, lambda s: True)["phase"] == "lobby"

        hws.send_text(json.dumps({"action": "start_game"}))
        poll(client, code, H, lambda s: s["phase"] == "theme_submission")
        hws.send_text(json.dumps({"action": "submit_theme", "text": "Medieval knights attempting to deliver a pizza before it gets cold"}))
        gws.send_text(json.dumps({"action": "submit_theme", "text": "Office workers trapped inside a haunted Costco"}))
        st = poll(client, code, H, lambda s: s["phase"] == "theme_voting")
        assert len(st["theme_options"]) == 2
        for ws, _ in socks.values():
            ws.send_text(json.dumps({"action": "cast_vote", "option_id": st["theme_options"][1]["id"]}))
        st = poll(client, code, H, lambda s: s["phase"] == "objective_reveal")
        assert st["objective"]["title"]
        hws.send_text(json.dumps({"action": "close_phase"}))
        poll(client, code, H, lambda s: s["phase"] == "character_creation")
        hws.send_text(json.dumps({"action": "save_character", "name": "Greg", "archetype": "Extremely Confident Accountant",
                                  "special_ability": "Can calculate probabilities instantly", "weakness": "Cannot resist correcting people",
                                  "secret_motivation": "Believes every problem can be solved with spreadsheets",
                                  "starting_item": "Calculator", "humorous_trait": "Hums the Jeopardy theme"}))
        poll(client, code, H, lambda s: s["me"]["character"]["complete"])
        gst = poll(client, code, G, lambda s: True)
        assert "spreadsheets" not in json.dumps(gst), "Greg's secret must not reach Sam"
        hws.send_text(json.dumps({"action": "close_phase"}))

        turns = 0
        while True:
            st = poll(client, code, H, lambda s: s["phase"] in ("adventure", "final_challenge", "story_generation", "ended"))
            if st["phase"] in ("story_generation", "ended"):
                break
            views = {pid: poll(client, code, tok, lambda s: True) for pid, (_, tok) in socks.items()}
            todo = [(pid, d) for pid, v in views.items() for d in v["decisions"] if not d["submitted"][pid]]
            for pid, d in todo:
                ws, tok = socks[pid]
                payload = {"action": "submit_decision", "group_id": d["group_id"]}
                if turns % 3 == 2:
                    payload["freeform"] = "I betray the group and run away with the treasure"
                else:
                    payload["choice_id"] = d["available_choices"][0]["id"]
                ws.send_text(json.dumps(payload))
                turns += 1
                poll(client, code, tok, lambda s, g=d["group_id"], pid=pid: all(x["group_id"] != g or x["submitted"][pid] for x in s["decisions"]))
            if not todo and not any(v["decisions"] for v in views.values()):
                hws.send_text(json.dumps({"action": "close_phase"}))
                poll(client, code, H, lambda s, t=st["turn_number"], ph=st["phase"]: s["turn_number"] != t or s["phase"] != ph)
            assert turns < 60

        st = poll(client, code, H, lambda s: s["phase"] == "ended" and s["final_story"] is not None)
        poll(client, code, H, lambda s: s["audio_status"].get("state") == "client")  # archive + audio hook done
        assert st["final_story"]["title"].startswith("The Misadventures of Havoc and Chaos")
        assert st["outcome"]["kind"]
        assert st["revealed"]
        share_id = st["share_id"]

        hws.send_text(json.dumps({"action": "set_preferences", "audio_enabled": False}))
        poll(client, code, H, lambda s: s["me"]["preferences"]["audio_enabled"] is False)
        assert poll(client, code, G, lambda s: True)["me"]["preferences"]["audio_enabled"] is True  # per player

    txt = client.get(f"/api/games/{code}/story.txt", params={"token": G})
    assert txt.status_code == 200 and "MISADVENTURES OF HAVOC AND CHAOS" in txt.text
    assert client.get(f"/api/games/{code}/audio/0", params={"token": G}).status_code == 204  # browser narration

    shared = client.get(f"/api/share/{share_id}")
    assert shared.status_code == 200
    blob = shared.text
    assert H not in blob and G not in blob
    assert host["player_id"] not in blob
    assert shared.json()["chapters"]
    assert client.get("/api/share/doesnotexist").status_code == 404


def test_lobby_rules(client):
    host = client.post("/api/games", json={"name": "Alex", "settings": {"max_players": 2, "auto_advance": False}}).json()
    code = host["code"]
    with client.websocket_connect(f"/ws/{code}?token={host['token']}") as hws:
        until(hws, lambda s: True)
        hws.send_text(json.dumps({"action": "start_game"}))
        err = json.loads(hws.receive_text())
        while err.get("type") != "error":
            err = json.loads(hws.receive_text())
        assert "at least" in err["message"]
        client.post(f"/api/games/{code}/join", json={"name": "Sam"})
        assert client.post(f"/api/games/{code}/join", json={"name": "Extra"}).status_code == 400  # full
