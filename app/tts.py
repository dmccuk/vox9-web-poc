import requests
from app.settings import settings

# Map our simple format to ElevenLabs output_format
_FORMAT_TO_EL_OUT = {
    "mp3": "mp3_44100_128",
    "wav": "pcm_44100",
}

def synthesize_elevenlabs(text: str) -> bytes:
    if not settings.ELEVEN_API_KEY:
        raise RuntimeError("ELEVEN_API_KEY not set")
    if not settings.ELEVEN_VOICE_ID:
        raise RuntimeError("ELEVEN_VOICE_ID not set")

    output_format = _FORMAT_TO_EL_OUT.get(settings.ELEVEN_OUTPUT_FORMAT.lower(), "mp3_44100_128")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVEN_VOICE_ID}"
    headers = {
        "xi-api-key": settings.ELEVEN_API_KEY,
        "accept": "audio/mpeg" if output_format.startswith("mp3") else "*/*",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": settings.ELEVEN_MODEL_ID,
        # Optional voice settings example:
        # "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
        "output_format": output_format,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    if not r.ok:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:500]}")
    return r.content
