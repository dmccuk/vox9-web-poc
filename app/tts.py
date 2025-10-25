import time
import random
import requests
from typing import Optional, Dict, Any, List, Tuple
from app.settings import settings

# Map our simple format to ElevenLabs output_format
_FORMAT_TO_EL_OUT = {
    "mp3": "mp3_44100_128",
    "wav": "pcm_44100",
}

# Your favorites (name, voice_id)
DEFAULT_FAVORITE_VOICES: List[Tuple[str, str]] = [
    ("Adam", "pNInz6obpgDQGcFmaJgB"),
    ("Antoni", "ErXwobaYiN019PkySvjV"),
    ("Jessie", "t0jbNlBVZ17f02VDIeMI"),
    ("Dave - Texan middle aged", "3ozl8hsxdYYNRhFU44aP"),
    ("Michael", "flq6f7yk4E4fJM5XTYuZ"),
    ("Brian", "nPczCjzI2devNBz1zQrb"),
]

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
        # Add voice_settings here later if you want:
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
    raise RuntimeError("ElevenLabs rate-limited or busy after retries.")

def list_voices() -> dict:
    """
    Return voices from ElevenLabs + favorites merged.
    Always returns {"voices":[{"voice_id","name"},...]} (favorites first).
    """
    out = {"voices": []}

    # Start with favorites
    favorites = [{"voice_id": vid, "name": name} for (name, vid) in DEFAULT_FAVORITE_VOICES]
    out["voices"].extend(favorites)

    # Merge in live API voices (if API key provided)
    if settings.ELEVEN_API_KEY:
        url = "https://api.elevenlabs.io/v1/voices"
        headers = {"xi-api-key": settings.ELEVEN_API_KEY}
        try:
            r = requests.get(url, headers=headers, timeout=60)
            if r.ok:
                data = r.json()
                seen = {v["voice_id"] for v in out["voices"]}
                for v in data.get("voices", []) or []:
                    vid = v.get("voice_id")
                    name = v.get("name")
                    if vid and vid not in seen:
                        out["voices"].append({"voice_id": vid, "name": name})
                        seen.add(vid)
        except Exception:
            # Ignore API failure; favorites still shown
            pass

    return out
