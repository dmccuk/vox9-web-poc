"""
Vox-9 captions toolkit — Single-line cinematic captions (Phase 1.1)

• Segments are ONE line each (no multi-line stacks).
• Long sentences are split into sequential one-liners (phrases).
• Safe margins + center-bottom alignment by default.
• render_burned_mp4 burns ASS using libass with force_style.

Later (Phase 2) we can pass UI-chosen style to these functions, including char_width.
"""

from __future__ import annotations
import os
import re
import tempfile
import subprocess
from typing import Dict, List, Tuple


# ---------- basic cleanup ----------

def clean_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------- segmentation helpers ----------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_SPLIT = re.compile(r"\s*[,;:—–-]\s*")   # commas, semicolons, dashes


def _estimate_max_cols(
    *,
    resolution: str = "1080x1920",
    margin_l: int = 80,
    margin_r: int = 80,
    font_px: int = 64,
    char_width: float = 0.56,
) -> int:
    """
    Estimate how many characters comfortably fit on one line for libass.

    char_width ~= average glyph width as a fraction of font size (0.56 works well for sans fonts).
    We'll make this adjustable from the UI later.
    """
    try:
        w = int(resolution.split("x")[0])
    except Exception:
        w = 1080
    usable_width = max(50, w - margin_l - margin_r)
    avg_px = max(10.0, font_px * max(0.3, min(0.9, char_width)))
    max_cols = int(usable_width / avg_px)
    return max(18, min(80, max_cols))  # sane bounds


def _phrase_chunk(words: List[str], max_cols: int) -> List[str]:
    """
    Greedy word-wrapping into single-line phrases, capped by max_cols characters.
    Each returned string is <= max_cols characters (approx).
    """
    lines: List[str] = []
    cur: List[str] = []
    cur_len = 0

    for w in words:
        wlen = len(w)
        if not cur:
            cur.append(w)
            cur_len = wlen
            continue
        # +1 for space between words
        if cur_len + 1 + wlen > max_cols:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = wlen
        else:
            cur.append(w)
            cur_len += 1 + wlen

    if cur:
        lines.append(" ".join(cur))
    return lines


def _split_sentence_to_phrases(
    sent: str,
    *,
    resolution: str = "1080x1920",
    margin_l: int = 80,
    margin_r: int = 80,
    font_px: int = 64,
    char_width: float = 0.56,
) -> List[str]:
    """
    Split a sentence into 1..N single-line phrases. Prefer clause boundaries,
    then fall back to greedy word chunks so each line fits on one screen line.
    """
    max_cols = _estimate_max_cols(
        resolution=resolution,
        margin_l=margin_l, margin_r=margin_r,
        font_px=font_px, char_width=char_width
    )

    # First split on clause separators (commas, semicolons, dashes)
    # then pack each clause into single-line chunks.
    rough_clauses = [c.strip() for c in _CLAUSE_SPLIT.split(sent) if c.strip()]
    out: List[str] = []
    for clause in rough_clauses:
        out.extend(_phrase_chunk(clause.split(), max_cols))

    return out or [sent]


def split_into_segments_single_line(
    text: str,
    *,
    resolution: str = "1080x1920",
    margin_l: int = 80,
    margin_r: int = 80,
    font_px: int = 64,
    char_width: float = 0.56,
) -> List[str]:
    """
    Paragraphs → sentences → one-line phrases.
    Always yields single-line strings; long sentences become multiple sequential entries.
    """
    text = clean_text(text)
    # paragraphs (blank lines)
    paragraphs = re.split(r"\n\s*\n", text)

    segments: List[str] = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # sentences
        for sent in _SENT_SPLIT.split(p):
            sent = sent.strip()
            if not sent:
                continue
            segments.extend(
                _split_sentence_to_phrases(
                    sent,
                    resolution=resolution,
                    margin_l=margin_l, margin_r=margin_r,
                    font_px=font_px, char_width=char_width
                )
            )

    return segments or ["…"]


# ---------- duration model ----------

def _estimate_durations(segs: List[str]) -> List[Tuple[str, float]]:
    """
    Single-line duration based on words/second. We bias a bit longer than usual to feel cinematic.
    """
    out: List[Tuple[str, float]] = []
    for s in segs:
        words = max(1, len(s.split()))
        # ~2.4 wps feels more legible for single-line captions
        dur = max(1.2, min(7.0, words / 2.4))
        out.append((s, float(dur)))
    return out


# ---------- public caption API ----------

def make_captions(
    text: str,
    *,
    resolution: str = "1080x1920",
    margin_l: int = 80,
    margin_r: int = 80,
    font_px: int = 64,
    char_width: float = 0.56,
) -> Dict[str, List[Tuple[str, float]]]:
    """
    Produce segments (single-line phrases) with durations.
    Returns: {"segs": [(text, dur_sec), ...]}
    """
    segs = split_into_segments_single_line(
        text,
        resolution=resolution, margin_l=margin_l, margin_r=margin_r,
        font_px=font_px, char_width=char_width
    )
    return {"segs": _estimate_durations(segs)}


# ---------- timestamp helpers ----------

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


# ---------- writers: SRT / VTT / ASS ----------

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
    font: str = "DejaVu Sans",       # reliable default in container
    size: int = 64,
    bold: bool = False,
    italic: bool = False,
    resolution: str = "1080x1920",   # "W×H"
    margin_l: int = 80,
    margin_r: int = 80,
    margin_v: int = 120,
) -> str:
    w, h = [int(x) for x in resolution.split("x")]
    header = (
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

    # one line per segment; NO \N
    t = 0.0
    events: List[str] = []
    for line, dur in segs:
        events.append(f"Dialogue: 0,{_fmt_ass_ts(t)},{_fmt_ass_ts(t+dur)},Default,,0,0,0,,{line}")
        t += dur

    return header + "\n".join(events) + "\n"


# ---------- video render with burn-in ----------

def _run_ffmpeg(args: List[str]) -> None:
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore")[:1200])


def render_burned_mp4(
    audio_bytes: bytes,
    ass_text: str,
    *,
    audio_ext: str = "mp3",         # "mp3" or "wav"
    resolution: str = "1080x1920",
    layout: str = "9:16",
    # Ensure bottom-center and margins even if style is missing at runtime
    force_style: str = "Alignment=2,WrapStyle=2,MarginL=80,MarginR=80,MarginV=120",
) -> bytes:
    a_suffix = ".wav" if audio_ext.lower() == "wav" else ".mp3"
    afd, a_path = tempfile.mkstemp(suffix=a_suffix); os.write(afd, audio_bytes); os.close(afd)
    sfd, s_path = tempfile.mkstemp(suffix=".ass"); os.write(sfd, ass_text.encode("utf-8")); os.close(sfd)
    v_path = a_path + ".mp4"

    # get duration from ffprobe
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

    # inputs first, then filter
    _run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s={resolution}:d={dur}",
        "-i", a_path,
        "-vf", f"subtitles=filename='{s_path}':force_style='{force_style}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        v_path
    ])

    with open(v_path, "rb") as f:
        out = f.read()

    for pth in (a_path, s_path, v_path):
        try:
            os.remove(pth)
        except Exception:
            pass

    return out
