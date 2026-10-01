import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import Settings
from app.identity import AuthError, IdentityService, read_token, sign_token


@pytest.fixture(scope="module")
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def clerk_jwt(priv, sub="user_123", azp="http://localhost:5173", exp_in=300, **extra):
    now = int(time.time())
    return jwt.encode({"sub": sub, "azp": azp, "iat": now, "nbf": now - 1, "exp": now + exp_in,
                       "iss": "https://clerk.example.com", **extra}, priv, algorithm="RS256")


def test_signed_tokens_detect_tampering():
    tok = sign_token("s3cret", "g", {"sub": "guest_1"})
    assert read_token("s3cret", "g", tok)["sub"] == "guest_1"
    prefix, payload, sig = tok.split(".")
    import base64

    forged = base64.urlsafe_b64encode(json.dumps({"sub": "admin"}).encode()).rstrip(b"=").decode()
    for bad in (f"{prefix}.{forged}.{sig}", tok.replace("g.", "t.", 1), tok + "x"):
        with pytest.raises(AuthError):
            read_token("s3cret", "g", bad)
    with pytest.raises(AuthError):
        read_token("other", "g", tok)
    with pytest.raises(AuthError):
        read_token("s3cret", "g", sign_token("s3cret", "g", {"sub": "x", "exp": int(time.time()) - 1}))


def test_tickets_are_bound_to_a_game():
    ids = IdentityService(Settings(session_secret="k"))
    t = ids.issue_ticket("guest_1", "ABCDE")
    assert ids.read_ticket(t, "abcde") == "guest_1"
    with pytest.raises(AuthError):
        ids.read_ticket(t, "ZZZZZ")
    with pytest.raises(AuthError):
        ids.verify(t)  # a ticket is not a session


def test_clerk_jwt_verification(rsa_keys):
    priv, pub = rsa_keys
    ids = IdentityService(Settings(session_secret="k", clerk_jwt_key=pub, clerk_issuer="https://clerk.example.com",
                                   clerk_authorized_parties=["http://localhost:5173", "havoc://"]))
    ident = ids.verify(clerk_jwt(priv))
    assert ident.user_id == "user_123" and ident.kind == "clerk"
    with pytest.raises(AuthError):
        ids.verify(clerk_jwt(priv, azp="https://evil.example"))
    with pytest.raises(AuthError):
        ids.verify(clerk_jwt(priv, exp_in=-60))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    with pytest.raises(AuthError):
        ids.verify(clerk_jwt(other))


@pytest.fixture
def client(tmp_path, monkeypatch, rsa_keys):
    monkeypatch.setenv("HAVOC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("HAVOC_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("HAVOC_CLERK_JWT_KEY", rsa_keys[1])
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_v2_flow_guest_and_clerk(client, rsa_keys):
    host = client.post("/api/session/guest", json={"name": "Alex"}).json()
    assert host["token"].startswith("g.")
    assert client.get("/api/session", headers=auth(host["token"])).json()["kind"] == "guest"
    game = client.post("/api/games", json={"name": "Alex", "settings": {"auto_advance": False}}, headers=auth(host["token"])).json()
    code = game["code"]

    member = clerk_jwt(rsa_keys[0], sub="user_sam")
    joined = client.post(f"/api/games/{code}/join", json={"name": "Sam"}, headers=auth(member)).json()
    # Same account on another device gets the same seat, not a new player
    again = client.post(f"/api/games/{code}/join", json={"name": "Sam on phone"}, headers=auth(member)).json()
    assert again["player_id"] == joined["player_id"]
    assert client.get(f"/api/games/{code}").json()["players"] == 2

    st = client.get(f"/api/games/{code}/state", headers=auth(member)).json()
    assert st["me"]["id"] == joined["player_id"]
    assert "user_sam" not in json.dumps(st), "user ids are never projected"

    ticket = client.post(f"/api/games/{code}/ticket", headers=auth(member)).json()["ticket"]
    with client.websocket_connect(f"/ws/{code}?ticket={ticket}") as ws:
        msg = ws.receive_json()
        while msg["type"] != "state":
            msg = ws.receive_json()
        assert msg["state"]["me"]["id"] == joined["player_id"]

    other = client.post("/api/games", json={"name": "Z"}, headers=auth(host["token"])).json()["code"]
    with client.websocket_connect(f"/ws/{other}?ticket={ticket}") as ws:
        assert ws.receive_json()["fatal"] is True  # ticket is for a different game

    stranger = client.post("/api/session/guest", json={"name": "Nope"}).json()["token"]
    assert client.post(f"/api/games/{code}/ticket", headers=auth(stranger)).status_code == 403
    assert client.post("/api/games", json={"name": "x"}, headers=auth("g.bogus.sig")).status_code == 401

    # Kicked accounts can't sneak back in
    with client.websocket_connect(f"/ws/{code}?ticket={client.post(f'/api/games/{code}/ticket', headers=auth(host['token'])).json()['ticket']}") as hws:
        hws.receive_json()
        hws.send_json({"action": "kick_player", "player_id": joined["player_id"]})
        for _ in range(20):
            if client.get(f"/api/games/{code}").json()["players"] == 1:
                break
            time.sleep(0.05)
    assert client.post(f"/api/games/{code}/join", json={"name": "Sam"}, headers=auth(member)).status_code == 400
