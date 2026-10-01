import httpx

from ..base import TTSError, TTSProvider
from ..voices import STYLE_RATE, VoiceConfig, VoiceStyle

VOICES = {
    VoiceStyle.DRAMATIC: "onyx", VoiceStyle.COMEDIC: "fable", VoiceStyle.STORYTELLER: "nova",
    VoiceStyle.CHAOTIC: "echo", VoiceStyle.DEADPAN: "alloy",
}
INSTRUCTIONS = {
    VoiceStyle.DRAMATIC: "Narrate like an epic movie trailer.", VoiceStyle.COMEDIC: "Narrate playfully, landing the jokes.",
    VoiceStyle.STORYTELLER: "Narrate like a warm campfire storyteller.", VoiceStyle.CHAOTIC: "Narrate fast and excitable.",
    VoiceStyle.DEADPAN: "Narrate in a dry, deadpan documentary style.",
}


class OpenAITTS(TTSProvider):
    name = "openai"
    max_chars = 4000

    def __init__(self, api_key: str | None, model: str = "gpt-4o-mini-tts"):
        if not api_key:
            raise TTSError("HAVOC_TTS_API_KEY is required for OpenAI TTS")
        self.model = model
        self._client = httpx.AsyncClient(base_url="https://api.openai.com/v1", timeout=120,
                                         headers={"Authorization": f"Bearer {api_key}"})

    async def synthesize(self, text: str, voice: VoiceConfig) -> bytes:
        r = await self._client.post("/audio/speech", json={
            "model": self.model, "input": text, "voice": VOICES[voice.voice_style], "response_format": "mp3",
            "speed": round(voice.speed * STYLE_RATE[voice.voice_style], 2), "instructions": INSTRUCTIONS[voice.voice_style],
        })
        if r.status_code >= 400:
            raise TTSError(f"OpenAI TTS error {r.status_code}: {r.text[:200]}")
        return r.content

    async def aclose(self) -> None:
        await self._client.aclose()
