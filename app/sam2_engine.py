from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class SegmentationConfig:
    min_area: int = 40


class Sam2Segmenter:
    """Uses SAM2 if available, else falls back to GrabCut-like segmentation."""

    def __init__(self, config: SegmentationConfig):
        self.config = config
        self._sam2_predictor = self._try_load_sam2()

    def _try_load_sam2(self):
        try:
            from sam2.build_sam import build_sam2  # type: ignore
            from sam2.sam2_image_predictor import SAM2ImagePredictor  # type: ignore

            checkpoint = Path("sam2_checkpoint.pt")
            config_path = "sam2_hiera_l.yaml"
            if checkpoint.exists():
                model = build_sam2(config_path, str(checkpoint))
                return SAM2ImagePredictor(model)
        except Exception:
            return None
        return None

    def _fallback_segment(self, frame: np.ndarray, x: int, y: int) -> np.ndarray:
        h, w = frame.shape[:2]
        x = int(np.clip(x, 0, w - 1))
        y = int(np.clip(y, 0, h - 1))

        mask = np.zeros((h, w), np.uint8)
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)

        size = max(32, min(h, w) // 8)
        rect = (max(0, x - size), max(0, y - size), min(size * 2, w - 1), min(size * 2, h - 1))

        cv2.grabCut(frame, mask, rect, bgd_model, fgd_model, 3, cv2.GC_INIT_WITH_RECT)
        out = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats((out > 0).astype(np.uint8), connectivity=8)
        best = 0
        best_area = 0
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area > best_area and area >= self.config.min_area:
                best_area = area
                best = i
        if best > 0:
            return np.where(labels == best, 255, 0).astype(np.uint8)
        return out

    def segment(self, frame: np.ndarray, x: int, y: int) -> np.ndarray:
        if self._sam2_predictor is not None:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self._sam2_predictor.set_image(rgb)
                masks, _, _ = self._sam2_predictor.predict(
                    point_coords=np.array([[x, y]]), point_labels=np.array([1]), multimask_output=False
                )
                return (masks[0].astype(np.uint8) * 255)
            except Exception:
                pass
        return self._fallback_segment(frame, x, y)


def ensure_sam2_runtime(root: Path) -> dict:
    script = root / "installer" / "install_sam2.py"
    if not script.exists():
        return {"ok": False, "message": "install_sam2.py missing"}

    python_exe = shutil.which("python")
    if not python_exe:
        return {"ok": False, "message": "python not found"}

    try:
        result = subprocess.run([python_exe, str(script)], cwd=str(root), capture_output=True, text=True, check=False)
        return {
            "ok": result.returncode == 0,
            "code": result.returncode,
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-1000:],
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
