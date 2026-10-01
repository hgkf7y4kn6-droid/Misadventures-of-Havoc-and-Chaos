"""Theme normalisation, semantic duplicate detection, and vote tallying.

Duplicate detection is hybrid:

1. deterministic canonicalisation (synonym folding, stopword removal, crude stemming),
2. a lexical similarity score (token Jaccard over canonical concepts + character trigrams),
3. optional embedding cosine similarity when the LLM provider supports embeddings,
4. optional LLM classification for borderline pairs (see ``llm.services.ThemeMerger``).

Clusters are formed with union-find so A~B and B~C merge into one option.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable

from ..models.game import ThemeOption, ThemeSubmission
from . import rng
from .procgen import NOUN_CANON, STOPWORDS, VERB_CANON

EXTRA_STOP = {"space", "giant", "little", "big", "evil", "group", "team", "crew", "gang", "bunch", "some", "all"}
DUPLICATE_THRESHOLD = 0.6
BORDERLINE = (0.35, 0.6)


def _stem(word: str) -> str:
    for suf in ("ing", "ers", "er", "es", "s", "ed"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


def concepts(text: str) -> list[str]:
    """Canonical concept tokens: 'Space pirates stealing the moon' -> ['pirate', 'steal', 'moon']."""
    words = re.findall(r"[a-z']+", text.lower())
    out: list[str] = []
    for w in words:
        if w in STOPWORDS:
            continue
        canon = VERB_CANON.get(w) or NOUN_CANON.get(w)
        if canon is None:
            if w in EXTRA_STOP or len(w) < 3:
                continue
            canon = _stem(w)
        if canon not in out:
            out.append(canon)
    return out


def normalize(text: str) -> str:
    return " ".join(concepts(text))


def _trigrams(text: str) -> set[str]:
    t = f"  {text} "
    return {t[i : i + 3] for i in range(len(t) - 2)}


def lexical_similarity(a: str, b: str) -> float:
    ca, cb = set(concepts(a)), set(concepts(b))
    if not ca or not cb:
        return 0.0
    jaccard = len(ca & cb) / len(ca | cb)
    # containment: "pirates steal the moon" vs "pirates steal the moon with a ladder"
    containment = len(ca & cb) / min(len(ca), len(cb))
    ta, tb = _trigrams(" ".join(sorted(ca))), _trigrams(" ".join(sorted(cb)))
    tri = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return 0.45 * jaccard + 0.35 * containment + 0.2 * tri


def cosine(u: list[float], v: list[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(y * y for y in v))
    return dot / (nu * nv) if nu and nv else 0.0


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def cluster(
    texts: list[str],
    embeddings: list[list[float]] | None = None,
    judge: Callable[[str, str], bool | None] | None = None,
) -> list[list[int]]:
    """Group indices of semantically duplicate texts."""
    n = len(texts)
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            score = lexical_similarity(texts[i], texts[j])
            if embeddings:
                score = max(score, (cosine(embeddings[i], embeddings[j]) - 0.55) / 0.35)
            same = score >= DUPLICATE_THRESHOLD
            if not same and judge and BORDERLINE[0] <= score < BORDERLINE[1]:
                same = bool(judge(texts[i], texts[j]))
            if same:
                uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def merged_title(originals: Iterable[str]) -> str:
    """Deterministic title for a cluster: the most 'central' submission, cleaned up."""
    from .procgen import profile_theme, title_case

    originals = list(originals)
    if len(originals) == 1:
        return title_case(originals[0].strip().rstrip("."))[:120]
    best = max(originals, key=lambda t: (sum(lexical_similarity(t, o) for o in originals), -len(t)))
    p = profile_theme(best)
    subject = re.sub(r"^(space|some|the)\s+", "", p.subject, flags=re.I)
    verb_phrase = p.goal
    words = verb_phrase.split()
    if words and VERB_CANON.get(words[0].lower()) == "steal":
        verb_phrase = "steal " + " ".join(words[1:])
    if subject and verb_phrase and subject.lower() not in ("heroes",):
        return title_case(f"{subject} attempt to {verb_phrase}")[:120]
    return title_case(best.rstrip("."))[:120]


def build_options(
    submissions: list[ThemeSubmission],
    embeddings: list[list[float]] | None = None,
    judge: Callable[[str, str], bool | None] | None = None,
    titler: Callable[[list[str]], str] | None = None,
) -> list[ThemeOption]:
    submissions = sorted(submissions, key=lambda s: (s.submitted_at, s.player_id))
    texts = [s.text for s in submissions]
    options = []
    for group in cluster(texts, embeddings, judge):
        originals = [texts[i] for i in group]
        title = (titler(originals) if titler else None) or merged_title(originals)
        options.append(
            ThemeOption(title=title, originals=originals, player_ids=[submissions[i].player_id for i in group])
        )
    return options


def tally(options: list[ThemeOption], votes: dict[str, str], seed: int) -> tuple[ThemeOption, dict[str, int]]:
    """Count votes. Ties: most original submitters, then a seeded draw (deterministic)."""
    counts = {o.id: 0 for o in options}
    for option_id in votes.values():
        if option_id in counts:
            counts[option_id] += 1
    top = max(counts.values()) if counts else 0
    tied = [o for o in options if counts[o.id] == top]
    most_backed = max(len(o.player_ids) for o in tied)
    tied = [o for o in tied if len(o.player_ids) == most_backed]
    tied.sort(key=lambda o: o.id)
    winner = tied[0] if len(tied) == 1 else rng.pick(seed, tied, "theme_tie")
    return winner, counts
