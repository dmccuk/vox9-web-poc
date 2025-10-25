"""
Vox-9 asset pipeline (compat layer)
- Exposes DEFAULT_STYLE and generate_assets(...) for main.py
- Internally uses tts.generate_assets_from_story (per-sentence timing)
- Optionally renders MP4 burn-in using captions_toolkit.render_burned_mp4
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional, List
import tempfile

from app.tts import generate_assets_from_story  # new engine (per-sentence)
from app.captions_toolkit import render_burned_mp4

# Default style (phase 1 defaults; UI can override later)
DEFAULT_STYLE = {
    "font": "DejaVu Sans",     # reliable in container (fonts-dejavu)
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
    for k in ("font", "size", "bold", "italic", "resolution", "layout"):
        if k in style:
            s[k] = style[k]
    # normalize
    s["size"] = int(s.get("size", 64))
    s["bold"] = bool(s.get("bold", False))
    s["italic"] = bool(s.get("italic", False))
    s["resolution"] = str(s.get("resolution", "1080x1920"))
    s["layout"] = str(s.get("layout", "9:16"))
    return s


def generate_assets(
    *,
    story_text: str,
    voice_id: Optional[str],
    outputs: List[str],     # any of: mp3, wav, srt, ass, vtt, mp4
    style: Optional[Dict] = None,
) -> Dict[str, bytes]:
    """
    Backward-compatible API used by main.py.
    Returns { ext: bytes } for requested outputs.
    Uses the new TTS engine to produce audio + captions with accurate timing.
    """
    req = {o.lower() for o in outputs}
    cfg = _style_from_payload(style)

    # Work dir
    tmp_root = Path(tempfile.mkdtemp(prefix="vox9_pipeline_"))
    work = tmp_root / "assets"
    work.mkdir(parents=True, exist_ok=True)

    # Run TTS + captions (writes narration.wav/mp3/ass/srt/vtt into work/)
    paths = generate_assets_from_story(
        story_text=story_text,
        output_dir=work,
        voice_id=voice_id or "",  # your main.py will pass default from settings if not provided
        # Map style into TTS caption styling (ASS writer)
        font_name=cfg["font"],
        font_size=int(cfg["size"]),
        bold=bool(cfg["bold"]),
        italic=bool(cfg["italic"]),
        # (We keep other defaults like lead-in/out from tts.py)
    )

    # Collect requested outputs
    have: Dict[str, bytes] = {}

    # Helper to read if exists
    def _read_if(p: Path) -> Optional[bytes]:
        try:
            if p and p.exists() and p.is_file():
                return p.read_bytes()
        except Exception:
            pass
        return None

    p_wav = Path(paths.get("wav", "")) if paths.get("wav") else None
    p_mp3 = Path(paths.get("mp3", "")) if paths.get("mp3") else None
    p_ass = Path(paths.get("ass", "")) if paths.get("ass") else None
    p_srt = Path(paths.get("srt", "")) if paths.get("srt") else None
    p_vtt = Path(paths.get("vtt", "")) if paths.get("vtt") else None

    if "wav" in req and p_wav:
        b = _read_if(p_wav)
        if b is not None:
            have["wav"] = b

    if "mp3" in req and p_mp3:
        b = _read_if(p_mp3)
        if b is not None:
            have["mp3"] = b

    if "ass" in req and p_ass:
        b = _read_if(p_ass)
        if b is not None:
            have["ass"] = b

    if "srt" in req and p_srt:
        b = _read_if(p_srt)
        if b is not None:
            have["srt"] = b

    if "vtt" in req and p_vtt:
        b = _read_if(p_vtt)
        if b is not None:
            have["vtt"] = b

    # Build MP4 if requested (burn-in ASS over black background; mux audio)
    if "mp4" in req:
        # Prefer WAV for cleaner AAC encode; fallback to MP3
        audio_bytes = None
        audio_ext = "mp3"
        if p_wav:
            audio_bytes = _read_if(p_wav)
            audio_ext = "wav" if audio_bytes else "mp3"
        if audio_bytes is None and p_mp3:
            audio_bytes = _read_if(p_mp3)
            audio_ext = "mp3"

        if not audio_bytes:
            raise RuntimeError("MP4 requested but no audio was generated")

        ass_text = ""
        if p_ass:
            bt = _read_if(p_ass)
            if bt:
                ass_text = bt.decode("utf-8")
        if not ass_text:
            # If no ASS was produced for some reason, synth a trivial one-liner
            ass_text = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n" \
                       "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, " \
                       "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, " \
                       "Alignment, MarginL, MarginR, MarginV, Encoding\n" \
                       f"Style: Default,{cfg['font']},{cfg['size']},&H00FFFFFF,&H000000FF,&H00000000,&H80000000," \
                       f"{-1 if cfg['bold'] else 0},{-1 if cfg['italic'] else 0},0,0,100,100,0,0,1,3,0,2,80,80,120,0\n\n" \
                       "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

        mp4 = render_burned_mp4(
            audio_bytes=audio_bytes,
            ass_text=ass_text,
            audio_ext=audio_ext,
            resolution=cfg["resolution"],
            layout=cfg["layout"],
        )
        have["mp4"] = mp4

    return have
