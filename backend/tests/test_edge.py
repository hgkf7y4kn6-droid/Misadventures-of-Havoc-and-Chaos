"""The engine <-> Durable Object contract, exercised end to end without Cloudflare.

FakeRoom plays the GameRoom DO: it receives signed op batches (socket sends, kicks,
alarm arm/cancel) and calls the signed internal API back, including delivering alarms.
"""

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.edge.link import EdgeLink
from app.edge.signing import sign, verify

SECRET = "edge-test-secret"


class FakeRoom:
    def __init__(self):
        self.lock = threading.Lock()
        self.states: dict[str, dict] = {}
        self.errors: list[tuple[str, str]] = []
        self.alarm: dict | None = None
        self.ops_log: list[dict] = []
        self.bad_signatures = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        if not verify(SECRET, request.method, request.url.path, body, request.headers.get("x-havoc-ts"), request.headers.get("x-havoc-sig")):
            self.bad_signatures += 1
            return httpx.Response(401)
        with self.lock:
            for op in json.loads(body)["ops"]:
                self.ops_log.append(op)
                if op["op"] == "send":
                    m = op["message"]
                    if m["type"] == "state":
                        self.states[op["player_id"]] = m["state"]
                    elif m["type"] == "error":
                        self.errors.append((op["player_id"], m["message"]))
                elif op["op"] == "alarm":
                    self.alarm = op
                elif op["op"] == "cancel_alarm":
                    self.alarm = None
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def edge(tmp_path, monkeypatch):
    monkeypatch.setenv("HAVOC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/e.db")
    monkeypatch.setenv("HAVOC_EDGE_SECRET", SECRET)
    monkeypatch.setenv("HAVOC_EDGE_URL", "https://edge.test")
    monkeypatch.setenv("HAVOC_RESOLUTION_PAUSE_SECONDS", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    room = FakeRoom()
    with TestClient(create_app()) as client:
        link = EdgeLink("https://edge.test", SECRET, httpx.AsyncClient(transport=httpx.MockTransport(room.handler)))
        reg = client.app.state.registry
        reg.publisher = reg.scheduler = link
        yield client, room
    get_settings.cache_clear()


def call(client, method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    path_only = path.split("?")[0]
    headers = {"content-type": "application/json", **sign(SECRET, method, path_only, body)}
    return client.request(method, path, content=body, headers=headers)


def wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_internal_api_requires_valid_signature(edge):
    client, _ = edge
    assert client.post("/internal/games", json={"code": "ABCDE", "user_id": "u", "name": "x"}).status_code == 401
    body = json.dumps({"code": "ABCDE", "user_id": "u", "name": "x"}).encode()
    h = sign(SECRET, "POST", "/internal/games", body)
    assert client.post("/internal/games", content=body + b" ", headers=h).status_code == 401  # body tampered
    assert client.post("/internal/games", content=body, headers=sign("wrong", "POST", "/internal/games", body)).status_code == 401
    assert client.post("/internal/games", content=body, headers=sign(SECRET, "POST", "/internal/games", body, ts=int(time.time()) - 3600)).status_code == 401
    # in edge mode the public play API is not exposed by the engine at all
    assert client.post("/api/games", json={"name": "x"}).status_code in (404, 405)


def test_full_game_through_the_durable_object_contract(edge):
    client, room = edge
    code = "EDGEA"
    r = call(client, "POST", "/internal/games", {"code": code, "user_id": "user_alex", "name": "Alex",
                                                  "settings": {"decision_seconds": 30, "theme_submission_seconds": 30}})
    assert r.status_code == 200, r.text
    host = r.json()["player_id"]
    assert call(client, "POST", "/internal/games", {"code": code, "user_id": "x", "name": "y"}).status_code == 409
    guest = call(client, "POST", f"/internal/games/{code}/join", {"user_id": "guest_sam", "name": "Sam"}).json()["player_id"]
    assert call(client, "GET", f"/internal/games/{code}/seat?user_id=guest_sam").json()["player_id"] == guest
    assert call(client, "GET", f"/internal/games/{code}/seat?user_id=nobody").status_code == 404

    for pid in (host, guest):
        st = call(client, "POST", f"/internal/games/{code}/connect", {"player_id": pid}).json()["state"]
        assert st["me"]["id"] == pid

    def act(pid, msg):
        r = call(client, "POST", f"/internal/games/{code}/action", {"player_id": pid, "message": msg})
        assert r.status_code == 200 and r.json()["accepted"], r.text

    def phase():
        return room.states.get(host, {}).get("phase")

    act(guest, {"action": "set_ready", "ready": True})
    act(guest, {"action": "start_game"})  # not the host -> error pushed back over the socket channel
    wait(lambda: any(p == guest and "host" in m for p, m in room.errors))
    act(host, {"action": "start_game"})
    wait(lambda: phase() == "theme_submission")
    assert room.alarm and room.alarm["tag"] == "theme_submission"

    # a stale alarm (wrong token) is ignored; the real one advances the phase
    assert call(client, "POST", f"/internal/games/{code}/alarm", {"tag": "theme_submission", "token": "stale"}).json()["accepted"] is False
    act(host, {"action": "submit_theme", "text": "Pirates steal the moon"})
    wait(lambda: room.states[host].get("my_theme"))
    alarm = dict(room.alarm)
    assert call(client, "POST", f"/internal/games/{code}/alarm", alarm).json()["accepted"] is True
    wait(lambda: phase() in ("theme_voting", "objective_reveal"))
    # the same alarm delivered twice (DO alarms are at-least-once) is a no-op
    assert call(client, "POST", f"/internal/games/{code}/alarm", alarm).json()["accepted"] is False

    steps = 0
    while phase() not in ("ended", "story_generation"):
        steps += 1
        assert steps < 200, phase()
        before = (phase(), room.states[host].get("turn_number"), room.states[host].get("version"))
        acted = False
        for pid in (host, guest):
            for d in room.states.get(pid, {}).get("decisions", []):
                if not d["submitted"][pid]:
                    act(pid, {"action": "submit_decision", "group_id": d["group_id"], "choice_id": d["available_choices"][0]["id"]})
                    acted = True
            if phase() == "theme_voting" and not room.states[pid].get("my_vote"):
                act(pid, {"action": "cast_vote", "option_id": room.states[pid]["theme_options"][0]["id"]})
                acted = True
        if not acted and room.alarm:  # nothing to do: the DO's alarm moves things along
            call(client, "POST", f"/internal/games/{code}/alarm", {"tag": room.alarm["tag"], "token": room.alarm["token"]})
        wait(lambda before=before: (phase(), room.states[host].get("turn_number"), room.states[host].get("version")) != before)

    wait(lambda: phase() == "ended" and room.states[host].get("final_story") is not None, timeout=20)
    assert room.states[guest]["final_story"]["chapters"]
    assert room.bad_signatures == 0
    # ops for one game arrive strictly ordered: an alarm is never followed by a stale cancel for itself
    kinds = [o["op"] for o in room.ops_log]
    assert "alarm" in kinds and "cancel_alarm" in kinds

    txt = call(client, "GET", f"/internal/games/{code}/players/{guest}/story.txt")
    assert txt.status_code == 200 and "MISADVENTURES" in txt.text
    assert call(client, "GET", f"/internal/games/{code}/players/{guest}/audio/0").status_code == 204


async def test_edgelink_batches_and_preserves_order():
    seen = []

    def handler(request):
        seen.append([o["op"] for o in json.loads(request.read())["ops"]])
        return httpx.Response(200)

    link = EdgeLink("https://edge.test", SECRET, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def fire(tag, token):
        return None

    link.cancel("AAAAA")
    link.arm("AAAAA", 5, "adventure", "tok", fire)
    await link.to_player("AAAAA", "p1", {"type": "state"})
    await link.drain()
    assert seen == [["cancel_alarm", "alarm", "send"]]  # one request, in order
    await link.aclose()


async def test_rehydrated_engine_keeps_edge_presence(tmp_path):
    """At the edge, sockets outlive an engine restart, so presence must survive rehydration."""
    from app.config import Settings
    from app.engine.ports import ManualScheduler, NullPublisher
    from app.llm.services import LLMService
    from app.persistence.db import Repository
    from app.realtime.hub import Hub
    from app.registry import GameRegistry
    from app.tts.providers.browser import BrowserTTS
    from app.tts.service import TTSService

    repo = Repository(f"sqlite+aiosqlite:///{tmp_path}/p.db")
    await repo.init()
    for edge_url, expected in (("https://edge.test", True), (None, False)):
        settings = Settings(edge_secret=SECRET, edge_url=edge_url)

        def registry(settings=settings):
            return GameRegistry(settings, LLMService(None, settings), TTSService(BrowserTTS(), settings), Hub(), repo,
                                publisher=NullPublisher(), scheduler=ManualScheduler())

        mgr, host_id, _ = await registry().create("Alex", None, user_id=f"u-{expected}")
        await mgr.set_connected(host_id, True)
        reborn = await registry().get(mgr.state.code)  # a fresh process loading the game from the database
        assert reborn.state.players[host_id].connected is expected
    await repo.close()
