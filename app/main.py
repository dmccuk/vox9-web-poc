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
from app.pipeline_adapter import run_pipeline_adapter
from app.storage import (
    presign_upload,
    presign_download,
    list_objects,
    put_object_bytes,
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
    allow_origins=["*"],  # later: set to your exact Render origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- DB setup -----
engine = create_engine("sqlite:///./jobs.db")
SQLModel.metadata.create_all(engine)

def save_job(job: Job):
    with Session(engine) as s:
        s.add(job)
        s.commit()
        s.refresh(job)
        return job

def update_job(job_id: str, **fields):
    with Session(engine) as s:
        job = s.exec(select(Job).where(Job.id == job_id)).one()
        for k, v in fields.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow()
        s.add(job)
        s.commit()
        s.refresh(job)
        return job

# ----- S3 presign & listing endpoints -----
@app.post("/api/presign")
def get_presign(
    filename: str = Query(...),
    content_type: str = Query("application/octet-stream"),
    _: None = Depends(single_user_guard),
):
    # Sanitize filename to avoid policy mismatches
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    key = f"{settings.S3_INPUT_PREFIX}{safe}"
    return presign_upload(key, content_type)

@app.get("/api/presign_view")
def get_presign_view(
    key: str = Query(..., description="S3 object key to view/stream"),
    _: None = Depends(single_user_guard),
):
    # Stream/play in browser
    return {"url": presign_download(key, as_attachment=False)}

@app.get("/api/presign_download")
def get_presign_download(
    key: str = Query(..., description="S3 object key to download"),
    _: None = Depends(single_user_guard),
):
    # Force download via Content-Disposition: attachment
    name = key.split("/")[-1] or "download"
    return {"url": presign_download(key, as_attachment=True, download_name=name)}

@app.post("/api/presign_download_many")
def presign_download_many(
    payload: Dict = Body(...),
    _: None = Depends(single_user_guard),
):
    keys: List[str] = payload.get("keys", [])
    links = []
    for k in keys:
        name = k.split("/")[-1] or "download"
        links.append({"key": k, "url": presign_download(k, as_attachment=True, download_name=name)})
    return {"links": links}

@app.get("/api/list_objects")
def api_list_objects(
    prefix: str = Query("inputs/", description="S3 prefix to list, e.g. inputs/ or outputs/"),
    token: Optional[str] = Query(None, description="Pagination token"),
    max_keys: int = Query(100, ge=1, le=1000),
    _: None = Depends(single_user_guard),
):
    try:
        items, next_token = list_objects(prefix=prefix, continuation_token=token, max_keys=max_keys)
        return {"items": items, "next_token": next_token}
    except Exception as e:
        # Return readable error so the browser shows it instead of a 500
        raise HTTPException(status_code=400, detail=str(e))

# ----- Text job API (demo) -----
@app.post("/api/jobs")
def create_job(payload: Dict, bg: BackgroundTasks, _: None = Depends(single_user_guard)):
    """
    payload example:
    { "text": "hello world", "s3_input_key": "inputs/foo.mp4" }
    """
    text = (payload or {}).get("text") or ""
    job = save_job(Job(input_text=text, status="queued"))

    def run():
        try:
            update_job(job.id, status="running")
            out = run_pipeline_adapter(text)
            update_job(job.id, status="completed", output_text=out)
        except Exception as e:
            update_job(job.id, status="failed", error=str(e))

    bg.add_task(run)
    return {"job_id": job.id, "status": "queued"}

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, _: None = Depends(single_user_guard)):
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if not job:
            return {"error": "not found"}
        return {
            "id": job.id,
            "status": job.status,
            "output_text": job.output_text,
            "error": job.error,
        }

# ----- ElevenLabs TTS → S3 outputs/ -----
@app.post("/api/tts")
def tts_generate(
    payload: Dict = Body(...),
    _: None = Depends(single_user_guard),
):
    """
    Request: { "text": "Hello world", "filename": "my_audio.mp3" (optional) }
    Returns: { "s3_key": "...", "download_url": "..." }
    """
    text = (payload or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    desired_name = (payload or {}).get("filename") or ""
    ext = "mp3" if settings.ELEVEN_OUTPUT_FORMAT.lower() == "mp3" else "wav"
    base = desired_name.rsplit(".", 1)[0] if desired_name else f"eleven_{uuid4().hex[:8]}"
    fname = f"{base}.{ext}"
    key = f"{settings.S3_OUTPUT_PREFIX}{fname}"

    try:
        audio = synthesize_elevenlabs(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

    content_type = "audio/mpeg" if ext == "mp3" else "audio/wav"
    put_object_bytes(key, content_type, audio)

    url = presign_download(key, as_attachment=True, download_name=fname)
    return {"s3_key": key, "download_url": url}
