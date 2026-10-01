"""@havoc/protocol (TypeScript) must match the engine's realtime contract."""

import re
import typing
from pathlib import Path

from app.models.events import ClientMessage, RealtimeEvent

TS = Path(__file__).resolve().parents[2] / "packages" / "protocol" / "src" / "events.ts"


def _ts_list(name: str) -> list[str]:
    body = re.search(rf"export const {name} = \[(.*?)\] as const", TS.read_text(), re.S).group(1)
    return re.findall(r'"([A-Za-z_]+)"', body)


def test_realtime_events_match():
    assert sorted(_ts_list("REALTIME_EVENTS")) == sorted(e.value for e in RealtimeEvent)


def test_client_actions_match():
    union = typing.get_args(typing.get_args(ClientMessage)[0])
    py_actions = {typing.get_args(m.model_fields["action"].annotation)[0] for m in union}
    assert sorted(_ts_list("CLIENT_ACTIONS")) == sorted(py_actions)
