import os
import uuid
import json
import shutil
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from processor import VideoProcessor

app = FastAPI(title="AutoCut API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# In-memory job store (use Redis in production)
jobs = {}


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload a video file and return a job ID."""
    allowed_types = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm"}
    allowed_ext = {".mp4", ".mov", ".mkv", ".webm"}

    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_ext:
        raise HTTPException(400, f"Unsupported format: {ext}. Use MP4, MOV, MKV.")

    job_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{job_id}{ext}"

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    jobs[job_id] = {
        "id": job_id,
        "filename": file.filename,
        "input_path": str(dest),
        "status": "uploaded",
        "progress": 0,
        "step": "Aguardando processamento",
        "output_path": None,
        "error": None,
        "stats": None,
    }

    return {"job_id": job_id, "filename": file.filename}


@app.post("/api/process/{job_id}")
async def process_video(
    job_id: str,
    background_tasks: BackgroundTasks,
    cut_mode: str = "medium",        # aggressive | medium | light
    min_silence_ms: Optional[int] = None,
    remove_background_noise: bool = True,
    padding_ms: int = 50,
):
    """Start processing a video job."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]
    if job["status"] == "processing":
        raise HTTPException(400, "Already processing")

    # Determine silence threshold from cut_mode
    if min_silence_ms is None:
        thresholds = {"aggressive": 200, "medium": 400, "light": 700}
        min_silence_ms = thresholds.get(cut_mode, 400)

    job["status"] = "processing"
    job["progress"] = 0
    job["cut_mode"] = cut_mode
    job["min_silence_ms"] = min_silence_ms

    background_tasks.add_task(
        run_processing,
        job_id,
        job["input_path"],
        min_silence_ms,
        remove_background_noise,
        padding_ms,
    )

    return {"job_id": job_id, "status": "processing"}


async def run_processing(job_id, input_path, min_silence_ms, remove_bg, padding_ms):
    """Background task for video processing."""
    def progress_callback(pct, step):
        jobs[job_id]["progress"] = pct
        jobs[job_id]["step"] = step

    try:
        output_path = OUTPUT_DIR / f"{job_id}_edited.mp4"
        processor = VideoProcessor(
            input_path=input_path,
            output_path=str(output_path),
            min_silence_ms=min_silence_ms,
            remove_bg_noise=remove_bg,
            padding_ms=padding_ms,
            progress_callback=progress_callback,
        )
        stats = await asyncio.get_event_loop().run_in_executor(None, processor.run)

        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["step"] = "Concluído!"
        jobs[job_id]["output_path"] = str(output_path)
        jobs[job_id]["stats"] = stats

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["step"] = f"Erro: {str(e)}"


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Poll job status and progress."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


@app.get("/api/download/{job_id}")
async def download_result(job_id: str):
    """Download the processed video."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(400, "Video not ready yet")

    output_path = job["output_path"]
    if not Path(output_path).exists():
        raise HTTPException(404, "Output file missing")

    original_name = Path(job["filename"]).stem
    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"{original_name}_autocut.mp4",
    )


@app.delete("/api/job/{job_id}")
async def delete_job(job_id: str):
    """Clean up job files."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs.pop(job_id)
    for path_key in ("input_path", "output_path"):
        p = job.get(path_key)
        if p and Path(p).exists():
            Path(p).unlink()

    return {"deleted": job_id}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
