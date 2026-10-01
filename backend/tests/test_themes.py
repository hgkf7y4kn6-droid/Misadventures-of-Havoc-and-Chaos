from app.engine import themes
from app.models.game import ThemeOption, ThemeSubmission


def subs(*texts):
    return [ThemeSubmission(player_id=f"p{i}", text=t, submitted_at=i) for i, t in enumerate(texts)]


def test_semantic_duplicates_merge():
    opts = themes.build_options(subs("Pirates steal the moon.", "Pirates trying to rob the moon.", "Space pirates stealing the moon."))
    assert len(opts) == 1
    assert len(opts[0].originals) == 3
    assert "Pirates" in opts[0].title and "Moon" in opts[0].title


def test_distinct_themes_stay_separate():
    opts = themes.build_options(subs(
        "Office workers trapped inside a haunted Costco",
        "Medieval knights attempting to deliver a pizza before it gets cold",
        "A group of raccoons accidentally becomes the government",
        "Pirates steal the moon",
    ))
    assert len(opts) == 4


def test_transitive_clustering():
    opts = themes.build_options(subs("Pirates steal the moon", "Pirates rob the moon with a ladder", "Pirates rob a ladder"))
    assert len(opts) <= 2


def test_llm_judge_used_for_borderline():
    calls = []

    def judge(a, b):
        calls.append((a, b))
        return True

    texts = ["Knights deliver a pizza", "Knights bring pizza to a castle"]
    themes.cluster(texts, judge=judge)
    # either lexically merged outright or the judge was consulted
    assert calls or themes.lexical_similarity(*texts) >= themes.DUPLICATE_THRESHOLD


def test_tally_ties_are_deterministic():
    a, b = ThemeOption(id="a", title="A"), ThemeOption(id="b", title="B")
    votes = {"p1": "a", "p2": "b"}
    w1, _ = themes.tally([a, b], votes, seed=5)
    w2, _ = themes.tally([b, a], votes, seed=5)
    assert w1.id == w2.id


def test_tally_majority():
    a, b = ThemeOption(id="a", title="A"), ThemeOption(id="b", title="B")
    winner, counts = themes.tally([a, b], {"p1": "b", "p2": "b", "p3": "a"}, seed=1)
    assert winner.id == "b" and counts == {"a": 1, "b": 2}
