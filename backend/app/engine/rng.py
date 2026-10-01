"""Deterministic randomness.

Every random decision derives its own stream from the game seed plus a
purpose label (``"roll", round, player_id`` …). That makes outcomes
reproducible regardless of the order in which players submit, and lets a
whole adventure be replayed from its seed and inputs.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def stream(seed: int, *keys: object) -> random.Random:
    material = ":".join([str(seed), *(str(k) for k in keys)])
    digest = hashlib.sha256(material.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def d20(seed: int, *keys: object) -> int:
    return stream(seed, "d20", *keys).randint(1, 20)


def pick(seed: int, options: Sequence[T], *keys: object) -> T:
    if not options:
        raise ValueError("cannot pick from an empty sequence")
    return options[stream(seed, "pick", *keys).randrange(len(options))]


def chance(seed: int, probability: float, *keys: object) -> bool:
    return stream(seed, "chance", *keys).random() < probability


def shuffled(seed: int, items: Sequence[T], *keys: object) -> list[T]:
    out = list(items)
    stream(seed, "shuffle", *keys).shuffle(out)
    return out
