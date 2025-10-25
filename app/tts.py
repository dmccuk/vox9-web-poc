import time
import random
import requests
from typing import Optional, Dict, Any
from app.settings import settings

# Map our simple format to ElevenLabs output_format
_FORMAT_TO_EL_OUT = {
    "mp3": "mp3_44100_128",
    "wav": "pcm_44100",
}

def synthesize_elevenlabs(text: str, voice_id: Optional[str] = None, *, max_retries: int = 2) -> bytes:
    """
    Synthesize speech via ElevenLabs TTS.
    Retries lightly on transient 429/system_busy.
    """
    api_key = settings.ELEVEN_API_KEY
    voice_id = voice_id or settings.ELEVEN_VOICE_ID
    if not api_key:
        raise RuntimeError("ELEVEN_API_KEY not set")
    if not voice_id:
        raise RuntimeError("ELEVEN_VOICE_ID not set (or provide voice_id in request)")

    output_format = _FORMAT_TO_EL_OUT.get(settings.ELEVEN_OUTPUT_FORMAT.lower(), "mp3_44100_128")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg" if output_format.startswith("mp3") else "*/*",
        "content-type": "application/json",
    }
    payload: Dict[str, Any] = {
        "text": text,
        "model_id": settings.ELEVEN_MODEL_ID,
        "output_format": output_format,
        # You can add voice_settings here if you want later
        # "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}
    }

    for i in range(max_retries + 1):
        r = requests.post(url, headers=headers, json=payload, timeout=120)
        if r.ok:
            return r.content
        if r.status_code == 429 or "system_busy" in (r.text or ""):
            if i == max_retries:
                break
            time.sleep((2 ** i) + random.uniform(0, 0.5))
            continue
        r.raise_for_status()
    raise RuntimeError(f"ElevenLabs rate-limited or busy after retries.")

def list_voices() -> Dict:
    """
    Return voices from ElevenLabs.
    """
    if not settings.ELEVEN_API_KEY:
        raise RuntimeError("ELEVEN_API_KEY not set")
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": settings.ELEVEN_API_KEY}
    r = requests.get(url, headers=headers, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Voices error {r.status_code}: {r.text[:300]}")
    return r.json()
