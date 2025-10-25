"""
Vox-9 TTS engine — synced captions from text using ElevenLabs
Now defaults to widescreen (1920x1080) and safe single-line captions.
"""

import os
import re
import json
import tempfile
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import requests
from pydub import AudioSegment
from pydub.utils import which

AudioSegment.converter = which("ffmpeg")
AudioSegment.ffprobe = which("ffprobe")

# ---------- Defaults ----------
DEFAULT_FONT_NAME = "DejaVu Sans"   # safe in container; Calibri if provided
DEFAULT_FONT_SIZE = 65
DEFAULT_ALIGNMENT = 2               # bottom-centre
DEFAULT_MARGIN_V = 30
DEFAULT_MARGIN_L = 80
DEFAULT_MARGIN_R = 80
DEFAULT_OUTLINE = 3.0
DEFAULT_SHADOW  = 1.0

DEFAULT_MAX_CHARS_PER_LINE = 60     # matches Tk app vibe
DEFAULT_LINES_PER_EVENT    = 1

DEFAULT_LEAD_IN_MS          = 250
DEFAULT_CAPTION_LEAD_IN_MS  = 50
DEFAULT_CAPTION_LEAD_OUT_MS = 120
DEFAULT_GAP_MS              = 150
DEFAULT_PARAGRAPH_GAP_MS    = 600

DEFAULT_RESOLUTION = "1920x1080"    # widescreen default

ELEVEN_TTS_URL_TMPL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ASS_TAG_RE = re.compile(r"\{\\.*?\}")

# ---------- Helpers ----------
def clean_text(raw: str) -> str:
    t = raw.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\t+", " ", t)
    t = re.sub(r"[ \t\f\v]+", " ", t)
    t = re.sub(r"[ \t\f\v]*\n[ \t\f\v]*", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n".join(lines).strip()

def _parse_resolution(res: str) -> Tuple[int,int]:
    try:
        w, h = res.lower().split("x")
        return int(w), int(h)
    except Exception:
        return (1920, 1080)

def format_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def ass_to_ms(ts_ass: str) -> int:
    try:
        h, m, s_cs = ts_ass.split(":")
        s, cs = s_cs.split(".")
        return ((int(h)*3600 + int(m)*60 + int(s)) * 1000) + int(cs)*10
    except Exception:
        return 0

def ms_to_srt(ms: int) -> str:
    ms = max(0, int(ms))
    h = ms // 3_600_000; m = (ms % 3_600_000) // 60_000; s = (ms % 60_000) // 1000; rem = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{rem:03d}"

def normalize_caption_text(txt: str) -> str:
    return ASS_TAG_RE.sub("", txt.replace("\\N", "\n")).strip()

# ---------- Sentence splitting ----------
_NON_TERMINAL_ABBREVIATIONS = {"mr.", "mrs."}
_COMMON_STARTERS = {"a","an","and","but","he","she","it","i","you","we","they","the","there","these","those","this"}

def _ends_with_abbrev(s: str) -> bool:
    if not s: return False
    last = s.rstrip().split()
    if not last: return False
    token = last[-1].rstrip("\"'”’)]}")
    return token.lower() in _NON_TERMINAL_ABBREVIATIONS

def _starts_like_new_sentence(part: str) -> bool:
    if not part: return False
    w = part.split()
    if not w: return False
    first = w[0].lstrip("\"'“”‘’([{").lower()
    return first in _COMMON_STARTERS

def split_into_sentences(text: str) -> List[Tuple[str, bool]]:
    paras = text.split("\n\n")
    out: List[Tuple[str,bool]] = []
    seen_para = False
    for para in paras:
        p = para.strip()
        if not p:
            continue
        parts = re.split(r"(?<=[\.!?])\s+", p)
        first_in_para = True
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if out and not first_in_para and _ends_with_abbrev(out[-1][0]) and not _starts_like_new_sentence(part):
                prev = out[-1]
                out[-1] = (f"{prev[0]} {part}", prev[1])
            else:
                out.append((part, seen_para and first_in_para))
            first_in_para = False
        seen_para = True
    return out

# ---------- Simple wrap (safe defaults like Tk) ----------
def split_text_for_events(text: str, max_chars: int, max_lines: int) -> list:
    words = text.split()
    if not words:
        return [""]
    lines = []
    line = ""
    for w in words:
        candidate = (line + " " + w).strip()
        if len(candidate) <= max_chars or not line:
            line = candidate
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    # group into single-line events (or multiple sequential if long)
    segs = []
    for i in range(0, len(lines), max_lines):
        segs.append("\\N".join(lines[i:i+max_lines]))
    return segs

# ---------- Write captions ----------
def write_ass(sub_path: Path, events: list, *, font_name: str, font_size: int,
              bold: bool, italic: bool, outline: float, shadow: float,
              alignment: int, margin_v: int, margin_l: int, margin_r: int,
              resolution: str) -> None:
    w, h = _parse_resolution(resolution)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        "WrapStyle: 2\n"  # prevents cut-off if line slightly exceeds width
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
    )
    style = (
        "Style: Default,"
        f"{font_name},{int(font_size)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        f"{-1 if bold else 0},{-1 if italic else 0},0,0,100,100,0,0,1,"
        f"{outline},{shadow},{alignment},{margin_l},{margin_r},{margin_v},0"
    )
    events_hdr = "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    lines = [header + style + events_hdr]
    for ev in events:
        lines.append(f"Dialogue: 0,{ev['start']},{ev['end']},Default,,0,0,0,,{ev['text']}")
    sub_path.write_text("\n".join(lines), encoding="utf-8")

# ---------- ElevenLabs ----------
class ElevenAPI:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({"xi-api-key": api_key})

    def synth_sentence(self, voice_id: str, text: str, *, model_id: str,
                       stability: float = 0.5, similarity: float = 0.75, speaking_rate: float = 1.0) -> bytes:
        url = ELEVEN_TTS_URL_TMPL.format(voice_id=voice_id)
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": float(stability),
                "similarity_boost": float(similarity),
                "speaking_rate": float(speaking_rate),
            },
        }
        r = self.session.post(url, json=payload, headers={"Accept": "audio/mpeg"}, timeout=120)
        r.raise_for_status()
        return r.content

# ---------- Generate assets ----------
def generate_assets_from_story(
    story_text: str,
    output_dir: Path,
    *,
    voice_id: str,
    model_id: str = "eleven_monolingual_v1",
    speaking_rate: float = 1.0,
    stability: float = 0.5,
    similarity_boost: float = 0.75,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
    font_name: str = DEFAULT_FONT_NAME,
    font_size: int = DEFAULT_FONT_SIZE,
    bold: bool = True,
    italic: bool = False,
    resolution: str = DEFAULT_RESOLUTION,
) -> Dict[str, str]:
    api_key = os.getenv("ELEVEN_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVEN_API_KEY is missing")
    eleven = ElevenAPI(api_key)

    cleaned = clean_text(story_text)
    pieces = split_into_sentences(cleaned)
    if not pieces:
        raise RuntimeError("No sentences found")

    tmp = Path(tempfile.mkdtemp(prefix="vox9_tts_"))
    chunks: List[AudioSegment] = []
    durations: List[float] = []
    for idx, (sentence, _) in enumerate(pieces, 1):
        mp3 = eleven.synth_sentence(
            voice_id, sentence, model_id=model_id,
            stability=stability, similarity=similarity_boost, speaking_rate=speaking_rate
        )
        mp3_path = tmp / f"chunk_{idx:04d}.mp3"
        mp3_path.write_bytes(mp3)
        seg = AudioSegment.from_file(mp3_path, format="mp3")
        chunks.append(seg)
        durations.append(len(seg) / 1000.0)

    # Join all audio
    full = AudioSegment.silent(duration=DEFAULT_LEAD_IN_MS)
    for seg in chunks:
        full += seg + AudioSegment.silent(duration=DEFAULT_GAP_MS)

    # Export audio
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "narration"
    wav_path = output_dir / f"{stem}.wav"
    mp3_path = output_dir / f"{stem}.mp3"
    full.export(wav_path, format="wav")
    try:
        full.export(mp3_path, format="mp3")
    except Exception:
        mp3_path = None

    # Build caption events
    t = DEFAULT_LEAD_IN_MS / 1000.0
    events = []
    for (sentence, _), dur in zip(pieces, durations):
        sentence_start = t
        sentence_end = sentence_start + dur
        seg_texts = split_text_for_events(sentence, max_chars_per_line, 1)
        for seg_text in seg_texts:
            events.append({
                "start": format_ts(sentence_start),
                "end": format_ts(sentence_end),
                "text": seg_text,
            })
        t = sentence_end + DEFAULT_GAP_MS / 1000.0

    # Write captions
    ass_path = output_dir / f"{stem}.ass"
    srt_path = output_dir / f"{stem}.srt"
    write_ass(ass_path, events, font_name=font_name, font_size=font_size,
              bold=bold, italic=italic, outline=DEFAULT_OUTLINE, shadow=DEFAULT_SHADOW,
              alignment=DEFAULT_ALIGNMENT, margin_v=DEFAULT_MARGIN_V,
              margin_l=DEFAULT_MARGIN_L, margin_r=DEFAULT_MARGIN_R,
              resolution=resolution)

    # SRT simple
    out_srt = []
    for i, ev in enumerate(events, 1):
        a = ass_to_ms(ev["start"]); b = ass_to_ms(ev["end"]); b = max(b, a+10)
        out_srt += [str(i), f"{ms_to_srt(a)} --> {ms_to_srt(b)}", normalize_caption_text(ev["text"]), ""]
    srt_path.write_text("\n".join(out_srt), encoding="utf-8")

    return {
        "wav": str(wav_path),
        "mp3": str(mp3_path) if mp3_path else "",
        "ass": str(ass_path),
        "srt": str(srt_path),
    }

# ---------- Voice listing ----------
DEFAULT_FAVORITE_VOICES = [
    ("Adam", "pNInz6obpgDQGcFmaJgB"),
    ("Antoni", "ErXwobaYiN019PkySvjV"),
    ("Jessie", "t0jbNlBVZ17f02VDIeMI"),
    ("Dave - Texan middle aged", "3ozl8hsxdYYNRhFU44aP"),
    ("Michael", "flq6f7yk4E4fJM5XTYuZ"),
    ("Brian", "nPczCjzI2devNBz1zQrb"),
]

def list_voices():
    api_key = os.getenv("ELEVEN_API_KEY")
    hdrs = {"xi-api-key": api_key} if api_key else {}
    try:
        if not api_key:
            raise RuntimeError("no key")
        r = requests.get("https://api.elevenlabs.io/v1/voices", headers=hdrs, timeout=30)
        r.raise_for_status()
        data = r.json() or {}; voices = data.get("voices") or []
        out = [{"name": (v.get("name") or "Unnamed").strip(), "voice_id": (v.get("voice_id") or "").strip()}
               for v in voices if (v.get("voice_id") or "").strip()]
        return {"voices": out or [{"name": n, "voice_id": vid} for (n, vid) in DEFAULT_FAVORITE_VOICES]}
    except Exception:
        return {"voices": [{"name": n, "voice_id": vid} for (n, vid) in DEFAULT_FAVORITE_VOICES]}
