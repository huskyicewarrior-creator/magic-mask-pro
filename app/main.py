from __future__ import annotations

import base64
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.sam2_engine import Sam2Segmenter, SegmentationConfig, ensure_sam2_runtime

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
BACKGROUNDS = DATA / "backgrounds"
OUTPUTS = DATA / "outputs"

for folder in (UPLOADS, BACKGROUNDS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)

PRESET_BACKGROUNDS = {
    "greenscreen": (0, 255, 0),
    "bluescreen": (255, 0, 0),
}
for bg_name, bgr in PRESET_BACKGROUNDS.items():
    target = BACKGROUNDS / f"{bg_name}.png"
    if not target.exists():
        cv2.imwrite(str(target), np.full((1080, 1920, 3), bgr, dtype=np.uint8))


@dataclass
class Clip:
    clip_id: str
    video_id: str
    in_point: float = 0.0
    out_point: Optional[float] = None
    position: int = 0


@dataclass
class EditorProject:
    project_id: str
    name: str
    clips: List[Clip] = field(default_factory=list)


class ClipCreateRequest(BaseModel):
    project_id: str
    video_id: str
    in_point: float = 0.0
    out_point: Optional[float] = None


class ClipUpdateRequest(BaseModel):
    in_point: float = 0.0
    out_point: Optional[float] = None
    position: int = 0


class TimelineOrderRequest(BaseModel):
    clip_ids: List[str]


class MaskPoint(BaseModel):
    x: int
    y: int


class MaskConfigRequest(BaseModel):
    points_add: List[MaskPoint] = Field(default_factory=list)
    points_remove: List[MaskPoint] = Field(default_factory=list)
    dilation_px: int = 5
    feather_px: int = 3


class ExportRequest(BaseModel):
    project_id: str
    background_id: str
    video_id: str
    trim_start: float = 0.0
    trim_end: Optional[float] = None
    disable_trim: bool = False
    mask: MaskConfigRequest


app = FastAPI(title="Magic Mask Pro Studio")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/web", StaticFiles(directory=ROOT / "web", html=True), name="web")

jobs: Dict[str, Dict] = {}
library: List[Dict] = []
projects: Dict[str, EditorProject] = {}


@app.get("/")
def home():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "name": "Magic Mask Pro Studio"}


@app.post("/api/install/sam2")
def install_sam2():
    report = ensure_sam2_runtime(ROOT)
    return report


@app.post("/api/projects")
def create_project(name: str = Form("Untitled Project")):
    project_id = f"proj_{uuid.uuid4().hex[:10]}"
    projects[project_id] = EditorProject(project_id=project_id, name=name)
    return {"project_id": project_id, "name": name}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    project = projects.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "project_id": project.project_id,
        "name": project.name,
        "clips": [clip.__dict__ for clip in sorted(project.clips, key=lambda c: c.position)],
    }


@app.post("/api/clips")
def add_clip(req: ClipCreateRequest):
    project = projects.get(req.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    clip = Clip(
        clip_id=f"clip_{uuid.uuid4().hex[:10]}",
        video_id=req.video_id,
        in_point=max(0.0, req.in_point),
        out_point=req.out_point,
        position=len(project.clips),
    )
    project.clips.append(clip)
    return clip.__dict__


@app.patch("/api/clips/{clip_id}")
def update_clip(clip_id: str, req: ClipUpdateRequest):
    for project in projects.values():
        for clip in project.clips:
            if clip.clip_id == clip_id:
                clip.in_point = max(0.0, req.in_point)
                clip.out_point = req.out_point
                clip.position = max(0, req.position)
                return clip.__dict__
    raise HTTPException(404, "Clip not found")


@app.post("/api/projects/{project_id}/timeline-order")
def reorder_timeline(project_id: str, req: TimelineOrderRequest):
    project = projects.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    clip_map = {clip.clip_id: clip for clip in project.clips}
    for idx, clip_id in enumerate(req.clip_ids):
        if clip_id in clip_map:
            clip_map[clip_id].position = idx
    return {"ok": True}


@app.get("/api/backgrounds")
def get_backgrounds():
    return [
        {
            "id": p.stem,
            "name": p.name,
            "url": f"/api/background-file/{p.stem}",
        }
        for p in sorted(BACKGROUNDS.glob("*.*"))
    ]


@app.get("/api/background-file/{bg_id}")
def background_file(bg_id: str):
    for candidate in BACKGROUNDS.glob(f"{bg_id}.*"):
        return FileResponse(candidate)
    raise HTTPException(404, "Background not found")


@app.post("/api/upload-video")
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix or ".mp4"
    target = UPLOADS / f"video_{uuid.uuid4().hex}{ext}"
    with target.open("wb") as f:
        f.write(await file.read())

    cap = cv2.VideoCapture(str(target))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    duration = frame_count / fps if fps > 0 else 0
    cap.release()

    if frame_count <= 0:
        raise HTTPException(400, "Could not parse video file")

    return {"video_id": target.name, "duration": round(duration, 2)}


@app.post("/api/upload-background")
async def upload_background(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix or ".png"
    target = BACKGROUNDS / f"uploaded_{uuid.uuid4().hex}{ext}"
    with target.open("wb") as f:
        f.write(await file.read())
    return {"background_id": target.stem}


def _resolve_background(bg_id: str, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    for candidate in BACKGROUNDS.glob(f"{bg_id}.*"):
        bg = cv2.imread(str(candidate))
        if bg is None:
            break
        return cv2.resize(bg, (w, h), interpolation=cv2.INTER_AREA)
    if bg_id == "greenscreen":
        return np.full((h, w, 3), (0, 255, 0), dtype=np.uint8)
    if bg_id == "bluescreen":
        return np.full((h, w, 3), (255, 0, 0), dtype=np.uint8)
    raise HTTPException(404, "Background not found")


def _mask_with_controls(frame: np.ndarray, mask: np.ndarray, config: MaskConfigRequest) -> np.ndarray:
    controlled = mask.astype(np.uint8)
    if config.dilation_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.dilation_px * 2 + 1, config.dilation_px * 2 + 1))
        controlled = cv2.dilate(controlled, k)
    if config.feather_px > 0:
        blur = config.feather_px * 2 + 1
        controlled = cv2.GaussianBlur(controlled.astype(np.float32), (blur, blur), 0)

    for point in config.points_add:
        cv2.circle(controlled, (point.x, point.y), 24, 255, -1)
    for point in config.points_remove:
        cv2.circle(controlled, (point.x, point.y), 24, 0, -1)

    return np.clip(controlled, 0, 255).astype(np.uint8)


@app.post("/api/preview-mask")
async def preview_mask(
    video_id: str = Form(...),
    point_x: int = Form(...),
    point_y: int = Form(...),
    time_s: float = Form(0.0),
    config_json: str = Form('{"points_add": [], "points_remove": [], "dilation_px": 5, "feather_px": 3}'),
):
    video_path = UPLOADS / video_id
    if not video_path.exists():
        raise HTTPException(404, "Video not found")

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0.0, time_s) * fps))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(400, "Could not read frame")

    seg = Sam2Segmenter(SegmentationConfig())
    mask = seg.segment(frame, point_x, point_y)

    config = MaskConfigRequest.model_validate_json(config_json)
    controlled = _mask_with_controls(frame, mask, config)

    overlay = frame.copy()
    overlay[controlled > 120] = (20, 220, 20)
    preview = cv2.addWeighted(frame, 0.55, overlay, 0.45, 0)

    _, png = cv2.imencode(".png", preview)
    return {"overlay": base64.b64encode(png.tobytes()).decode("utf-8")}


@app.post("/api/export")
def export_video(req: ExportRequest):
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    jobs[job_id] = {"status": "queued", "progress": 0, "error": None}

    def _run():
        try:
            jobs[job_id]["status"] = "running"
            video_path = UPLOADS / req.video_id
            if not video_path.exists():
                raise RuntimeError("Video missing")

            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                raise RuntimeError("Bad video")

            start_frame = 0 if req.disable_trim else int(max(0.0, req.trim_start) * fps)
            end_frame = total if req.disable_trim else int((req.trim_end or (total / fps)) * fps)
            end_frame = max(start_frame + 1, min(end_frame, total))

            out_path = OUTPUTS / f"export_{uuid.uuid4().hex}.mp4"
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))

            seg = Sam2Segmenter(SegmentationConfig())
            for frame_idx in range(total):
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_idx < start_frame:
                    continue
                if frame_idx >= end_frame:
                    break

                first_point = req.mask.points_add[0] if req.mask.points_add else MaskPoint(x=width // 2, y=height // 2)
                raw_mask = seg.segment(frame, first_point.x, first_point.y)
                mask = _mask_with_controls(frame, raw_mask, req.mask)
                bg = _resolve_background(req.background_id, (height, width))

                alpha = (mask.astype(np.float32) / 255.0)[..., None]
                result = (frame * alpha + bg * (1 - alpha)).astype(np.uint8)
                writer.write(result)

                processed = frame_idx - start_frame + 1
                span = max(1, end_frame - start_frame)
                jobs[job_id]["progress"] = int((processed / span) * 100)

            cap.release()
            writer.release()

            library.append(
                {
                    "id": out_path.stem,
                    "url": f"/api/output/{out_path.name}",
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
            )
            jobs[job_id]["status"] = "done"
            jobs[job_id]["progress"] = 100
        except Exception as exc:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/library")
def get_library():
    return list(reversed(library))




@app.get("/api/media/{filename}")
def get_media(filename: str):
    target = UPLOADS / filename
    if not target.exists():
        raise HTTPException(404, "Media not found")
    return FileResponse(target)


@app.get("/api/output/{filename}")
def get_output(filename: str):
    target = OUTPUTS / filename
    if not target.exists():
        raise HTTPException(404, "Output not found")
    return FileResponse(target)


@app.post("/api/one-click/check")
def one_click_check():
    return {
        "python": shutil.which("python") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "sam2_checkpoint": (ROOT / "sam2_checkpoint.pt").exists(),
        "windows_installer": (ROOT / "installer" / "install_windows.ps1").exists(),
    }
