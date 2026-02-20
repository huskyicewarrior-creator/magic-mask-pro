from __future__ import annotations

import base64
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.sam2_engine import Sam2Segmenter, SegmentationConfig

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
BACKGROUNDS = DATA / "backgrounds"
OUTPUTS = DATA / "outputs"

for folder in (UPLOADS, BACKGROUNDS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)

GREEN_BG = BACKGROUNDS / "greenscreen.png"
BLUE_BG = BACKGROUNDS / "bluescreen.png"
if not GREEN_BG.exists():
    cv2.imwrite(str(GREEN_BG), np.full((1080, 1920, 3), (0, 255, 0), dtype=np.uint8))
if not BLUE_BG.exists():
    cv2.imwrite(str(BLUE_BG), np.full((1080, 1920, 3), (255, 0, 0), dtype=np.uint8))

app = FastAPI(title="Magic Mask Pro")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/web", StaticFiles(directory=ROOT / "web", html=True), name="web")

jobs: Dict[str, Dict] = {}
library: List[Dict] = []


class ProcessRequest(BaseModel):
    video_id: str
    background_id: str
    points: List[List[int]]
    labels: List[int]
    trim_start: float = 0.0
    trim_end: Optional[float] = None
    disable_trim: bool = False


def _new_segmenter() -> Sam2Segmenter:
    return Sam2Segmenter(SegmentationConfig(sam2_checkpoint=ROOT / "sam2_checkpoint.pt"))


@app.get("/")
def home():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/health")
def health():
    seg = _new_segmenter()
    return {"ok": True, "name": "Magic Mask Pro", "segmentation_engine": "sam2" if seg.using_sam2 else "opencv-fallback"}


@app.get("/api/backgrounds")
def get_backgrounds():
    return [
        {"id": "greenscreen", "name": "Green Screen", "url": "/api/background-file/greenscreen"},
        {"id": "bluescreen", "name": "Blue Screen", "url": "/api/background-file/bluescreen"},
        *[
            {"id": p.stem, "name": p.name, "url": f"/api/background-file/{p.stem}"}
            for p in BACKGROUNDS.glob("uploaded_*.*")
        ],
    ]


@app.get("/api/background-file/{bg_id}")
def background_file(bg_id: str):
    for candidate in BACKGROUNDS.glob(f"{bg_id}.*"):
        return FileResponse(candidate)
    raise HTTPException(404, "Background not found")


def _transcode_to_mp4(source: Path, target: Path, quality: str = "20") -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                quality,
                "-pix_fmt",
                "yuv420p",
                str(target),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return True
    except Exception:
        return False


@app.post("/api/upload-video")
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix or ".bin"
    raw_id = f"video_raw_{uuid.uuid4().hex}{ext}"
    raw_target = UPLOADS / raw_id
    with raw_target.open("wb") as f:
        f.write(await file.read())

    cap = cv2.VideoCapture(str(raw_target))
    readable = cap.isOpened() and int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) > 0
    cap.release()

    if readable:
        return {"video_id": raw_id}

    converted_id = f"video_{uuid.uuid4().hex}.mp4"
    converted_target = UPLOADS / converted_id
    if _transcode_to_mp4(raw_target, converted_target):
        raw_target.unlink(missing_ok=True)
        return {"video_id": converted_id}

    raise HTTPException(400, "Video could not be read. Try MP4/H264 or install ffmpeg for auto-convert.")


@app.post("/api/upload-background")
async def upload_background(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix or ".png"
    file_id = f"uploaded_{uuid.uuid4().hex}{ext}"
    target = BACKGROUNDS / file_id
    with target.open("wb") as f:
        f.write(await file.read())
    return {"background_id": Path(file_id).stem}


@app.post("/api/preview-mask")
def preview_mask(video_id: str = Form(...), points: str = Form(...), labels: str = Form(...), time_s: float = Form(0.0)):
    source = UPLOADS / video_id
    if not source.exists():
        raise HTTPException(404, "Video not found")

    pts = [tuple(p) for p in __import__("json").loads(points)]
    lbs = [int(x) for x in __import__("json").loads(labels)]

    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(time_s * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(400, "Could not read preview frame")

    seg = _new_segmenter()
    mask = seg.get_mask(frame, pts, lbs)
    overlay = frame.copy()
    overlay[mask > 0] = (0, 200, 255)
    blended = cv2.addWeighted(frame, 0.45, overlay, 0.55, 0)
    _, png = cv2.imencode(".png", blended)
    return {
        "overlay": base64.b64encode(png.tobytes()).decode("utf-8"),
        "engine": "sam2" if seg.using_sam2 else "opencv-fallback",
    }


def _resolve_background(bg_id: str) -> Path:
    for candidate in BACKGROUNDS.glob(f"{bg_id}.*"):
        return candidate
    raise FileNotFoundError(bg_id)


def _process_job(job_id: str, req: ProcessRequest):
    try:
        source = UPLOADS / req.video_id
        background_path = _resolve_background(req.background_id)

        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            raise RuntimeError("Unable to open video for processing")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if req.disable_trim:
            start_frame = 0
            end_frame = frame_count
        else:
            start_frame = int(max(0, req.trim_start * fps))
            end_frame = int((req.trim_end * fps) if req.trim_end is not None else frame_count)
            end_frame = min(frame_count, max(start_frame + 1, end_frame))

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        temp_output = OUTPUTS / f"{job_id}_temp.mp4"
        final_output = OUTPUTS / f"{job_id}.mp4"
        writer = cv2.VideoWriter(str(temp_output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))

        bg_img = cv2.imread(str(background_path))
        if bg_img is None:
            raise RuntimeError("Background image could not be loaded")
        bg_img = cv2.resize(bg_img, (width, height))

        points = [tuple(p) for p in req.points]
        labels = [int(v) for v in req.labels]
        segmenter = _new_segmenter()

        processed = 0
        total = max(1, end_frame - start_frame)
        while cap.isOpened() and (start_frame + processed) < end_frame:
            ok, frame = cap.read()
            if not ok:
                break
            mask = segmenter.get_mask(frame, points, labels)
            mask3 = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) / 255.0
            composed = (frame * mask3 + bg_img * (1.0 - mask3)).astype(np.uint8)
            writer.write(composed)

            processed += 1
            jobs[job_id]["progress"] = int((processed / total) * 100)

        cap.release()
        writer.release()

        if processed == 0:
            raise RuntimeError("No frames were rendered. Check trim range.")

        transcoded = _transcode_to_mp4(temp_output, final_output, quality="18")
        if transcoded:
            temp_output.unlink(missing_ok=True)
        else:
            temp_output.rename(final_output)

        entry = {
            "id": job_id,
            "file": final_output.name,
            "url": f"/api/download/{job_id}",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        library.insert(0, entry)
        jobs[job_id].update({"status": "done", "progress": 100, "result": entry})
    except Exception as exc:
        jobs[job_id].update({"status": "error", "error": str(exc)})


@app.post("/api/process")
def process_video(req: ProcessRequest):
    if not (UPLOADS / req.video_id).exists():
        raise HTTPException(404, "Video missing")
    if not req.points or not req.labels or len(req.points) != len(req.labels):
        raise HTTPException(400, "You must add at least one mask point")
    job_id = f"job_{uuid.uuid4().hex}"
    jobs[job_id] = {"status": "processing", "progress": 0}
    threading.Thread(target=_process_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


@app.get("/api/library")
def get_library():
    return library


@app.delete("/api/library/{job_id}")
def delete_library_item(job_id: str):
    target = OUTPUTS / f"{job_id}.mp4"
    if target.exists():
        target.unlink(missing_ok=True)
    idx = next((i for i, x in enumerate(library) if x["id"] == job_id), None)
    if idx is not None:
        library.pop(idx)
    return {"ok": True}


@app.get("/api/download/{job_id}")
def download(job_id: str):
    target = OUTPUTS / f"{job_id}.mp4"
    if not target.exists():
        raise HTTPException(404, "Output not found")
    return FileResponse(target, media_type="video/mp4", filename=f"magic-mask-pro-{job_id}.mp4")
