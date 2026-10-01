import base64

import httpx

from ..base import TTSError, TTSProvider
from ..voices import STYLE_RATE, VoiceConfig, VoiceStyle

VOICES = {
    VoiceStyle.DRAMATIC: "Neural2-D", VoiceStyle.COMEDIC: "Neural2-J", VoiceStyle.STORYTELLER: "Neural2-F",
    VoiceStyle.CHAOTIC: "Neural2-I", VoiceStyle.DEADPAN: "Neural2-A",
}


class GoogleTTS(TTSProvider):
    name = "google"
    max_chars = 4500

    def __init__(self, api_key: str | None):
        if not api_key:
            raise TTSError("HAVOC_TTS_API_KEY is required for Google Cloud TTS")
        self._client = httpx.AsyncClient(base_url="https://texttospeech.googleapis.com/v1", timeout=120, params={"key": api_key})

    async def synthesize(self, text: str, voice: VoiceConfig) -> bytes:
        lang = voice.language if "-" in voice.language else "en-US"
        r = await self._client.post("/text:synthesize", json={
            "input": {"text": text},
            "voice": {"languageCode": lang, "name": f"{lang}-{VOICES[voice.voice_style]}"},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": round(voice.speed * STYLE_RATE[voice.voice_style], 2)},
        })
        if r.status_code >= 400:
            raise TTSError(f"Google TTS error {r.status_code}: {r.text[:200]}")
        return base64.b64decode(r.json()["audioContent"])

    async def aclose(self) -> None:
        await self._client.aclose()
