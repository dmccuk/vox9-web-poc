"""
Vox-9 captions toolkit (Phase 1)
- Text cleanup & segmentation
- Caption writers: SRT / ASS / VTT
- Video render: black background + burned-in ASS subtitles over your audio
  (uses ffmpeg + libass; good defaults; style is configurable)
"""
from __future__ import annotations
import os
import re
import tempfile
import subprocess
from typing import Dict, List, Tuple


# ------------------------- text utilities -------------------------

def clean_text(raw: str) -> str:
    """Light cleanup. (Keep yours more advanced here later.)"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_into_segments(text: str) -> List[str]:
    """
    Very simple segmentation: split on blank lines or sentence punctuation.
    Replace with your robust logic later.
    """
    # split paragraphs first
    parts = re.split(r"\n\s*\n", text)
    segs: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # split sentences in the paragraph (., ?, !)
        sents = re.split(r"(?<=[.!?])\s+", p)
        for s in sents:
            s = s.strip()
            if s:
                segs.append(s)
    return segs or ["…"]


def _estimate_durations(segs: List[str]) -> List[Tuple[str, float]]:
    """
    Extremely crude timing estimate. Replace with your alignment later.
    ~170 wpm ~ 2.8 wps; scale by length.
    """
    out: List[Tuple[str, float]] = []
    for s in segs:
        words = max(1, len(s.split()))
        dur = max(1.2, min(7.0, words / 2.8))  # 2.8 words/sec baseline
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


# ------------------------- caption writers -------------------------

def make_captions(text: str) -> Dict[str, List[Tuple[str, float]]]:
    """
    Produce segments with estimated durations.
    Returns {"segs": [(text, dur_sec), ...]}
    """
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
    font: str = "Inter",
    size: int = 64,
    bold: bool = False,
    italic: bool = False,
    resolution: str = "1080x1920",  # "W×H"
) -> str:
    # libass style header
    w, h = resolution.split("x")
    style = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(w)}\n"
        f"PlayResY: {int(h)}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{int(size)},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"{-1 if bold else 0},{-1 if italic else 0},0,0,100,100,0,0,1,3,0,2,80,80,120,0\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # events
    t = 0.0
    ev: List[str] = []
    for line, dur in segs:
        ev.append(f"Dialogue: 0,{_fmt_ass_ts(t)},{_fmt_ass_ts(t+dur)},Default,,0,0,0,,{line}")
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
    layout: str = "9:16",           # informational; we compute size from resolution
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

    # video synth + subtitles
    _run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s={resolution}:d={dur}",
        "-vf", f"subtitles='{s_path}'",
        "-i", a_path,
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
