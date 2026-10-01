"""Text-to-speech provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .voices import VoiceConfig


class TTSError(RuntimeError):
    pass


class TTSProvider(ABC):
    name: str = "abstract"
    #: False means narration happens in the browser (speechSynthesis); the server only supplies text.
    server_side: bool = True
    media_type: str = "audio/mpeg"
    max_chars: int = 3800

    @abstractmethod
    async def synthesize(self, text: str, voice: VoiceConfig) -> bytes:
        """Return encoded audio for ``text`` (≤ ``max_chars``)."""

    async def aclose(self) -> None:
        return None
