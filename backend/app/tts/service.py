"""Narration pipeline: chapter segmentation → TTS per chunk → cache → sequential playback.

Final Story → Chapter Segmentation → TTS per Chapter → Audio Cache → Sequential Playback
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..config import Settings
from ..models.game import FinalStory
from .base import TTSError, TTSProvider
from .providers.browser import BrowserTTS
from .voices import VoiceConfig, VoiceStyle

log = logging.getLogger("havoc.tts")


def build_tts_provider(settings: Settings) -> TTSProvider:
    kind = (settings.tts_provider or "browser").lower()
    try:
        if kind == "openai":
            from .providers.openai_tts import OpenAITTS

            return OpenAITTS(settings.tts_api_key)
        if kind == "elevenlabs":
            from .providers.elevenlabs import ElevenLabsTTS

            return ElevenLabsTTS(settings.tts_api_key)
        if kind == "google":
            from .providers.google_tts import GoogleTTS

            return GoogleTTS(settings.tts_api_key)
        if kind == "polly":
            from .providers.polly import PollyTTS

            return PollyTTS()
    except TTSError as exc:
        log.warning("TTS provider %s unavailable (%s); falling back to browser narration", kind, exc)
    return BrowserTTS()


def chapter_script(story: FinalStory, index: int) -> str:
    """The text read aloud for one chapter (title + body; story title before chapter 1)."""
    ch = story.chapters[index]
    prefix = f"{story.title}. " if index == 0 else ""
    return f"{prefix}{ch.title}.\n\n{ch.text}"


def split_for_provider(text: str, max_chars: int) -> list[str]:
    """Split on paragraph, then sentence boundaries so no request exceeds the provider's limit."""
    chunks: list[str] = []
    current = ""
    for para in re.split(r"\n\s*\n", text):
        pieces = [para] if len(para) <= max_chars else re.split(r"(?<=[.!?])\s+", para)
        for piece in pieces:
            while len(piece) > max_chars:  # pathological sentence
                chunks.append(piece[:max_chars])
                piece = piece[max_chars:]
            if len(current) + len(piece) + 2 > max_chars and current:
                chunks.append(current.strip())
                current = ""
            current += piece + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


class TTSService:
    def __init__(self, provider: TTSProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.cache_dir = Path(settings.tts_cache_dir)
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def server_side(self) -> bool:
        return self.provider.server_side

    def _key(self, text: str, voice: VoiceConfig) -> str:
        material = f"{self.provider.name}|{voice.model_dump_json()}|{text}"
        return hashlib.sha256(material.encode()).hexdigest()[:32]

    def cached_path(self, story: FinalStory, index: int, voice: VoiceConfig) -> Path:
        return self.cache_dir / f"{self._key(chapter_script(story, index), voice)}.mp3"

    async def chapter_audio(self, story: FinalStory, index: int, voice: VoiceConfig) -> Path:
        """Return a cached file for the chapter, synthesising (chunk by chunk) on first request."""
        if not self.provider.server_side:
            raise TTSError("browser narration")
        if not 0 <= index < len(story.chapters):
            raise IndexError(index)
        path = self.cached_path(story, index, voice)
        lock = self._locks.setdefault(str(path), asyncio.Lock())
        async with lock:
            if path.exists():
                return path
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            parts = []
            for chunk in split_for_provider(chapter_script(story, index), min(self.provider.max_chars, self.settings.tts_max_chars_per_request)):
                for attempt in range(3):
                    try:
                        parts.append(await self.provider.synthesize(chunk, voice))
                        break
                    except TTSError:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(1.5 * (attempt + 1))
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(b"".join(parts))  # MP3 frames concatenate cleanly
            tmp.replace(path)
            return path

    async def generate_all(self, story: FinalStory, voice: VoiceConfig,
                           on_chapter: Callable[[int, bool], Awaitable[None]]) -> None:
        """Pre-generate every chapter in order so playback can start on chapter 1 immediately."""
        for i in range(len(story.chapters)):
            ok = True
            try:
                await self.chapter_audio(story, i, voice)
            except Exception:  # noqa: BLE001 - a failed chapter can be retried on demand
                log.exception("narration failed for chapter %s", i)
                ok = False
            await on_chapter(i, ok)

    async def aclose(self) -> None:
        await self.provider.aclose()


def voice_for(style: str | None, speed: float = 1.0) -> VoiceConfig:
    try:
        return VoiceConfig(voice_style=VoiceStyle(style or "storyteller"), speed=speed)
    except ValueError:
        return VoiceConfig()
