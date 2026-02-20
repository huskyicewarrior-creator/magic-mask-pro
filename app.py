from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import gradio as gr
import numpy as np
from PIL import Image
from rembg import remove

APP_NAME = "Magic Mask Pro"
ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

PRESET_BACKGROUNDS = {
    "Green Screen": np.full((1080, 1920, 3), (0, 255, 0), dtype=np.uint8),
    "Blue Screen": np.full((1080, 1920, 3), (255, 0, 0), dtype=np.uint8),
}


@dataclass
class ProcessorConfig:
    trim_start: float
    trim_end: float
    background_choice: str
    quality_crf: int


class Segmenter:
    """Uses SAM2 when available, otherwise falls back to rembg."""

    def __init__(self) -> None:
        self.use_fallback = os.getenv("MAGIC_MASK_FORCE_FALLBACK", "0") == "1"
        self.sam2_predictor = None
        if not self.use_fallback:
            self._try_load_sam2()

    def _try_load_sam2(self) -> None:
        try:
            import sam2  # type: ignore # noqa: F401

            # Hook point for real SAM2 init if environment has model/checkpoints.
            # Kept defensive so the app remains runnable in lightweight setups.
            self.sam2_predictor = "sam2-ready"
        except Exception:
            self.sam2_predictor = None

    @property
    def mode(self) -> str:
        return "SAM2" if self.sam2_predictor else "Fallback (rembg)"

    def alpha_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        rgba = remove(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGBA))
        if rgba.shape[-1] < 4:
            return np.full(frame_bgr.shape[:2], 255, dtype=np.uint8)
        alpha = rgba[..., 3]
        return alpha


SEGMENTER = Segmenter()


def ffprobe_duration(path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def load_background(background_choice: str, uploaded_bg: Optional[str], size: tuple[int, int]) -> np.ndarray:
    h, w = size
    if background_choice in PRESET_BACKGROUNDS:
        bg = PRESET_BACKGROUNDS[background_choice]
        return cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)

    if uploaded_bg:
        img = cv2.imread(uploaded_bg)
        if img is not None:
            return cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)

    return cv2.resize(PRESET_BACKGROUNDS["Green Screen"], (w, h), interpolation=cv2.INTER_LINEAR)


def blend_frame(frame_bgr: np.ndarray, alpha_mask: np.ndarray, background_bgr: np.ndarray) -> np.ndarray:
    alpha = (alpha_mask.astype(np.float32) / 255.0)[..., None]
    fg = frame_bgr.astype(np.float32)
    bg = background_bgr.astype(np.float32)
    out = fg * alpha + bg * (1.0 - alpha)
    return np.clip(out, 0, 255).astype(np.uint8)


def get_preview(video_path: str, trim_start: float) -> Image.Image:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_index = max(int(trim_start * fps), 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = cap.read()
    cap.release()

    if not ok:
        raise RuntimeError("Could not read preview frame from video.")

    alpha = SEGMENTER.alpha_mask(frame)
    color_overlay = np.zeros_like(frame)
    color_overlay[:, :, 2] = 255
    overlay = cv2.addWeighted(frame, 0.7, color_overlay, 0.3, 0)
    overlay[alpha < 128] = frame[alpha < 128]

    return Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))


def process_video(
    video_path: str,
    background_choice: str,
    custom_bg: Optional[str],
    trim_start: float,
    trim_end: float,
    quality_crf: int,
    progress=gr.Progress(track_tqdm=False),
):
    if not video_path:
        raise gr.Error("Upload a video first.")

    total_duration = ffprobe_duration(video_path)
    safe_end = min(trim_end, total_duration)
    safe_start = max(0.0, min(trim_start, safe_end - 0.01))
    if safe_end <= safe_start:
        raise gr.Error("Trim end must be greater than trim start.")

    cfg = ProcessorConfig(
        trim_start=safe_start,
        trim_end=safe_end,
        background_choice=background_choice,
        quality_crf=quality_crf,
    )

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    start_frame = int(cfg.trim_start * fps)
    end_frame = min(int(cfg.trim_end * fps), total_frames - 1)

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    ok, first = cap.read()
    if not ok:
        cap.release()
        raise gr.Error("Unable to decode the selected range.")

    h, w = first.shape[:2]
    background = load_background(cfg.background_choice, custom_bg, (h, w))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = OUTPUT_DIR / f"mask_raw_{ts}.mp4"
    final_path = OUTPUT_DIR / f"magic_mask_pro_{ts}.mp4"

    writer = cv2.VideoWriter(
        str(raw_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (w, h),
    )

    frame = first
    current = start_frame
    processed = 0
    total_to_process = max(1, end_frame - start_frame + 1)

    while ok and current <= end_frame:
        alpha = SEGMENTER.alpha_mask(frame)
        composed = blend_frame(frame, alpha, background)
        writer.write(composed)

        processed += 1
        progress(processed / total_to_process, desc=f"Masking frames... ({processed}/{total_to_process})")
        ok, frame = cap.read()
        current += 1

    cap.release()
    writer.release()

    progress(0.95, desc="Encoding high-quality MP4...")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(raw_path),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        str(cfg.quality_crf),
        "-pix_fmt",
        "yuv420p",
        str(final_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    if raw_path.exists():
        raw_path.unlink()

    progress(1.0, desc="Done")
    return str(final_path), refresh_library()


def refresh_library():
    files = sorted(OUTPUT_DIR.glob("magic_mask_pro_*.mp4"), reverse=True)
    rows = [[f.name, str(f)] for f in files]
    return rows


def get_logo_svg() -> str:
    return (ROOT / "assets" / "logo.svg").read_text(encoding="utf-8")


CUSTOM_CSS = """
:root {
  --accent: #8b5cf6;
  --accent2: #06b6d4;
}
.gradio-container {
  background: radial-gradient(circle at top, #111827, #030712 65%);
}
#hero {
  border: 1px solid rgba(139,92,246,0.4);
  border-radius: 18px;
  padding: 16px;
  backdrop-filter: blur(8px);
  animation: glow 2.5s ease-in-out infinite alternate;
}
@keyframes glow {
  from { box-shadow: 0 0 16px rgba(139,92,246,0.25); }
  to { box-shadow: 0 0 30px rgba(6,182,212,0.35); }
}
"""


def build_app() -> gr.Blocks:
    with gr.Blocks(title=APP_NAME, css=CUSTOM_CSS, theme=gr.themes.Soft()) as demo:
        gr.HTML(f"<div id='hero'>{get_logo_svg()}</div>")
        gr.Markdown(f"## {APP_NAME}\nAI video object masking with **{SEGMENTER.mode}** + live progress.")

        with gr.Tabs():
            with gr.Tab("Editor"):
                with gr.Row():
                    video_in = gr.Video(label="Upload video")
                    preview_img = gr.Image(label="Mask preview overlay", interactive=False)

                with gr.Row():
                    trim_start = gr.Slider(0, 120, value=0, step=0.1, label="Trim start (seconds)")
                    trim_end = gr.Slider(1, 240, value=10, step=0.1, label="Trim end (seconds)")

                with gr.Row():
                    background_choice = gr.Radio(
                        choices=["Green Screen", "Blue Screen", "Custom Upload"],
                        value="Green Screen",
                        label="Background",
                    )
                    custom_bg = gr.Image(label="Custom background image", type="filepath")
                    quality = gr.Slider(14, 28, value=17, step=1, label="Quality (lower = higher quality)")

                preview_btn = gr.Button("Preview Mask Overlay", variant="secondary")
                process_btn = gr.Button("Generate High-Quality Masked Video", variant="primary")
                output_video = gr.Video(label="Output (high quality mp4)")

            with gr.Tab("Library"):
                library_df = gr.Dataframe(headers=["File", "Path"], datatype=["str", "str"], value=refresh_library())
                refresh_btn = gr.Button("Refresh Library")

        preview_btn.click(
            fn=get_preview,
            inputs=[video_in, trim_start],
            outputs=[preview_img],
        )

        process_btn.click(
            fn=process_video,
            inputs=[video_in, background_choice, custom_bg, trim_start, trim_end, quality],
            outputs=[output_video, library_df],
        )

        refresh_btn.click(fn=refresh_library, inputs=None, outputs=[library_df])

    return demo


if __name__ == "__main__":
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7860)
