from app.models.game import FinalStory, StoryChapter
from app.tts.base import TTSProvider
from app.tts.service import TTSService, chapter_script, split_for_provider
from app.tts.voices import VoiceConfig, VoiceStyle


class FakeTTS(TTSProvider):
    name = "fake"
    max_chars = 120

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, voice):
        assert len(text) <= self.max_chars
        self.calls.append(text)
        return b"ID3" + text.encode()[:10]


def story():
    return FinalStory(title="The Misadventures of Havoc and Chaos: Test", chapters=[
        StoryChapter(index=0, title="Chapter 1 — An Extremely Bad Idea", text="Para one. " * 30 + "\n\nPara two."),
        StoryChapter(index=1, title="Epilogue — Somehow, They Won", text="The end."),
    ])


def test_split_respects_limits():
    text = "Sentence number one is here. " * 50 + "\n\n" + "x" * 500
    chunks = split_for_provider(text, 200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks).replace("\n", "").replace(" ", "") .startswith("Sentencenumberone")


def test_chapter_script_includes_title_once():
    s = story()
    assert chapter_script(s, 0).startswith(s.title)
    assert not chapter_script(s, 1).startswith(s.title)


async def test_chapter_audio_is_chunked_and_cached(tmp_path, settings):
    settings.tts_cache_dir = str(tmp_path)
    fake = FakeTTS()
    svc = TTSService(fake, settings)
    s = story()
    v = VoiceConfig(voice_style=VoiceStyle.DEADPAN)
    p1 = await svc.chapter_audio(s, 0, v)
    n = len(fake.calls)
    assert n > 1 and p1.exists()
    p2 = await svc.chapter_audio(s, 0, v)
    assert p2 == p1 and len(fake.calls) == n  # cache hit
    ready = []

    async def on_chapter(i, ok):
        ready.append((i, ok))

    await svc.generate_all(s, v, on_chapter)
    assert ready == [(0, True), (1, True)]
    other = await svc.chapter_audio(s, 0, VoiceConfig(voice_style=VoiceStyle.CHAOTIC))
    assert other != p1  # voices are cached separately
