"""Midway Scout — agent dispatch API + dashboard.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, orchestrator

app = FastAPI(title="Midway Scout", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Serves dashboard assets (logo, etc.); "/" itself still returns index.html below.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup():
    db.init_db()
    stale = db.fail_stale_jobs()
    if stale:
        print(f"[startup] marked {stale} interrupted job(s) as failed")


class JobRequest(BaseModel):
    topics: list[str] = Field(..., min_length=1, description="One agent per topic")
    context: str = ""
    engine: str = Field("auto", pattern="^(auto|firecrawl|browser|rss)$")
    num_agents: int = Field(3, ge=1, le=config.MAX_CONCURRENT_AGENTS)


@app.get("/")
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, **config.status_summary()}


@app.post("/api/jobs")
async def create_job(req: JobRequest):
    if req.engine == "browser" and (config.LLM_PROVIDER not in ("openai", "anthropic")
                                    or not config.llm_ready()):
        raise HTTPException(400, "The browser engine needs an OpenAI or Anthropic API key. "
                                 "The rss/firecrawl engines work with claude-code, ollama, or no LLM at all.")
    if req.engine == "firecrawl" and not config.firecrawl_ready():
        raise HTTPException(400, "FIRECRAWL_API_KEY is not set in .env — use the RSS engine (keyless) instead.")
    topics = [t.strip() for t in req.topics if t.strip()]
    if not topics:
        raise HTTPException(400, "At least one non-empty topic is required")
    job_id = db.create_job(topics, req.context.strip(), req.engine, req.num_agents)
    asyncio.get_event_loop().create_task(orchestrator.run_job(job_id))
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs")
def jobs():
    items = db.list_jobs()
    for j in items:  # keep the list payload light
        j.pop("digest_md", None)
        j["results"] = len(j["results"])
    return items


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/jobs/{job_id}/digest.md")
def job_digest(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job["digest_md"]:
        raise HTTPException(404, "Digest not ready")
    return PlainTextResponse(
        job["digest_md"],
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="digest-{job_id}.md"'},
    )
