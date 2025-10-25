from pathlib import Path
import re
from typing import Dict, Optional, List

from fastapi import FastAPI, Depends, Query, Body, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.auth import single_user_guard
from app.storage import (
    presign_upload,
    presign_download,
    list_tree,
    put_object_bytes,
    get_object_text,
    delete_object,
)
from app.settings import settings
from app.tts import synthesize_elevenlabs, list_voices
from app.vox9_pipeline import (
    make_narration,
    make_captions_from_text,
    make_black_mp4_with_audio,
)

app = FastAPI()

# ----- Static files & index -----
STATIC_DIR = (Path(__file__).parent / "static").resolve()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", include_in_schema=False)
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(STATIC_DIR / "favicon.ico"))

@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}

# ----- CORS (POC) -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- helpers -----
_slug_re = re.compile(r"[^a-z0-9]+")
def slug(s: str) -> str:
    s = s.strip().lower()
    s = _slug_re.sub("-", s).strip("-")
    return s or "untitled"

# ---------- S3 Explorer ----------
@app.get("/api/tree")
def api_tree(
    prefix: str = Query("projects/", description="Folder prefix (e.g. 'projects/' or 'projects/my-story/')"),
    token: Optional[str] = Query(None),
    max_keys: int = Query(200, ge=1, le=1000),
    _: None = Depends(single_user_guard),
):
    try:
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        data = list_tree(prefix=prefix, continuation_token=token, max_keys=max_keys)
        data.setdefault("error", None)
        return data
    except Exception as e:
        return {"folders": [], "files": [], "next_token": None, "error": str(e)}

@app.get("/api/presign_download")
def api_presign_download(key: str = Query(...), _: None = Depends(single_user_guard)):
    name = key.split("/")[-1] or "download"
    return {"url": presign_download(key, as_attachment=True, download_name=name)}

@app.delete("/api/object")
def api_delete_object(key: str = Query(...), _: None = Depends(single_user_guard)):
    if not key.startswith(settings.PROJECTS_PREFIX):
        raise HTTPException(status_code=403, detail="Forbidden path")
    try:
        delete_object(key)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------- Upload story ----------
@app.post("/api/presign_story")
def presign_story(
    filename: str = Query(...),
    content_type: str = Query("text/plain"),
    _: None = Depends(single_user_guard),
):
    base = filename.rsplit(".", 1)[0].strip() or "story"
    project = slug(base)
    key = f"{settings.PROJECTS_PREFIX}{project}/{filename}"
    return presign_upload(key, content_type)

# ---------- Voices ----------
@app.get("/api/voices")
def api_voices(_: None = Depends(single_user_guard)):
    return list_voices()

# ---------- Simple TTS (kept for compatibility) ----------
@app.post("/api/tts_from_story")
def tts_from_story(
    payload: Dict = Body(..., example={"s3_story_key": "projects/my-story/story.txt", "voice_id": "<optional>"}),
    _: None = Depends(single_user_guard),
):
    key = (payload or {}).get("s3_story_key") or ""
    if not key.startswith(settings.PROJECTS_PREFIX):
        raise HTTPException(status_code=400, detail="Invalid key")

    parts = key.split("/")
    if len(parts) < 3 or parts[0] != settings.PROJECTS_PREFIX.strip("/"):
        raise HTTPException(status_code=400, detail="Key must be under projects/<story>/")

    story_slug = parts[1]
    story_filename = parts[-1]
    story_base = story_filename.rsplit(".", 1)[0] or story_slug

    text = get_object_text(key)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Story file is empty")

    voice_id = (payload or {}).get("voice_id") or settings.ELEVEN_VOICE_ID
    try:
        audio = synthesize_elevenlabs(text, voice_id=voice_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

    ext = "mp3" if settings.ELEVEN_OUTPUT_FORMAT.lower() == "mp3" else "wav"
    out_name = f"{story_base}.{ext}"
    out_key = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{out_name}"
    content_type = "audio/mpeg" if ext == "mp3" else "audio/wav"
    put_object_bytes(out_key, content_type, audio)

    url = presign_download(out_key, as_attachment=True, download_name=out_name)
    return {"s3_key": out_key, "download_url": url}

# ---------- Multi-asset generation ----------
@app.post("/api/generate_assets")
def generate_assets(
    payload: Dict = Body(..., example={
        "s3_story_key": "projects/my-story/story.txt",
        "voice_id": "<optional>",
        "outputs": ["mp3","srt","wav","ass","vtt","mp4"]
    }),
    _: None = Depends(single_user_guard),
):
    key = (payload or {}).get("s3_story_key") or ""
    if not key.startswith(settings.PROJECTS_PREFIX):
        raise HTTPException(status_code=400, detail="Invalid key")
    wanted: List[str] = [o.lower() for o in (payload or {}).get("outputs", [])] or ["mp3", "srt"]

    parts = key.split("/")
    if len(parts) < 3 or parts[0] != settings.PROJECTS_PREFIX.strip("/"):
        raise HTTPException(status_code=400, detail="Key must be under projects/<story>/")

    story_slug = parts[1]
    story_filename = parts[-1]
    story_base = story_filename.rsplit(".", 1)[0] or story_slug

    # Read story
    text = get_object_text(key)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Story file is empty")

    # Audio
    need_mp3 = "mp3" in wanted
    need_wav = "wav" in wanted
    audio_map = {"mp3": None, "wav": None}
    if need_mp3 or need_wav or "mp4" in wanted:
        try:
            audio_map = make_narration(text, payload.get("voice_id") or settings.ELEVEN_VOICE_ID, need_mp3 or "mp4" in wanted, need_wav)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

    results = []

    # Upload audio
    if audio_map.get("mp3"):
        k = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{story_base}.mp3"
        put_object_bytes(k, "audio/mpeg", audio_map["mp3"])
        results.append({"type": "mp3", "key": k, "url": presign_download(k, as_attachment=True, download_name=f"{story_base}.mp3")})

    if audio_map.get("wav"):
        k = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{story_base}.wav"
        put_object_bytes(k, "audio/wav", audio_map["wav"])
        results.append({"type": "wav", "key": k, "url": presign_download(k, as_attachment=True, download_name=f"{story_base}.wav")})

    # Captions
    if any(x in wanted for x in ("srt","ass","vtt")):
        caps = make_captions_from_text(text)
        if "srt" in wanted:
            k = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{story_base}.srt"
            put_object_bytes(k, "text/plain; charset=utf-8", caps["srt"].encode("utf-8"))
            results.append({"type": "srt", "key": k, "url": presign_download(k, as_attachment=True, download_name=f"{story_base}.srt")})
        if "ass" in wanted:
            k = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{story_base}.ass"
            put_object_bytes(k, "text/plain; charset=utf-8", caps["ass"].encode("utf-8"))
            results.append({"type": "ass", "key": k, "url": presign_download(k, as_attachment=True, download_name=f"{story_base}.ass")})
        if "vtt" in wanted:
            k = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{story_base}.vtt"
            put_object_bytes(k, "text/vtt; charset=utf-8", caps["vtt"].encode("utf-8"))
            results.append({"type": "vtt", "key": k, "url": presign_download(k, as_attachment=True, download_name=f"{story_base}.vtt")})

    # MP4 (black bg + audio)
    if "mp4" in wanted:
        audio_bytes = audio_map.get("wav") or audio_map.get("mp3")
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="MP4 requested but no audio was generated")
        ext = "wav" if audio_map.get("wav") else "mp3"
        try:
            mp4 = make_black_mp4_with_audio(audio_bytes, ext=ext, layout="9:16")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"MP4 render failed: {e}")
        k = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{story_base}.mp4"
        put_object_bytes(k, "video/mp4", mp4)
        results.append({"type": "mp4", "key": k, "url": presign_download(k, as_attachment=True, download_name=f"{story_base}.mp4")})

    return {"assets": results}
