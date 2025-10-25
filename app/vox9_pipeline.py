"""
Scaffold pipeline for Vox-9 assets.
- MP3/WAV via ElevenLabs or ffmpeg transcode
- Simple SRT/ASS/VTT from text (placeholder timing)
- Optional black MP4 with audio (no burned captions yet)
Swap these implementations for your real tkinter logic later.
"""
from __future__ import annotations
import io
import os
import re
import json
import math
import tempfile
import subprocess
from typing import Dict, Optional, Tuple

from app.tts import synthesize_elevenlabs

# ---------- Helpers

def _run_ffmpeg(args: list[str]) -> bytes:
    """Run ffmpeg, return output file bytes when -y -i ... outpath is last arg."""
    # We assume ffmpeg is installed in the container (Dockerfile installs it).
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'ignore')[:300]}")
    # The caller reads output file itself; we don't return bytes here.
    return b""  # unused

def _write_temp_bytes(suffix: str, data: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, data)
    os.close(fd)
    return path

def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()

def _estimate_segments(text: str) -> list[tuple[str, float]]:
    """
    Extremely simple segmentation: split on newlines/periods, assign ~2.5s per line,
    tweak by length. Replace with your real alignment later.
    """
    chunks = [c.strip() for c in re.split(r"[.\n]+", text) if c.strip()]
    segs = []
    for c in chunks:
        dur = max(1.5, min(6.0, 0.35 * (len(c) / 8)))  # crude
        segs.append((c, float(dur)))
    return segs or [("...", 2.0)]

def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# ---------- Captions (scaffold)

def make_captions_from_text(text: str) -> Dict[str, str]:
    segs = _estimate_segments(text)
    # SRT
    t = 0.0
    srt_lines = []
    for i, (txt, dur) in enumerate(segs, start=1):
        start = _fmt_ts(t)
        end = _fmt_ts(t + dur)
        srt_lines += [str(i), f"{start} --> {end}", txt, ""]
        t += dur
    srt = "\n".join(srt_lines).strip() + "\n"

    # VTT
    t = 0.0
    vtt_lines = ["WEBVTT", ""]
    for txt, dur in segs:
        start = _fmt_ts(t).replace(",", ".")
        end = _fmt_ts(t + dur).replace(",", ".")
        vtt_lines += [f"{start} --> {end}", txt, ""]
        t += dur
    vtt = "\n".join(vtt_lines).strip() + "\n"

    # ASS (very barebones)
    ass_header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: Default,Inter,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,80,80,120,0\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    def ass_ts(sec: float) -> str:
        h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60); cs = int((sec - int(sec)) * 100)
        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"
    t = 0.0
    ass_events = []
    for txt, dur in segs:
        ass_events.append(f"Dialogue: 0,{ass_ts(t)},{ass_ts(t+dur)},Default,,0,0,0,,{txt}")
        t += dur
    ass = ass_header + "\n".join(ass_events) + "\n"
    return {"srt": srt, "vtt": vtt, "ass": ass}

# ---------- Audio

def make_narration(text: str, voice_id: Optional[str], need_mp3: bool, need_wav: bool) -> Dict[str, Optional[bytes]]:
    """
    Produce mp3 and/or wav. If both requested, ask EL for one and transcode the other.
    """
    out: Dict[str, Optional[bytes]] = {"mp3": None, "wav": None}

    if need_mp3 and not need_wav:
        out["mp3"] = synthesize_elevenlabs(text, voice_id=voice_id, out_format="mp3")
        return out

    if need_wav and not need_mp3:
        out["wav"] = synthesize_elevenlabs(text, voice_id=voice_id, out_format="wav")
        return out

    # both requested
    mp3 = synthesize_elevenlabs(text, voice_id=voice_id, out_format="mp3")
    out["mp3"] = mp3

    # transcode to wav via ffmpeg
    mp3_path = _write_temp_bytes(".mp3", mp3)
    wav_path = mp3_path.replace(".mp3", ".wav")
    _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", mp3_path, wav_path])
    out["wav"] = _read_file(wav_path)
    try:
        os.remove(mp3_path); os.remove(wav_path)
    except Exception:
        pass
    return out

# ---------- MP4 (scaffold)

def make_black_mp4_with_audio(audio_bytes: bytes, layout: str = "9:16") -> bytes:
    """
    Create a simple black MP4 matching audio duration (no captions burned).
    layout: "9:16" or "16:9"
    """
    # Write audio temp
    a_path = _write_temp_bytes(".mp3", audio_bytes)
    # get duration via ffprobe
    fp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", a_path],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if fp.returncode != 0:
        raise RuntimeError("ffprobe failed")
    try:
        dur = float(fp.stdout.decode().strip())
    except Exception:
        dur = 10.0

    size = "1080x1920" if layout == "9:16" else "1920x1080"
    v_path = a_path + ".mp4"
    _run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s={size}:d={dur}",
        "-i", a_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        v_path
    ])
    data = _read_file(v_path)
    try:
        os.remove(a_path); os.remove(v_path)
    except Exception:
        pass
    return data
