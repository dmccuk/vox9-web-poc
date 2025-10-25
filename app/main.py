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
from app.tts import list_voices
from app.asset_pipeline import generate_assets, DEFAULT_STYLE

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

# ---------- Multi-asset generation (Phase 1 style-ready) ----------
@app.post("/api/generate_assets")
def api_generate_assets(
    payload: Dict = Body(..., example={
        "s3_story_key": "projects/my-story/story.txt",
        "voice_id": "<optional>",
        "outputs": ["mp3","srt","ass","vtt","mp4"],
        "caption_style": {
          "font": "Inter",
          "size": 64,
          "bold": False,
          "italic": False,
          "resolution": "1080x1920",
          "layout": "9:16"
        }
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

    # Style config (backend-ready; UI will pass later)
    style = payload.get("caption_style") or DEFAULT_STYLE
    voice_id = payload.get("voice_id") or settings.ELEVEN_VOICE_ID

    # Generate everything in-memory
    try:
        blobs = generate_assets(
            story_text=text,
            voice_id=voice_id,
            outputs=wanted,
            style=style,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")

    # Upload to S3 under assets/
    results = []
    ct_map = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "srt": "text/plain; charset=utf-8",
        "ass": "text/plain; charset=utf-8",
        "vtt": "text/vtt; charset=utf-8",
        "mp4": "video/mp4",
    }

    for ext, data in blobs.items():
        k = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{story_base}.{ext}"
        put_object_bytes(k, ct_map.get(ext, "application/octet-stream"), data)
        results.append({"type": ext, "key": k, "url": presign_download(k, as_attachment=True, download_name=f"{story_base}.{ext}")})

    return {"assets": results}
