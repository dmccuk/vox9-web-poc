"""
Vox-9 asset pipeline (simplified)
Now defaults to 1920x1080 widescreen and same style as Tk app.
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional, List
import tempfile

from app.tts import generate_assets_from_story
from app.captions_toolkit import render_burned_mp4

# ---------- Default widescreen style ----------
DEFAULT_STYLE = {
    "font": "DejaVu Sans",
    "size": 65,
    "bold": False,
    "italic": False,
    "resolution": "1920x1080",  # widescreen
    "layout": "16:9",
}

def _style_from_payload(style: Optional[Dict]) -> Dict:
    s = dict(DEFAULT_STYLE)
    if not style:
        return s
    for k in ("font", "size", "bold", "italic", "resolution", "layout"):
        if k in style:
            s[k] = style[k]
    s["size"] = int(s.get("size", 65))
    s["bold"] = bool(s.get("bold", False))
    s["italic"] = bool(s.get("italic", False))
    s["resolution"] = str(s.get("resolution", "1920x1080"))
    s["layout"] = str(s.get("layout", "16:9"))
    return s

def generate_assets(
    *,
    story_text: str,
    voice_id: Optional[str],
    outputs: List[str],
    style: Optional[Dict] = None,
) -> Dict[str, bytes]:
    """
    Wrapper for FastAPI endpoint. Builds audio + captions, returns requested assets.
    """
    req = {o.lower() for o in outputs}
    cfg = _style_from_payload(style)

    tmp_root = Path(tempfile.mkdtemp(prefix="vox9_pipeline_"))
    work = tmp_root / "assets"
    work.mkdir(parents=True, exist_ok=True)

    paths = generate_assets_from_story(
        story_text=story_text,
        output_dir=work,
        voice_id=voice_id or "",
        font_name=cfg["font"],
        font_size=int(cfg["size"]),
        bold=bool(cfg["bold"]),
        italic=bool(cfg["italic"]),
        resolution=cfg["resolution"],
    )

    have: Dict[str, bytes] = {}
    def _read_if(p: Path) -> Optional[bytes]:
        try:
            if p and p.exists():
                return p.read_bytes()
        except Exception:
            pass
        return None

    for key in ["wav", "mp3", "ass", "srt", "vtt"]:
        p = Path(paths.get(key, "")) if paths.get(key) else None
        if p and key in req:
            b = _read_if(p)
            if b is not None:
                have[key] = b

    if "mp4" in req:
        audio_bytes = None
        audio_ext = "mp3"
        p_wav = Path(paths.get("wav", "")) if paths.get("wav") else None
        if p_wav and p_wav.exists():
            audio_bytes = _read_if(p_wav)
            audio_ext = "wav"
        elif paths.get("mp3"):
            audio_bytes = _read_if(Path(paths["mp3"]))
            audio_ext = "mp3"
        if not audio_bytes:
            raise RuntimeError("MP4 requested but no audio found")
        ass_text =
