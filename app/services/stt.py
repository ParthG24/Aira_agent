"""Speech-to-Text (STT) Provider using Groq Whisper Large v3 Turbo."""
import io
import os
import wave
import logging
from typing import Tuple, Optional

logger = logging.getLogger("app.services.stt")

NOISE_TOKENS = {"[blank_audio]", "(music)", "(bell)", "(applause)", "[laughter]", "*", "...", ".", "!", "?", "--", "_"}
UNCONDITIONAL_HALLUCINATIONS = {
    "thank you for watching.", "thank you for watching", "thanks for watching.", "thanks for watching",
    "please subscribe.", "please subscribe", "like and subscribe", "subscribe to my channel",
    "subtitles by", "amara.org", "translated by", "watching", "subscribe"
}
SHORT_FILLERS = {"you", "the", "bye", "okay", "yeah", "uh", "um", "ah", "so", "oh", "and", "a", "i", "to"}


DISCARD_STANDALONE = {
    "the", "a", "an", "and", "to", "of", "in", "it", "is", "you", "i", "or",
    "so", "uh", "um", "ah", "oh", "eh", "hmm", "shh", "that", "this", "there",
    "the.", "the...", "the the", "you.", "and.", "to."
}


def clean_trailing_hallucinations(text: str) -> str:
    """Removes trailing dangling hallucinations like '... the' or repetitive filler tokens."""
    words = text.split()
    if len(words) > 1 and words[-1].lower().strip(".!?,") in {"the", "uh", "um"}:
        return " ".join(words[:-1]).strip()
    return text


def pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wraps raw 16-bit mono PCM bytes into an in-memory WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


PROMPT_HALLUCINATIONS = {
    "skincare customer support", "order tracking, returns", "customer support, order",
    "returns, cancellation", "order tracking", "customer support", "returns, or"
}


class GroqSTT:
    def __init__(self, api_key: str, model: Optional[str] = None):
        from groq import AsyncGroq
        self.client = AsyncGroq(api_key=api_key)
        self.model = model or os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo").strip() or "whisper-large-v3-turbo"

    async def transcribe(self, pcm_bytes: bytes) -> Tuple[bool, str]:
        """Transcribes PCM audio with acoustic & hallucination guardrails."""
        # Require at least 250ms of audio (4000 samples at 16kHz = 8000 bytes)
        if not pcm_bytes or len(pcm_bytes) < 4000:
            return False, ""

        # Pre-check audio energy: discard near-silent audio chunks / room hiss before calling API
        try:
            import audioop
            rms = audioop.rms(pcm_bytes, 2)
            if rms < 250:
                logger.debug("Discarding near-silent audio packet (RMS=%d)", rms)
                return False, ""
        except Exception:
            pass

        wav_bytes = pcm16_to_wav(pcm_bytes, 16000)
        try:
            resp = await self.client.audio.transcriptions.create(
                file=("audio.wav", wav_bytes, "audio/wav"),
                model=self.model,
                response_format="verbose_json",
                prompt="Aria Aura Skincare ORD-101 ORD-102 ORD-103",
                temperature=0.0,
            )

            text = resp.text.strip() if getattr(resp, "text", None) else ""
            segments = getattr(resp, "segments", [])
            avg_logprob = segments[0].avg_logprob if segments and hasattr(segments[0], "avg_logprob") else None
            no_speech_prob = segments[0].no_speech_prob if segments and hasattr(segments[0], "no_speech_prob") else None

            clean_text = text.strip()
            if not clean_text or clean_text.lower() in NOISE_TOKENS or not any(c.isalnum() for c in clean_text):
                logger.debug("Discarding empty or noise transcription: '%s'", clean_text)
                return False, ""

            # Discard standalone noise fillers & hallucinations
            normalized_clean = clean_text.lower().strip(".!?, ")
            if normalized_clean in DISCARD_STANDALONE or clean_text.lower() in DISCARD_STANDALONE:
                logger.info("Discarding standalone Whisper hallucination: '%s'", clean_text)
                return False, ""

            # Filter out prompt echoing hallucinations
            lower_clean = clean_text.lower()
            if any(ph in lower_clean for ph in PROMPT_HALLUCINATIONS):
                logger.info("Discarding Whisper prompt echo hallucination: '%s'", clean_text)
                return False, ""

            # Filter out very short junk outputs (e.g. single letters)
            alnums = [c for c in clean_text if c.isalnum()]
            if len(alnums) < 2:
                logger.debug("Discarding single-letter acoustic artifact: '%s'", clean_text)
                return False, ""

            if clean_text.lower() in UNCONDITIONAL_HALLUCINATIONS:
                logger.debug("Discarding unconditional Whisper hallucination: '%s'", clean_text)
                return False, ""

            # Discard if Whisper is confident it's non-speech (>0.85)
            if no_speech_prob and no_speech_prob > 0.85:
                logger.debug("Discarding audio due to high no_speech_prob (%.3f): '%s'", no_speech_prob, clean_text)
                return False, ""

            # Discard severely degraded low-confidence audio prone to phantom hallucinations
            if avg_logprob and avg_logprob < -1.80:
                logger.debug("Discarding audio due to low avg_logprob (%.3f): '%s'", avg_logprob, clean_text)
                return False, ""

            if clean_text.lower() in SHORT_FILLERS and no_speech_prob and no_speech_prob > 0.65:
                logger.debug("Discarding short filler without confident speech: '%s'", clean_text)
                return False, ""

            # Clean trailing dangling filler words like 'Order number 102 the' -> 'Order number 102'
            clean_text = clean_trailing_hallucinations(clean_text)
            if not clean_text or clean_text.lower().strip(".!?, ") in DISCARD_STANDALONE:
                return False, ""

            logger.info("STT valid transcription: '%s' (logprob=%.2f, no_speech=%.2f)", clean_text, avg_logprob or 0.0, no_speech_prob or 0.0)
            return True, clean_text

        except Exception as e:
            logger.error("STT transcription error: %s", e)
            return False, ""


def get_stt() -> GroqSTT:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return GroqSTT(api_key=key)


