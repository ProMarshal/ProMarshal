"""Deepgram STT client for meeting audio transcription."""

import httpx
from typing import List, Dict, Any

from app.core.config import settings

DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"

# Deepgram query params — nova-2 model, diarization off (we handle speaker
# detection via DOM), punctuation on, timestamps on
DEEPGRAM_PARAMS = {
    "model": "nova-2",
    "punctuate": "true",
    "language": "en",
    "words": "true",
}


async def transcribe_audio_chunk(audio_bytes: bytes, content_type: str = "audio/webm") -> Dict[str, Any]:
    """Send an audio chunk to Deepgram and return transcript with word-level timestamps.

    Args:
        audio_bytes: Raw audio bytes (webm/opus from MediaRecorder)
        content_type: MIME type of the audio

    Returns:
        {
            "text": "full transcript text",
            "words": [{"word": "hello", "start": 0.5, "end": 0.9}, ...]
        }

    Raises:
        ValueError: If Deepgram API key is not configured
        httpx.HTTPError: If Deepgram API call fails
    """
    import logging
    logger = logging.getLogger(__name__)

    api_key = str(settings.deepgram_api_key or "").strip()
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY is not configured")

    # Deepgram wants "audio/webm" not "audio/webm;codecs=opus"
    clean_content_type = content_type.split(";")[0].strip() if content_type else "audio/webm"

    params = dict(DEEPGRAM_PARAMS)

    logger.info(
        "[deepgram] Sending request | bytes=%d content_type=%r clean=%r params=%s",
        len(audio_bytes), content_type, clean_content_type, params,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            DEEPGRAM_API_URL,
            params=params,
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": clean_content_type,
            },
            content=audio_bytes,
        )
        logger.info("[deepgram] Response status=%d body=%s", response.status_code, response.text[:300])
        response.raise_for_status()
        data = response.json()

    return _parse_deepgram_response(data)


def _parse_deepgram_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract text and word-level timestamps from Deepgram response."""
    try:
        channels = data["results"]["channels"]
        if not channels:
            return {"text": "", "words": []}

        alternatives = channels[0]["alternatives"]
        if not alternatives:
            return {"text": "", "words": []}

        best = alternatives[0]
        text = str(best.get("transcript") or "").strip()
        words = [
            {
                "word": w.get("word", ""),
                "start": w.get("start", 0.0),
                "end": w.get("end", 0.0),
            }
            for w in best.get("words") or []
        ]
        return {"text": text, "words": words}

    except (KeyError, IndexError, TypeError):
        return {"text": "", "words": []}
