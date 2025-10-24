from fastapi import FastAPI, Depends, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Session, create_engine, select
from datetime import datetime
from typing import Dict
from .auth import single_user_guard
from .models import Job
from .pipeline_adapter import run_pipeline_adapter

app = FastAPI()

# Serve the demo page at /
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

# CORS: allow everything for POC (tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

engine = create_engine("sqlite:///./jobs.db")
SQLModel.metadata.create_all(engine)

def save_job(job: Job):
    with Session(engine) as s:
        s.add(job); s.commit(); s.refresh(job)
        return job

def update_job(job_id: str, **fields):
    with Session(engine) as s:
        job = s.exec(select(Job).where(Job.id == job_id)).one()
        for k,v in fields.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow()
        s.add(job); s.commit(); s.refresh(job)
        return job

@app.post("/api/jobs")
def create_job(payload: Dict, bg: BackgroundTasks, _: None = Depends(single_user_guard)):
    """
    payload: { "text": "hello world" }
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
            "error": job.error
        }
