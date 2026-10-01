"""Provider-independent voice configuration.

The game engine only ever speaks in ``VoiceConfig`` terms. Each provider owns
its own mapping from a style ("comedic") to a concrete voice.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class VoiceStyle(StrEnum):
    DRAMATIC = "dramatic"
    COMEDIC = "comedic"
    STORYTELLER = "storyteller"
    CHAOTIC = "chaotic"
    DEADPAN = "deadpan"


class VoiceConfig(BaseModel):
    voice_style: VoiceStyle = VoiceStyle.STORYTELLER
    language: str = Field(default="en-US", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


VOICE_DESCRIPTIONS = {
    VoiceStyle.DRAMATIC: "Booming, theatrical, every sentence a movie trailer.",
    VoiceStyle.COMEDIC: "Warm, playful, leaning into every punchline.",
    VoiceStyle.STORYTELLER: "A campfire narrator who has told this story a hundred times.",
    VoiceStyle.CHAOTIC: "Fast, excitable, barely holding it together.",
    VoiceStyle.DEADPAN: "A nature-documentary narrator who has seen too much.",
}

# Default speaking rates per style; providers multiply by VoiceConfig.speed.
STYLE_RATE = {
    VoiceStyle.DRAMATIC: 0.92, VoiceStyle.COMEDIC: 1.05, VoiceStyle.STORYTELLER: 1.0,
    VoiceStyle.CHAOTIC: 1.18, VoiceStyle.DEADPAN: 0.95,
}
