"""TTS for Aria: Pure Sarvam AI with configurable SARVAM_MODEL and Shreya voice persona."""
import os
import re
import base64
import logging
from typing import Optional
import httpx

logger = logging.getLogger("app.services.tts")

SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
PRIMARY_SPEAKER = (os.getenv("SARVAM_SPEAKER", "shreya").strip().lower() or "shreya")
TARGET_LANGUAGE = os.getenv("SARVAM_LANGUAGE", "en-IN").strip() or "en-IN"
SENTENCE_SPLIT_PATTERN = re.compile(r"([.?!])(?:\s+|$)")

_sarvam_client: Optional[httpx.AsyncClient] = None


def get_sarvam_model() -> str:
    """Reads SARVAM_MODEL from environment if configured by user."""
    return os.getenv("SARVAM_MODEL", "").strip()


def get_sarvam_client() -> httpx.AsyncClient:
    """Reuses persistent AsyncClient to eliminate TLS handshake latency on streaming turns."""
    global _sarvam_client
    if _sarvam_client is None or _sarvam_client.is_closed:
        _sarvam_client = httpx.AsyncClient(
            timeout=8.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=60.0),
        )
    return _sarvam_client


class SentenceSplitter:
    """Buffers tokens and yields completed sentences on punctuation for low-latency voice."""
    def __init__(self):
        self.buffer = ""

    def process_token(self, token: str) -> list[str]:
        self.buffer += token
        ready = []
        while True:
            m = SENTENCE_SPLIT_PATTERN.search(self.buffer)
            if not m:
                break
            sent = self.buffer[:m.end()].strip()
            self.buffer = self.buffer[m.end():]
            if sent:
                ready.append(sent)
        return ready

    def flush(self) -> list[str]:
        rem = self.buffer.strip()
        self.buffer = ""
        return [rem] if rem else []


async def synthesize_sarvam(text: str, api_key: str) -> Optional[bytes]:
    """Synthesizes speech using Sarvam AI with SARVAM_MODEL and Shreya voice."""
    clean_text = text.strip()
    if not clean_text:
        return None

    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }
    
    sarvam_model = get_sarvam_model()
    payload = {
        "inputs": [clean_text],
        "target_language_code": TARGET_LANGUAGE,
        "speaker": PRIMARY_SPEAKER,
        "enable_preprocessing": True,
    }
    if sarvam_model:
        payload["model"] = sarvam_model

    client = get_sarvam_client()
    try:
        resp = await client.post(SARVAM_URL, json=payload, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            audios = data.get("audios", [])
            if audios:
                audio_bytes = base64.b64decode(audios[0])
                logger.info("Sarvam TTS (%s, model=%s) synthesized %d bytes for: '%s'", PRIMARY_SPEAKER, sarvam_model or "default", len(audio_bytes), clean_text[:40])
                return audio_bytes
            if "audio" in data:
                audio_bytes = base64.b64decode(data["audio"])
                logger.info("Sarvam TTS (%s, model=%s) synthesized %d bytes for: '%s'", PRIMARY_SPEAKER, sarvam_model or "default", len(audio_bytes), clean_text[:40])
                return audio_bytes
        else:
            logger.warning("Sarvam TTS HTTP %d: %s", resp.status_code, resp.text[:150])
            # If payload with model failed or vice versa, attempt fallback
            retry_payload = dict(payload)
            if "model" in retry_payload:
                del retry_payload["model"]
            else:
                retry_payload["model"] = "bulbul:v3"
            retry_resp = await client.post(SARVAM_URL, json=retry_payload, headers=headers)
            if retry_resp.status_code == 200:
                data = retry_resp.json()
                audios = data.get("audios", [])
                if audios:
                    return base64.b64decode(audios[0])
                if "audio" in data:
                    return base64.b64decode(data["audio"])
            else:
                logger.error("Sarvam TTS retry HTTP %d: %s", retry_resp.status_code, retry_resp.text[:150])
    except Exception as e:
        logger.error("Sarvam TTS request exception: %s", e)
    return None


async def synthesize_sentence(text: str) -> Optional[bytes]:
    """Generates audio for a single sentence via Sarvam AI Shreya voice."""
    clean = text.strip()
    if not clean:
        return None
    sarvam_key = os.getenv("SARVAM_API_KEY", "").strip()
    if sarvam_key and len(sarvam_key) > 5 and "your_" not in sarvam_key:
        return await synthesize_sarvam(clean, sarvam_key)
    logger.warning("Sarvam TTS skipped: SARVAM_API_KEY is not configured or placeholder.")
    return None
