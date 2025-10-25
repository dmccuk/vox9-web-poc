"""
Vox-9 TTS engine — synced captions from text using ElevenLabs
Derived from the original Tkinter captions app, adapted for the FastAPI backend.
"""

import os
import re
import json
import tempfile
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import requests
from pydub import AudioSegment
from pydub.utils import which

# --- Ensure ffmpeg/ffprobe available ---
AudioSegment.converter = which("ffmpeg")
AudioSegment.ffprobe = which("ffprobe")

# --- Defaults ---
DEFAULT_FONT_NAME = "Calibri"
DEFAULT_FONT_SIZE = 65
DEFAULT_ALIGNMENT = 2  # bottom-centre
DEFAULT_MARGIN_V = 30
DEFAULT_OUTLINE = 3.0
DEFAULT_SHADOW = 1.0

DEFAULT_MAX_CHARS_PER_LINE = 65
DEFAULT_LINES_PER_EVENT = 1

DEFAULT_LEAD_IN_MS = 250
DEFAULT_CAPTION_LEAD_IN_MS = 50
DEFAULT_CAPTION_LEAD_OUT_MS = 120
DEFAULT_GAP_MS = 150
DEFAULT_PARAGRAPH_GAP_MS = 600

ELEVEN_TTS_URL_TMPL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# ---------- TEXT HELPERS ----------
ASS_TAG_RE = re.compile(r"\{\\.*?\}")

def clean_text(raw: str) -> str:
    t = raw.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\t+", " ", t)
    t = re.sub(r"[ \t\f\v]+", " ", t)
    t = re.sub(r"[ \t\f\v]*\n[ \t\f\v]*", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n".join(lines).strip()


# ---------- SENTENCE SPLITTING ----------
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


# ---------- TIMESTAMP HELPERS ----------
def format_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
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
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1000
    rem = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{rem:03d}"

def normalize_caption_text(txt: str) -> str:
    return ASS_TAG_RE.sub("", txt.replace("\\N", "\n")).strip()


# ---------- LINE SPLITTING ----------
def _wrap_words_to_lines(words: list, max_chars: int) -> list:
    lines, line = [], ""
    for w in words:
        cand = (line + " " + w).strip()
        if len(cand) <= max_chars or not line:
            line = cand
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines

def split_text_for_events(text: str, max_chars: int, max_lines: int) -> list:
    words = text.split()
    lines = _wrap_words_to_lines(words, max_chars)
    if not lines:
        return [""]
    segs = []
    for i in range(0, len(lines), max_lines):
        segs.append("\\N".join(lines[i:i+max_lines]))
    return segs


# ---------- WRITE CAPTIONS ----------
def write_ass(sub_path: Path, events: list, *, font_name: str, font_size: int, bold: bool,
              italic: bool, outline: float, shadow: float, alignment: int, margin_v: int) -> None:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    )
    style = (
        "Style: Default,"
        f"{font_name},{int(font_size)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        f"{-1 if bold else 0},{-1 if italic else 0},0,0,100,100,0,0,1,"
        f"{outline},{shadow},{alignment},80,80,{margin_v},0"
    )
    events_hdr = "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    lines = [header + style + events_hdr]
    for ev in events:
        lines.append(f"Dialogue: 0,{ev['start']},{ev['end']},Default,,0,0,0,,{ev['text']}")
    sub_path.write_text("\n".join(lines), encoding="utf-8")

def write_srt(path: Path, events: List[Dict[str, str]]) -> None:
    out = []
    for i, ev in enumerate(events, 1):
        a = ass_to_ms(ev["start"]); b = ass_to_ms(ev["end"])
        if b <= a: b = a + 10
        out.append(str(i))
        out.append(f"{ms_to_srt(a)} --> {ms_to_srt(b)}")
        out.append(normalize_caption_text(ev["text"]))
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


# ---------- ELEVENLABS API ----------
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


# ---------- MAIN ENTRY ----------
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
    max_lines_per_event: int = DEFAULT_LINES_PER_EVENT,
    font_name: str = DEFAULT_FONT_NAME,
    font_size: int = DEFAULT_FONT_SIZE,
    bold: bool = True,
    italic: bool = False,
    lead_in_ms: int = DEFAULT_LEAD_IN_MS,
    caption_lead_in_ms: int = DEFAULT_CAPTION_LEAD_IN_MS,
    caption_lead_out_ms: int = DEFAULT_CAPTION_LEAD_OUT_MS,
    gap_ms: int = DEFAULT_GAP_MS,
    paragraph_gap_ms: int = DEFAULT_PARAGRAPH_GAP_MS,
) -> Dict[str, str]:
    """
    Synthesize per sentence, measure actual durations, create synced captions.
    Returns local file paths.
    """
    api_key = os.getenv("ELEVEN_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVEN_API_KEY is missing")
    eleven = ElevenAPI(api_key)

    cleaned = clean_text(story_text)
    pieces = split_into_sentences(cleaned)
    if not pieces:
        raise RuntimeError("No sentences found after cleaning text")

    tmp = Path(tempfile.mkdtemp(prefix="vox9_tts_"))
    chunks: List[AudioSegment] = []
    durations: List[float] = []
    for idx, (sentence, _para_break) in enumerate(pieces, 1):
        mp3 = eleven.synth_sentence(
            voice_id, sentence, model_id=model_id,
            stability=stability, similarity=similarity_boost, speaking_rate=speaking_rate
        )
        mp3_path = tmp / f"chunk_{idx:04d}.mp3"
        mp3_path.write_bytes(mp3)
        seg = AudioSegment.from_file(mp3_path, format="mp3")
        chunks.append(seg)
        durations.append(len(seg) / 1000.0)

    # --- Join audio with realistic gaps ---
    lead_in_ms = max(0, int(lead_in_ms))
    gap_ms = max(0, int(gap_ms))
    paragraph_gap_ms = max(gap_ms, int(paragraph_gap_ms))

    gaps_after: List[int] = []
    for i in range(len(pieces)):
        if i == len(pieces) - 1:
            gaps_after.append(0)
        else:
            pause = gap_ms
            if pieces[i+1][1]:
                pause = max(pause, paragraph_gap_ms)
            gaps_after.append(pause)

    full = AudioSegment.silent(duration=lead_in_ms)
    for seg, pause_after in zip(chunks, gaps_after):
        full += seg
        if pause_after > 0:
            full += AudioSegment.silent(duration=pause_after)

    # --- Write WAV/MP3 ---
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "narration"
    wav_path = output_dir / f"{stem}.wav"
    mp3_path = output_dir / f"{stem}.mp3"
    full.export(wav_path, format="wav")
    try:
        full.export(mp3_path, format="mp3")
    except Exception:
        mp3_path = None

    # --- Build caption events ---
    t = max(0.0, lead_in_ms / 1000.0)
    caption_lead_in = max(0, int(caption_lead_in_ms)) / 1000.0
    caption_lead_out = max(0, int(caption_lead_out_ms)) / 1000.0
    min_event = 0.03

    events = []
    for (sentence, _pb), dur, gap_after in zip(pieces, durations, gaps_after):
        sentence_start = t
        sentence_end = sentence_start + dur

        seg_texts = split_text_for_events(sentence, max_chars_per_line, max_lines_per_event)
        weights = [max(len(s.replace("\\N", " ").strip()), 1) for s in seg_texts]
        total_w = sum(weights)

        seg_start = sentence_start
        for i, (seg_text, w) in enumerate(zip(seg_texts, weights)):
            seg_end = sentence_end if i == len(seg_texts) - 1 else seg_start + (dur * (w / total_w))
            if seg_end <= seg_start:
                seg_end = seg_start + min_event

            start = max(0.0, seg_start - caption_lead_in)
            end = max(start + min_event, seg_end - caption_lead_out)

            if events and start < events[-1]["end_seconds"]:
                start = events[-1]["end_seconds"]
                if end <= start:
                    end = start + min_event

            events.append({
                "start": format_ts(start),
                "end": format_ts(end),
                "start_seconds": start,
                "end_seconds": end,
                "text": seg_text,
            })
            seg_start = seg_end

        t = sentence_end + (gap_after / 1000.0)

    # --- Write captions ---
    ass_path = output_dir / f"{stem}.ass"
    srt_path = output_dir / f"{stem}.srt"
    write_ass(
        ass_path,
        events,
        font_name=font_name,
        font_size=int(font_size),
        bold=bool(bold),
        italic=bool(italic),
        outline=DEFAULT_OUTLINE,
        shadow=DEFAULT_SHADOW,
        alignment=DEFAULT_ALIGNMENT,
        margin_v=DEFAULT_MARGIN_V,
    )
    write_srt(srt_path, events)

    vtt_path = output_dir / f"{stem}.vtt"
    vtt_lines = ["WEBVTT", ""]
    for ev in events:
        a = ms_to_srt(ass_to_ms(ev["start"])).replace(",", ".")
        b = ms_to_srt(ass_to_ms(ev["end"])).replace(",", ".")
        vtt_lines += [f"{a} --> {b}", normalize_caption_text(ev["text"]), ""]
    vtt_path.write_text("\n".join(vtt_lines), encoding="utf-8")

    meta = {
        "wrap": {"max_chars": int(max_chars_per_line), "max_lines": int(max_lines_per_event)},
        "style": {"font": font_name, "size": int(font_size), "bold": bool(bold), "italic": bool(italic)},
        "counts": {"sentences": len(pieces), "characters": len(cleaned)},
    }
    (output_dir / f"{stem}_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return {
        "wav": str(wav_path),
        "mp3": str(mp3_path) if mp3_path else "",
        "ass": str(ass_path),
        "srt": str(srt_path),
        "vtt": str(vtt_path),
    }


# ---------- LIST VOICES ----------
DEFAULT_FAVORITE_VOICES = [
    ("Adam", "pNInz6obpgDQGcFmaJgB"),
    ("Antoni", "ErXwobaYiN019PkySvjV"),
    ("Jessie", "t0jbNlBVZ17f02VDIeMI"),
    ("Dave - Texan middle aged", "3ozl8hsxdYYNRhFU44aP"),
    ("Michael", "flq6f7yk4E4fJM5XTYuZ"),
    ("Brian", "nPczCjzI2devNBz1zQrb"),
]

def list_voices():
    """
    Return all available ElevenLabs voices, or fallback defaults if API fails.
    """
    api_key = os.getenv("ELEVEN_API_KEY")
    hdrs = {"xi-api-key": api_key} if api_key else {}
    try:
        if not api_key:
            raise RuntimeError("no key")

        r = requests.get("https://api.elevenlabs.io/v1/voices", headers=hdrs, timeout=30)
        r.raise_for_status()
        data = r.json() or {}
        voices = data.get("voices") or []
        out = []
        for v in voices:
            name = (v.get("name") or "").strip() or "Unnamed"
            vid = (v.get("voice_id") or "").strip()
            if vid:
                out.append({"name": name, "voice_id": vid})
        if out:
            return {"voices": out}
        raise RuntimeError("empty")
    except Exception:
        # fallback to favorites
        return {"voices": [{"name": n, "voice_id": vid} for (n, vid) in DEFAULT_FAVORITE_VOICES]}
