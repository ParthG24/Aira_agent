"""TTS compatibility module for Aria (app.tts -> app.services.tts).

Uses Sarvam AI with configurable SARVAM_MODEL and Shreya voice persona.
Zero Edge-TTS / Neerja dependency.
"""
from app.services.tts import (
    SentenceSplitter,
    synthesize_sarvam,
    synthesize_sentence,
    get_sarvam_model,
    PRIMARY_SPEAKER,
    TARGET_LANGUAGE,
    SARVAM_URL,
)

__all__ = [
    "SentenceSplitter",
    "synthesize_sarvam",
    "synthesize_sentence",
    "get_sarvam_model",
    "PRIMARY_SPEAKER",
    "TARGET_LANGUAGE",
    "SARVAM_URL",
]
