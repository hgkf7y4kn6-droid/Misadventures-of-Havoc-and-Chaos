from ..base import TTSError, TTSProvider
from ..voices import VoiceConfig


class BrowserTTS(TTSProvider):
    """Fallback: the client narrates with the Web Speech API. Nothing to synthesise server-side."""

    name = "browser"
    server_side = False

    async def synthesize(self, text: str, voice: VoiceConfig) -> bytes:  # pragma: no cover - never called
        raise TTSError("browser narration happens on the client")
