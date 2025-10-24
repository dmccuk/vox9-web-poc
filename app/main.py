from pathlib import Path
import re
from datetime import datetime
from typing import Dict

from fastapi import FastAPI, Depends, BackgroundTasks, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Session, create_engine, select

from app.auth import single_user_guard
from app.models import Job
from app.pipeline_adapter import run_pipeline_adapter
from app.storage import presign_upload, presign_download
from app.settings import settings

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

# ----- Health (optional but handy) -----
@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}

# ----- CORS (POC: permissive; tighten later) -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # consider setting to your Render URL later
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

# ----- S3 presign endpoints -----
@app.post("/api/presign")
def get_presign(
    filename: str = Query(...),
    content_type: str = Query("application/octet-stream"),
    _: None = Depends(single_user_guard),
):
    """
    Return a presigned POST for direct-to-S3 browser uploads.
    """
    # Sanitize filename to avoid policy mismatches
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    key = f"{settings.S3_INPUT_PREFIX}{safe}"
    return presign_upload(key, content_type)

@app.get("/api/presign_download")
def get_presign_download(
    key: str = Query(..., description="S3 object key to download"),
    _: None = Depends(single_user_guard),
):
    """
    Return a presigned GET URL for downloading an S3 object.
    """
    return {"url": presign_download(key)}

# ----- Job API -----
@app.post("/api/jobs")
def create_job(payload: Dict, bg: BackgroundTasks, _: None = Depends(single_user_guard)):
    """
    payload example:
    { "text": "hello world", "s3_input_key": "inputs/foo.mp4" }
    """
    text = (payload or {}).get("text") or ""
    # s3_input_key is optional for now; wire to your real pipeline later
    # s3_key = (payload or {}).get("s3_input_key")

    job = save_job(Job(input_text=text, status="queued"))

    def run():
        try:
            update_job(job.id, status="running")
            out = run_pipeline_adapter(text)  # swap in real processing
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
