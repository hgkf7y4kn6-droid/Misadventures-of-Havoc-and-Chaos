import json
import os

import httpx

from ..base import TTSError, TTSProvider
from ..voices import VoiceConfig, VoiceStyle

# Public premade voice ids; override with HAVOC_ELEVENLABS_VOICES='{"comedic": "<voice id>", ...}'
DEFAULT_VOICES = {
    VoiceStyle.DRAMATIC: "pNInz6obpgDQGcFmaJgB", VoiceStyle.COMEDIC: "TxGEqnHWrfWFTfGW9XjX",
    VoiceStyle.STORYTELLER: "EXAVITQu4vr4xnSDxMaL", VoiceStyle.CHAOTIC: "ErXwobaYiN019PkySvjV",
    VoiceStyle.DEADPAN: "VR6AewLTigWG4xSOukaG",
}


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"
    max_chars = 2500

    def __init__(self, api_key: str | None):
        if not api_key:
            raise TTSError("HAVOC_TTS_API_KEY is required for ElevenLabs")
        overrides = json.loads(os.environ.get("HAVOC_ELEVENLABS_VOICES", "{}") or "{}")
        self.voices = {**DEFAULT_VOICES, **{VoiceStyle(k): v for k, v in overrides.items()}}
        self._client = httpx.AsyncClient(base_url="https://api.elevenlabs.io/v1", timeout=120, headers={"xi-api-key": api_key})

    async def synthesize(self, text: str, voice: VoiceConfig) -> bytes:
        r = await self._client.post(f"/text-to-speech/{self.voices[voice.voice_style]}", json={
            "text": text, "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.35 if voice.voice_style == VoiceStyle.CHAOTIC else 0.6, "similarity_boost": 0.75,
                               "speed": voice.speed},
        }, headers={"Accept": "audio/mpeg"})
        if r.status_code >= 400:
            raise TTSError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
        return r.content

    async def aclose(self) -> None:
        await self._client.aclose()
