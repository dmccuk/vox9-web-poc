from pathlib import Path
import re
from uuid import uuid4
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, Depends, BackgroundTasks, Query, Body, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Session, create_engine, select

from app.auth import single_user_guard
from app.models import Job
from app.pipeline_adapter import run_pipeline_adapter  # kept for future use
from app.storage import (
    presign_upload,
    presign_download,
    list_objects,
    put_object_bytes,
    get_object_text,
)
from app.settings import settings
from app.tts import synthesize_elevenlabs

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

# ----- CORS (POC: permissive; tighten later) -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # later: restrict to your Render origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- DB (kept; not used in the simplified flow yet) -----
engine = create_engine("sqlite:///./jobs.db")
SQLModel.metadata.create_all(engine)

def save_job(job: Job):
    with Session(engine) as s:
        s.add(job); s.commit(); s.refresh(job); return job

def update_job(job_id: str, **fields):
    with Session(engine) as s:
        job = s.exec(select(Job).where(Job.id == job_id)).one()
        for k, v in fields.items(): setattr(job, k, v)
        job.updated_at = datetime.utcnow()
        s.add(job); s.commit(); s.refresh(job); return job

# ----- Helpers -----
_slug_re = re.compile(r"[^a-z0-9]+")
def slug(s: str) -> str:
    s = s.strip().lower()
    s = _slug_re.sub("-", s).strip("-")
    return s or "untitled"

# ----- Upload: presign a STORY file → projects/<story>/<filename> -----
@app.post("/api/presign_story")
def presign_story(
    filename: str = Query(..., description="Local filename being uploaded"),
    content_type: str = Query("text/plain"),
    _: None = Depends(single_user_guard),
):
    """
    Returns a presigned POST so the browser can upload the story file
    directly to S3 under: projects/<story-name>/<original-filename>
    where story-name = slug(filename without extension).
    """
    base = filename.rsplit(".", 1)[0].strip() or "story"
    project = slug(base)
    key = f"{settings.PROJECTS_PREFIX}{project}/{filename}"
    return presign_upload(key, content_type)

# ----- Generate narration FROM an uploaded story file in S3 -----
@app.post("/api/tts_from_story")
def tts_from_story(
    payload: Dict = Body(..., example={"s3_story_key": "projects/my-story/story.txt"}),
    _: None = Depends(single_user_guard),
):
    """
    Request: { "s3_story_key": "projects/<story>/<filename.txt>" }
    1) Reads the text from S3
    2) Sends to ElevenLabs
    3) Writes MP3 to projects/<story>/assets/<story>.mp3
    Returns: { "s3_key": "...", "download_url": "..." }
    """
    key = (payload or {}).get("s3_story_key") or ""
    if not key.startswith(settings.PROJECTS_PREFIX):
        raise HTTPException(status_code=400, detail="Invalid key")

    # story name is the folder name under projects/
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != settings.PROJECTS_PREFIX.strip("/"):
        raise HTTPException(status_code=400, detail="Key must be under projects/<story>/")

    story_slug = parts[1]                 # e.g., "my-story"
    story_filename = parts[-1]            # original file name
    story_base = story_filename.rsplit(".", 1)[0] or story_slug

    # 1) Fetch story text
    try:
        text = get_object_text(key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Unable to read story: {e}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Story file is empty")

    # 2) Synthesize
    try:
        audio = synthesize_elevenlabs(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

    # 3) Save MP3 to assets/
    ext = "mp3" if settings.ELEVEN_OUTPUT_FORMAT.lower() == "mp3" else "wav"
    out_name = f"{story_base}.{ext}"
    out_key = f"{settings.PROJECTS_PREFIX}{story_slug}/assets/{out_name}"
    content_type = "audio/mpeg" if ext == "mp3" else "audio/wav"
    put_object_bytes(out_key, content_type, audio)

    # 4) Presign download (attachment)
    url = presign_download(out_key, as_attachment=True, download_name=out_name)
    return {"s3_key": out_key, "download_url": url}

# ----- (Optional) Keep list/presign download for future UI bits -----
@app.get("/api/presign_download")
def api_presign_download(key: str = Query(...), _: None = Depends(single_user_guard)):
    name = key.split("/")[-1] or "download"
    return {"url": presign_download(key, as_attachment=True, download_name=name)}

@app.get("/api/list_objects")
def api_list_objects(
    prefix: str = Query("projects/"),
    token: Optional[str] = Query(None),
    max_keys: int = Query(100, ge=1, le=1000),
    _: None = Depends(single_user_guard),
):
    try:
        items, next_token = list_objects(prefix=prefix, continuation_token=token, max_keys=max_keys)
        return {"items": items, "next_token": next_token}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
