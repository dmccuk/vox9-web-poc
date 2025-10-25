"""
Vox-9 captions toolkit (Phase 1, wrapped lines + forced style)
- Text cleanup & segmentation
- Caption writers: SRT / ASS / VTT (with safe line wrapping)
- Video render: black background + burned-in ASS subtitles over your audio
"""

from __future__ import annotations
import os
import re
import math
import tempfile
import subprocess
from typing import Dict, List, Tuple


# ------------------------- text utilities -------------------------

def clean_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_into_segments(text: str) -> List[str]:
    parts = re.split(r"\n\s*\n", text)
    segs: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        sents = re.split(r"(?<=[.!?])\s+", p)
        for s in sents:
            s = s.strip()
            if s:
                segs.append(s)
    return segs or ["…"]


def _estimate_durations(segs: List[str]) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for s in segs:
        words = max(1, len(s.split()))
        dur = max(1.2, min(7.0, words / 2.8))  # ~2.8 wps baseline
        out.append((s, float(dur)))
    return out


# ------------------------- timestamp helpers -------------------------

def _fmt_srt_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int((sec - int(sec)) * 100)  # centiseconds
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


# ------------------------- wrapping helpers -------------------------

def _wrap_text_for_width(text: str, width_px: int, font_px: int) -> str:
    """
    Hard-wrap `text` to fit within width_px using a rough per-character width.
    Heuristic: avg glyph width ~= 0.56 * font size (good for sans fonts).
    """
    avg_w = max(0.45, min(0.75, 0.56)) * max(10, font_px)
    max_cols = max(20, int(width_px / avg_w))

    words = text.split()
    lines: List[str] = []
    cur: List[str] = []
    cur_len = 0

    for w in words:
        if cur_len + (1 if cur else 0) + len(w) > max_cols:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            if cur:
                cur_len += 1 + len(w)
                cur.append(w)
            else:
                cur = [w]
                cur_len = len(w)
    if cur:
        lines.append(" ".join(cur))
    return r"\N".join(lines)  # ASS hard line-break


# ------------------------- caption writers -------------------------

def make_captions(text: str) -> Dict[str, List[Tuple[str, float]]]:
    segs = split_into_segments(clean_text(text))
    return {"segs": _estimate_durations(segs)}


def write_srt(segs: List[Tuple[str, float]]) -> str:
    t = 0.0
    out: List[str] = []
    for i, (line, dur) in enumerate(segs, start=1):
        out += [str(i), f"{_fmt_srt_ts(t)} --> {_fmt_srt_ts(t + dur)}", line, ""]
        t += dur
    return "\n".join(out).strip() + "\n"


def write_vtt(segs: List[Tuple[str, float]]) -> str:
    t = 0.0
    out: List[str] = ["WEBVTT", ""]
    for line, dur in segs:
        out += [f"{_fmt_srt_ts(t).replace(',', '.')} --> {_fmt_srt_ts(t + dur).replace(',', '.')}", line, ""]
        t += dur
    return "\n".join(out).strip() + "\n"


def write_ass(
    segs: List[Tuple[str, float]],
    *,
    font: str = "DejaVu Sans",       # safer default (present in container after Docker tweak)
    size: int = 64,
    bold: bool = False,
    italic: bool = False,
    resolution: str = "1080x1920",   # "W×H"
    margin_l: int = 80,
    margin_r: int = 80,
    margin_v: int = 120,
) -> str:
    # libass style header
    w, h = [int(x) for x in resolution.split("x")]
    style = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{int(size)},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"{-1 if bold else 0},{-1 if italic else 0},0,0,100,100,0,0,1,3,0,2,{margin_l},{margin_r},{margin_v},0\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # events with safe hard-wrapping
    usable_width = max(50, w - margin_l - margin_r)
    t = 0.0
    ev: List[str] = []
    for line, dur in segs:
        safe = _wrap_text_for_width(line, usable_width, size)
        ev.append(f"Dialogue: 0,{_fmt_ass_ts(t)},{_fmt_ass_ts(t+dur)},Default,,0,0,0,,{safe}")
        t += dur

    return style + "\n".join(ev) + "\n"


# ------------------------- video render (burn-in) -------------------------

def _run_ffmpeg(args: List[str]) -> None:
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore")[:1200])


def render_burned_mp4(
    audio_bytes: bytes,
    ass_text: str,
    *,
    audio_ext: str = "mp3",         # "mp3" or "wav" — just to choose temp suffix
    resolution: str = "1080x1920",  # "W×H"
    layout: str = "9:16",
    # force_style provides a last-resort override to libass at filter time
    force_style: str = "Alignment=2,WrapStyle=2,MarginL=80,MarginR=80,MarginV=120",
) -> bytes:
    """
    Compose a simple black video of given resolution, burn ASS subtitles, mux with audio.
    """
    # write temps
    a_suffix = ".wav" if audio_ext.lower() == "wav" else ".mp3"
    afd, a_path = tempfile.mkstemp(suffix=a_suffix); os.write(afd, audio_bytes); os.close(afd)
    sfd, s_path = tempfile.mkstemp(suffix=".ass"); os.write(sfd, ass_text.encode("utf-8")); os.close(sfd)
    v_path = a_path + ".mp4"

    # duration from ffprobe
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", a_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if probe.returncode != 0:
        raise RuntimeError("ffprobe failed for audio")
    try:
        dur = float(probe.stdout.decode().strip())
        if not (dur > 0):
            dur = 10.0
    except Exception:
        dur = 10.0

    # both inputs first; then apply video filter
    _run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s={resolution}:d={dur}",  # video input
        "-i", a_path,                                               # audio input
        "-vf", f"subtitles=filename='{s_path}':force_style='{force_style}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        v_path
    ])

    with open(v_path, "rb") as f:
        out = f.read()

    # cleanup
    for pth in (a_path, s_path, v_path):
        try: os.remove(pth)
        except Exception: pass

    return out
