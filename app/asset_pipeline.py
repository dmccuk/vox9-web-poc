"""
Vox-9 asset pipeline (Phase 1)
- TTS via ElevenLabs (mp3/wav)
- Captions (SRT / ASS / VTT) with configurable style
- Optional MP4 with burned-in ASS subtitles
"""
from __future__ import annotations
from typing import Dict, Optional, List

from app.tts import synthesize_elevenlabs
from app.captions_toolkit import (
    make_captions, write_srt, write_vtt, write_ass, render_burned_mp4
)

# Default style (matches your desktop defaults)
DEFAULT_STYLE = {
    "font": "Inter",
    "size": 64,
    "bold": False,
    "italic": False,
    "resolution": "1080x1920",  # W×H
    "layout": "9:16",
}


def _style_from_payload(style: Optional[Dict]) -> Dict:
    s = dict(DEFAULT_STYLE)
    if not style:
        return s
    s.update({k: v for k, v in style.items() if k in s})
    return s


def generate_assets(
    *,
    story_text: str,
    voice_id: Optional[str],
    outputs: List[str],     # any of: mp3, wav, srt, ass, vtt, mp4
    style: Optional[Dict] = None,
) -> Dict[str, bytes]:
    """
    Returns a dict of { ext: bytes } where ext ∈ requested outputs.
    """
    req = {o.lower() for o in outputs}
    have: Dict[str, bytes] = {}

    # 1) captions (we always compute once; cheap)
    segs = make_captions(story_text)["segs"]
    cfg = _style_from_payload(style)
    if "srt" in req:
        have["srt"] = write_srt(segs).encode("utf-8")
    if "vtt" in req:
        have["vtt"] = write_vtt(segs).encode("utf-8")
    if "ass" in req or "mp4" in req:
        ass_text = write_ass(
            segs,
            font=cfg["font"], size=int(cfg["size"]),
            bold=bool(cfg["bold"]), italic=bool(cfg["italic"]),
            resolution=cfg["resolution"]
        ).encode("utf-8")
        have.setdefault("ass", ass_text)

    # 2) audio
    need_mp3 = "mp3" in req or "mp4" in req  # mp4 needs audio
    need_wav = "wav" in req
    if need_mp3 and not need_wav:
        have["mp3"] = synthesize_elevenlabs(story_text, voice_id=voice_id, out_format="mp3")
    elif need_wav and not need_mp3:
        have["wav"] = synthesize_elevenlabs(story_text, voice_id=voice_id, out_format="wav")
    elif need_mp3 and need_wav:
        # ask EL for MP3 then transcode to WAV in the web tier later if desired
        have["mp3"] = synthesize_elevenlabs(story_text, voice_id=voice_id, out_format="mp3")
        # WAV not auto-transcoded here; earlier scaffold did it, but not strictly required for Phase 1
        # If you’d like WAV always, uncomment the transcoding route in vox9_pipeline (or add pydub here).
        # For now, if caller requested WAV too, ask EL directly:
        have["wav"] = synthesize_elevenlabs(story_text, voice_id=voice_id, out_format="wav")

    # 3) mp4 with burned-in subs (ASS)
    if "mp4" in req:
        audio_bytes = have.get("wav") or have.get("mp3")
        if not audio_bytes:
            raise RuntimeError("MP4 requested but no audio was generated")
        audio_ext = "wav" if have.get("wav") else "mp3"
        mp4 = render_burned_mp4(
            audio_bytes,
            have["ass"].decode("utf-8"),
            audio_ext=audio_ext,
            resolution=cfg["resolution"],
            layout=cfg["layout"]
        )
        have["mp4"] = mp4

    return have
