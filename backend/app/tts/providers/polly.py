import asyncio
from xml.sax.saxutils import escape

from ..base import TTSError, TTSProvider
from ..voices import STYLE_RATE, VoiceConfig, VoiceStyle

VOICES = {
    VoiceStyle.DRAMATIC: "Matthew", VoiceStyle.COMEDIC: "Kevin", VoiceStyle.STORYTELLER: "Joanna",
    VoiceStyle.CHAOTIC: "Justin", VoiceStyle.DEADPAN: "Brian",
}


class PollyTTS(TTSProvider):
    """Amazon Polly via boto3 (credentials from the standard AWS chain)."""

    name = "polly"
    max_chars = 2800  # SSML wrapper counts toward Polly's 3000-char limit

    def __init__(self) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise TTSError("pip install boto3 to use Amazon Polly") from exc
        self._client = boto3.client("polly")

    async def synthesize(self, text: str, voice: VoiceConfig) -> bytes:
        rate = f"{int(voice.speed * STYLE_RATE[voice.voice_style] * 100)}%"
        ssml = f'<speak><prosody rate="{rate}">{escape(text)}</prosody></speak>'

        def call() -> bytes:
            resp = self._client.synthesize_speech(Text=ssml, TextType="ssml", OutputFormat="mp3", Engine="neural",
                                                  VoiceId=VOICES[voice.voice_style])
            return resp["AudioStream"].read()

        try:
            return await asyncio.to_thread(call)
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"Polly error: {exc}") from exc
