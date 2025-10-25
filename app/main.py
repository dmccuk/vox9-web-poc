from pathlib import Path
import re
from uuid import uuid4
from typing import Dict, List, Optional

from fastapi import FastAPI, Depends, Query, Body, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.auth import single_user_guard
from app.storage import (
    presign_upload,
    presign_download,
    list_objects,
    list_tree,
    put_object_bytes,
    get_object_text,
)
from app.settings import settings
from app.tts import synthesize_elevenlabs, list_voices

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Helpers -----
_slug_re = re.compile(r"[^a-z0-9]+")
def slug(s: str) -> str:
    s = s.strip().lower()
    s = _slug_re.sub("-", s).strip("-")
    return s or "untitled"

# ---------- S3 Explorer APIs ----------
@app.get("/api/tree")
def api_tree(
    prefix: str = Query("projects/", description="Folder prefix ending with '/'"),
    token: Optional[str] = Query(None),
    max_keys: int = Query(200, ge=1, le=1000),
    _: None = Depends(single_user_guard),
):
    try:
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        return list_tree(prefix=prefix, continuation_token=token, max_keys=max_keys)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/presign_download")
def api_presign_download(key: str = Query(...), _: None = Depends(single_user_guard)):
    name = key.split("/")[-1] or "download"
    return {"url": presign_download(key, as_attachment=True, download_name=name)}

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

# ---------- ElevenLabs voices ----------
@app.get("/api/voices")
def api_voices(_: None = Depends(single_user_guard)):
    """
    Returns: { voices: [{voice_id, name}], default_voice_id: '...' }
    """
    try:
        data = list_voices()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Voice import failed: {e}")
    voices_raw = data.get("voices", []) or []
    voices = [{"voice_id": v.get("voice_id"), "name": v.get("name")} for v in voices_raw if v.get("voice_id")]
    return {"voices": voices, "default_voice_id": settings.ELEVEN_VOICE_ID}

# ---------- TTS from story ----------
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

    try:
        text = get_object_text(key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Unable to read story: {e}")
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
